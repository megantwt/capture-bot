"""
Weekly loop: Sunday roundup, your goal for the week, a plan, and a midweek nudge.
What the messages say is set in prompts/weekly.md and prompts/goals.md, which are
read fresh every time, so you can edit them without restarting the bot.
"""

from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import anthropic
import gspread

from workflows import crm

TZ = ZoneInfo(os.environ.get("TIMEZONE", "America/New_York"))
MODEL = os.environ.get("WEEKLY_MODEL") or os.environ.get("CLAUDE_MODEL", "claude-sonnet-5-5")
PROMPTS = Path(__file__).parent / "prompts"
WEEKLY_HEADERS = ["Week of", "Goal", "Plan", "Outcome"]

claude = anthropic.Anthropic()


def _read(name: str) -> str:
    path = PROMPTS / name
    return path.read_text() if path.exists() else ""


def weekly_ws():
    """The Weekly tab. Created automatically the first time it's needed."""
    sh = crm.sheet()
    try:
        return sh.worksheet("Weekly")
    except gspread.WorksheetNotFound:
        ws = sh.add_worksheet(title="Weekly", rows=300, cols=len(WEEKLY_HEADERS))
        ws.update(range_name="A1:D1", values=[WEEKLY_HEADERS])
        ws.freeze(rows=1)
        return ws


def _records(ws) -> list[dict]:
    rows = ws.get_all_values()
    if not rows:
        return []
    head = [h.strip() for h in rows[0]]
    return [dict(zip(head, r)) for r in rows[1:] if any(c.strip() for c in r)]


def _context() -> tuple[str, list[dict]]:
    sh = crm.sheet()
    contacts = [{k: v for k, v in r.items() if k and v.strip()}
                for r in _records(sh.worksheet("Contacts")) if r.get("Name", "").strip()]
    interactions = [{k: v for k, v in r.items() if k and v.strip() and k != "Raw transcript"}
                    for r in _records(sh.worksheet("Interactions"))][-150:]
    weeks = _records(weekly_ws())[-8:]
    today = datetime.now(TZ).strftime("%A %d %B %Y")
    text = (f"Today is {today}.\n\n"
            f"Contacts:\n{json.dumps(contacts, ensure_ascii=False)}\n\n"
            f"Interactions (oldest first):\n{json.dumps(interactions, ensure_ascii=False)}\n\n"
            f"Past weekly goals and plans (oldest first):\n{json.dumps(weeks, ensure_ascii=False)}")
    return text, weeks


def _ask(task: str, context: str, max_tokens: int = 2500) -> str:
    resp = claude.messages.create(
        model=MODEL,
        max_tokens=max_tokens,
        system=_read("weekly.md") + "\n\n" + _read("goals.md"),
        messages=[{"role": "user", "content": f"{context}\n\n---\n\n{task}"}],
    )
    return "".join(b.text for b in resp.content if b.type == "text").strip()


def roundup() -> str:
    context, weeks = _context()
    text = _ask("Do the ROUNDUP task. After it, on its own final line, write 'OUTCOME: ' followed by "
                "one sentence on how last week went against last week's goal, or 'OUTCOME: none' "
                "if there was no goal last week.", context)

    lines = text.splitlines()
    outcome = ""
    for i in range(len(lines) - 1, -1, -1):
        if lines[i].strip().upper().startswith("OUTCOME:"):
            outcome = lines[i].split(":", 1)[1].strip()
            del lines[i]
            break

    # Record how last week went, on last week's row, if it isn't filled in yet.
    if outcome and outcome.lower() != "none" and weeks and not weeks[-1].get("Outcome", "").strip():
        ws = weekly_ws()
        last_row = len(ws.col_values(1))
        if last_row >= 2:
            ws.update_cell(last_row, WEEKLY_HEADERS.index("Outcome") + 1, outcome)
    return "\n".join(lines).strip()


def plan(goal: str, week_of: str) -> str:
    context, _ = _context()
    text = _ask(f'Do the PLAN task. Megan\'s connection goal for this week, in her words:\n"""{goal}"""',
                context)
    weekly_ws().append_row([week_of, goal, text, ""], value_input_option="RAW")
    return text


def nudge() -> str:
    context, _ = _context()
    return _ask("Do the NUDGE task.", context, max_tokens=800)


def ask(question: str, earlier: str = "", open_drafts: str = "", recent: str = "") -> str:
    """Answer a request from Megan: draft an email, answer a question about her network, etc."""
    context, _ = _context()
    task = "Do the ASK task.\n"
    if open_drafts:
        task += f"\nUnsaved drafts (treat as known information too):\n{open_drafts}\n"
    if recent:
        task += f"\nRecent chat (for context, e.g. who 'him' or 'her' is):\n{recent}\n"
    if earlier:
        task += f'\nYour earlier reply, which Megan is responding to:\n"""{earlier}"""\n'
    task += f'\nMegan\'s message:\n"""{question}"""'
    return _ask(task, context, max_tokens=1500)

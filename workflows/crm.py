"""
CRM workflow: logs a networking interaction to the Google Sheet.

Every workflow file defines the same things:
  KEY, NAME, HINT          names used in Discord
  TOOL                     the fields Claude must fill in
  instructions()           how to fill them (read from prompts/<KEY>.md)
  build_prompt(text)       the message Claude sees
  vocabulary()             words that help transcription (optional)
  preview(draft)           what you see before saving
  save(draft, transcript)  where it goes

Columns are found by their header text, so you can reorder columns in the
sheet freely. If you rename a header, update CONTACT_HEADERS below to match.
"""

from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path

import gspread

KEY = "crm"
NAME = "CRM"
HINT = "Start with the person's name, then what happened and any next step."

# Posted by the !template command. A checklist of what to say in a note.
TEMPLATE = """**What to cover in a note**
New person:
- Full name and organisation ("Eugene Tan from FAR AI")
- Their role or team
- How you met (event, intro from someone, HLS, LinkedIn)
- What you talked about
- How they could help you (intro, referral, advice)
- What you could offer them
- Next step and when ("send my paper by Friday")
Someone already in the CRM:
- Name and org ("Eugene from FAR AI")
- What happened (coffee, call, LinkedIn message, reply)
- Anything new about them, and the next step with a date
Just a fact, not a conversation ("heard Eugene moved to DeepMind"): say so, and it's saved as a Note."""

# Fields worth having for a new contact, flagged on the draft when the note didn't cover them.
NEW_CONTACT_CHECKLIST = {"org": "Org", "role": "Role", "tags": "Tags", "context": "How you met",
                         "how_help": "How they can help", "what_offer": "What you can offer",
                         "next_step": "Next step", "due": "When"}

# Edit this list to change the tags. Keep it in sync with the Tags dropdown in the sheet.
TAGS = ["AI Safety", "Law", "Big Tech", "Compute", "Strategy", "Deals", "Finance"]

# "Note" is for information that isn't a contact (news about them, a reminder). It goes in the
# Notes column and Latest update, and does not reset Last contacted.
CHANNELS = ["Coffee", "Call", "LinkedIn", "Email", "Event", "Other", "Note"]

# Draft field -> header text in the Contacts tab.
CONTACT_HEADERS = {
    "name": "Name",
    "org": "Org",
    "role": "Role",
    "tags": "Tags",
    "context": "Context",
    "warmth": "Warmth",
    "cadence": "Cadence (days)",
    "how_help": "How they can help me",
    "what_offer": "What I can offer",
    "next_step": "Next step",
    "next_step_due": "Next step due",
    "latest_update": "Latest update",
    "notes": "Notes",
}
# Columns the bot fills if they exist in the sheet, and skips if they don't.
OPTIONAL_COLUMNS = {"latest_update", "notes"}
# Fields shown in the preview and written only when the note mentions them.
OPTIONAL_FIELDS = {"org": "Org", "role": "Role", "context": "Context", "warmth": "Warmth",
                   "how_help": "Can help with", "what_offer": "I can offer"}

TOOL = {
    "name": "log_interaction",
    "description": "Record one networking interaction for the CRM.",
    "input_schema": {
        "type": "object",
        "properties": {
            "person": {"type": "string"},
            "is_existing_contact": {"type": "boolean"},
            "date": {"type": "string", "description": "YYYY-MM-DD"},
            "channel": {"type": "string", "enum": CHANNELS},
            "summary": {"type": "string"},
            "next_step": {"type": "string"},
            "due": {"type": "string", "description": "YYYY-MM-DD or empty"},
            "org": {"type": "string", "description": "Organisation only, e.g. 'Anthropic'"},
            "role": {"type": "string", "description": "Role or team only, e.g. 'Policy lead'"},
            "tags": {"type": "array", "items": {"type": "string", "enum": TAGS},
                     "description": "Which areas this person works in. Empty list if unclear."},
            "context": {"type": "string"},
            "warmth": {"type": "string", "enum": ["Hot", "Warm", "Dormant", ""]},
            "how_help": {"type": "string"},
            "what_offer": {"type": "string"},
            "unclear": {"type": "string"},
        },
        "required": ["person", "is_existing_contact", "date", "channel", "summary", "next_step", "due",
                     "org", "role", "tags", "context", "warmth", "how_help", "what_offer", "unclear"],
    },
}

_sheet = None


def sheet():
    global _sheet
    if _sheet is None:
        creds = os.environ.get("GOOGLE_CREDENTIALS_FILE", "service_account.json")
        _sheet = gspread.service_account(filename=creds).open_by_key(os.environ["GOOGLE_SHEET_ID"])
    return _sheet


def _columns(ws) -> dict[str, int]:
    """Map each draft field to its 1-based column number, using the header row."""
    headers = [h.strip().lower() for h in ws.row_values(1)]
    cols = {}
    for field, header in CONTACT_HEADERS.items():
        if header.lower() not in headers:
            if field in OPTIONAL_COLUMNS:
                continue
            raise RuntimeError(f"The Contacts tab has no column called '{header}'. "
                               "Add it, or update CONTACT_HEADERS in workflows/crm.py.")
        cols[field] = headers.index(header.lower()) + 1
    return cols


def contact_directory() -> list[dict]:
    """Name, org and role of every contact, so Claude can tell people with the same first name apart."""
    ws = sheet().worksheet("Contacts")
    col = _columns(ws)
    out = []
    for row in ws.get_all_values()[1:]:
        get = lambda f: row[col[f] - 1].strip() if col[f] - 1 < len(row) else ""
        if get("name"):
            out.append({k: v for k, v in {"name": get("name"), "org": get("org"), "role": get("role")}.items() if v})
    return out


def contact_names() -> list[str]:
    ws = sheet().worksheet("Contacts")
    col = _columns(ws)["name"]
    return [n.strip() for n in ws.col_values(col)[1:] if n.strip()]


def instructions() -> str:
    # Read on every note, so edits to the prompt files apply without a restart.
    prompts = Path(__file__).parent.parent / "prompts"
    text = (prompts / f"{KEY}.md").read_text()
    goals = prompts / "goals.md"
    return text + ("\n\n" + goals.read_text() if goals.exists() else "")


def vocabulary() -> list[str]:
    try:
        return contact_names()
    except Exception:
        return []


def build_prompt(text: str) -> str:
    people = contact_directory()
    return (f"Existing contacts: {json.dumps(people, ensure_ascii=False) if people else 'none yet'}\n"
            f"Allowed tags: {', '.join(TAGS)}\n\n"
            f'Note about a networking interaction:\n"""{text}"""')


def preview(d: dict) -> str:
    who = d["person"] + ("  (existing contact)" if d["is_existing_contact"] else "  (new contact)")
    when = f"📅 {d['date']} · {d['channel']}"
    if d["channel"] == "Note":
        when += " (doesn't count as contact)"
    lines = [f"👤 {who}", when, f"📝 {d['summary']}"]
    if d["next_step"]:
        lines.append(f"➡️ {d['next_step']}" + (f" (by {d['due']})" if d["due"] else ""))
    if d.get("tags"):
        lines.append("🏷 " + ", ".join(d["tags"]))
    lines += [f"· {label}: {d[k]}" for k, label in OPTIONAL_FIELDS.items() if d.get(k)]
    if d.get("unclear"):
        lines.append(f"\n⚠️ Check: {d['unclear']}")
    missing = _missing(d)
    if missing:
        lines.append(f"❓ Missing: {', '.join(missing)}. Reply to this draft to add them, or just Save.")
    return "\n".join(lines)


def _missing(d: dict) -> list[str]:
    if d["channel"] == "Note":
        return []
    if d["is_existing_contact"]:
        fields = {"next_step": "Next step"} if not d["next_step"] else {"due": "When"}
    else:
        fields = NEW_CONTACT_CHECKLIST
    return [label for key, label in fields.items() if not d.get(key)]


def _merge_tags(existing: str, new: list[str]) -> str:
    current = [t.strip() for t in existing.split(",") if t.strip()]
    for t in new:
        if t not in current:
            current.append(t)
    return ", ".join(current)


def _short_date(iso: str) -> str:
    try:
        return datetime.strptime(iso, "%Y-%m-%d").strftime("%d %b").lstrip("0")
    except ValueError:
        return iso


def save(d: dict, transcript: str) -> None:
    contacts = sheet().worksheet("Contacts")
    interactions = sheet().worksheet("Interactions")
    col = _columns(contacts)

    names = contacts.col_values(col["name"])
    match = next((n for n in names[1:] if n.strip().lower() == d["person"].strip().lower()), None)
    person = match or d["person"].strip()
    tags = d.get("tags") or []

    values: dict[str, object] = {}
    if match:
        row = names.index(match) + 1
        if d["next_step"]:
            values["next_step"] = d["next_step"]
            values["next_step_due"] = d["due"]
        if tags:
            values["tags"] = _merge_tags(contacts.cell(row, col["tags"]).value or "", tags)
    else:
        row = len(names) + 1
        values = {"name": person, "warmth": "Warm",
                  "cadence": int(os.environ.get("DEFAULT_CADENCE_DAYS", "30")),
                  "next_step": d["next_step"], "next_step_due": d["due"], "tags": ", ".join(tags)}
    values.update({k: d[k] for k in OPTIONAL_FIELDS if d.get(k)})
    stamp = f"{_short_date(d['date'])}: {d['summary']}"
    values["latest_update"] = stamp
    is_note = d["channel"] == "Note"
    if is_note and "notes" in col:
        existing = contacts.cell(row, col["notes"]).value or "" if match else ""
        values["notes"] = f"{existing}\n{stamp}".strip()

    cells = [gspread.Cell(row, col[k], v) for k, v in values.items()
             if k in col and v not in (None, "")]
    if cells:
        contacts.update_cells(cells, value_input_option="USER_ENTERED")

    if is_note:
        return  # Notes don't go in the interaction log, so Last contacted is unchanged.
    irow = len(interactions.col_values(1)) + 1
    interactions.update(range_name=f"A{irow}:G{irow}", value_input_option="USER_ENTERED", values=[[
        d["date"], person, d["channel"], d["summary"], d["next_step"], d["due"], transcript]])

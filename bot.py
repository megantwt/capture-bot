"""
Capture bot (Discord).

Notes:   voice message or text -> transcript (on your laptop) -> draft (Claude)
         -> you confirm -> saved by the active workflow (workflows/).
Weekly:  Sunday roundup -> asks your goal -> plan; midweek nudge (weekly.py).
Catch-up: notes sent while the bot was off are drafted when it next starts.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import tempfile
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import anthropic
import discord
from discord.ext import tasks
from dotenv import load_dotenv

load_dotenv()

import store  # noqa: E402
import weekly  # noqa: E402
from transcribe import transcribe  # noqa: E402
from workflows import WORKFLOWS  # noqa: E402

DISCORD_TOKEN = os.environ["DISCORD_BOT_TOKEN"]
ALLOWED_USER_ID = int(os.environ.get("ALLOWED_USER_ID", "0"))
TZ = ZoneInfo(os.environ.get("TIMEZONE", "America/New_York"))
CLAUDE_MODEL = os.environ.get("CLAUDE_MODEL", "claude-sonnet-5-5")
DEFAULT_WORKFLOW = os.environ.get("DEFAULT_WORKFLOW", "crm")
PREFIX = "!"
MIN_NOTE_CHARS = 15
FOOTER = "\n\n*Save it? Or reply with a correction.*"
DAYS = {"mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4, "sat": 5, "sun": 6}

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("capture-bot")
claude = anthropic.Anthropic()

state = store.load()          # survives restarts
state.setdefault("handled", [])
runtime = {"workflow": DEFAULT_WORKFLOW}
drafts: dict[int, dict] = {}   # open drafts, keyed by the id of the bot's draft message
drafting: set[int] = set()     # ids of notes currently being transcribed or drafted


# ---------- schedule helpers ----------

def parse_when(value: str):
    """'sun 18:00' -> (6, 18, 0). 'off' or empty -> None."""
    value = (value or "").strip().lower()
    if not value or value == "off":
        return None
    day, hm = value.split()
    hour, minute = hm.split(":")
    return DAYS[day[:3]], int(hour), int(minute)


ROUNDUP_AT = parse_when(os.environ.get("ROUNDUP_TIME", "sun 18:00"))
NUDGE_AT = parse_when(os.environ.get("NUDGE_TIME", "wed 09:00"))


def last_occurrence(now: datetime, when) -> datetime:
    weekday, hour, minute = when
    cand = (now - timedelta(days=(now.weekday() - weekday) % 7)).replace(
        hour=hour, minute=minute, second=0, microsecond=0)
    return cand if cand <= now else cand - timedelta(days=7)


def ts(key: str) -> datetime:
    value = state.get(key)
    return datetime.fromisoformat(value) if value else datetime(2000, 1, 1, tzinfo=timezone.utc)


# ---------- state helpers ----------

def is_handled(mid: int) -> bool:
    return mid in state["handled"]


def mark_handled(*ids: int) -> None:
    for i in ids:
        if i not in state["handled"]:
            state["handled"].append(i)
    state["handled"] = state["handled"][-1000:]
    store.save(state)


def audio_of(message: discord.Message):
    return next((a for a in message.attachments
                 if (a.content_type or "").startswith("audio") or a.is_voice_message()), None)


def text_file_of(message: discord.Message):
    """A pasted text file, e.g. the message.txt Discord makes from a very long message."""
    return next((a for a in message.attachments
                 if (a.content_type or "").startswith("text")
                 or (a.filename or "").lower().endswith((".txt", ".md"))), None)


def is_note(message: discord.Message) -> bool:
    text = message.content.strip()
    return (audio_of(message) is not None or text_file_of(message) is not None
            or (len(text) >= MIN_NOTE_CHARS and not text.startswith(PREFIX)))


def in_flight(message: discord.Message) -> bool:
    return message.id in drafting or any(message.id in d["msg_ids"] for d in drafts.values())


async def message_text(message: discord.Message, hints: list[str]) -> str:
    audio = audio_of(message)
    if audio is None:
        text = message.content.strip()
        file = text_file_of(message)
        if file is not None:
            body = (await file.read()).decode("utf-8", errors="replace").strip()
            text = f"{text}\n\n{body}".strip()
        return text
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, audio.filename or "note.ogg")
        await audio.save(path)
        return await asyncio.to_thread(transcribe, path, hints)


async def send_long(channel, text: str, reference=None) -> None:
    chunks, cur = [], ""
    for line in text.split("\n"):
        while len(line) > 1900:
            chunks.append(line[:1900])
            line = line[1900:]
        if cur and len(cur) + len(line) + 1 > 1900:
            chunks.append(cur)
            cur = line
        else:
            cur = f"{cur}\n{line}" if cur else line
    if cur:
        chunks.append(cur)
    for i, chunk in enumerate(chunks):
        if i == 0 and reference is not None:
            await channel.send(chunk, reference=reference, mention_author=False)
        else:
            await channel.send(chunk)


# ---------- drafting notes ----------

def draft(workflow, text: str, previous: dict | None = None, correction: str | None = None,
          recent: str = "") -> dict:
    today = datetime.now(TZ).strftime("%Y-%m-%d (%A)")
    prompt = f"Today is {today}.\n\n" + workflow.build_prompt(text)
    if recent:
        prompt += ("\n\nRecent chat, only for working out who 'he', 'she' or 'they' refers to:\n"
                   + recent)
    if previous is not None and correction:
        prompt += (
            f"\n\nYour previous draft:\n{json.dumps(previous, indent=1)}\n\n"
            f'The user\'s correction:\n"""{correction}"""\n'
            "Apply the correction and return the full updated draft."
        )
    resp = claude.messages.create(
        model=CLAUDE_MODEL,
        max_tokens=1500,
        system=workflow.instructions() + "\n\nAlways answer by calling the tool.",
        tools=[workflow.TOOL],
        tool_choice={"type": "auto"},
        messages=[{"role": "user", "content": prompt}],
    )
    for block in resp.content:
        if block.type == "tool_use":
            return block.input
    raise RuntimeError("Claude did not return a draft")


class DraftView(discord.ui.View):
    """The Save / Discard buttons under each draft."""

    def __init__(self):
        super().__init__(timeout=None)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return interaction.user.id == ALLOWED_USER_ID

    async def _current(self, interaction: discord.Interaction):
        pending = drafts.get(interaction.message.id)
        if pending is None:
            body = interaction.message.content.replace(FOOTER, "")
            await interaction.response.edit_message(
                content=body + "\n\n*(This draft expired when the bot restarted. "
                               "If it wasn't saved, it will be drafted again.)*", view=None)
            return None
        return pending

    @discord.ui.button(label="Save", emoji="✅", style=discord.ButtonStyle.success)
    async def save(self, interaction: discord.Interaction, button: discord.ui.Button):
        pending = await self._current(interaction)
        if pending is None:
            return
        body = interaction.message.content.replace(FOOTER, "")
        await interaction.response.edit_message(content=body + "\n\n⏳ Saving…", view=None)
        try:
            await asyncio.to_thread(WORKFLOWS[pending["workflow"]].save, pending["draft"], pending["transcript"])
        except Exception as e:
            log.exception("save failed")
            await interaction.edit_original_response(content=body + FOOTER, view=DraftView())
            await interaction.followup.send(f"Couldn't save: {str(e)[:300]}\nTap Save again once it's fixed.")
            return
        await interaction.edit_original_response(content=body + "\n\n✅ Saved.")
        await finish(pending)

    @discord.ui.button(label="Discard", emoji="🗑", style=discord.ButtonStyle.secondary)
    async def discard(self, interaction: discord.Interaction, button: discord.ui.Button):
        pending = await self._current(interaction)
        if pending is None:
            return
        body = interaction.message.content.replace(FOOTER, "")
        await interaction.response.edit_message(content=body + "\n\n🗑 Discarded.", view=None)
        await finish(pending)


async def finish(pending: dict) -> None:
    drafts.pop(pending["view_msg"].id, None)
    mark_handled(*pending["msg_ids"])


async def start_draft(message: discord.Message, text: str | None = None, recent: str = "") -> None:
    wf = WORKFLOWS[runtime["workflow"]]
    drafting.add(message.id)
    try:
        async with message.channel.typing():
            if text is None:
                text = await message_text(message, await asyncio.to_thread(wf.vocabulary))
            result = await asyncio.to_thread(draft, wf, text, None, None, recent)
        sent = await message.channel.send(wf.preview(result) + FOOTER, view=DraftView(),
                                          reference=message, mention_author=False)
        drafts[sent.id] = {"workflow": wf.KEY, "draft": result, "transcript": text,
                           "msg_ids": [message.id], "view_msg": sent}
    except Exception as e:
        log.exception("drafting failed")
        await message.channel.send(f"Something went wrong with this note: {str(e)[:300]}",
                                   reference=message, mention_author=False)
    finally:
        drafting.discard(message.id)


async def apply_correction(message: discord.Message, pending: dict, fix: str | None = None) -> None:
    """Redraft one open draft with a text or voice correction, and update it in place."""
    wf = WORKFLOWS[pending["workflow"]]
    drafting.add(message.id)
    try:
        async with message.channel.typing():
            if fix is None:
                fix = await message_text(message, await asyncio.to_thread(wf.vocabulary))
            result = await asyncio.to_thread(draft, wf, pending["transcript"], pending["draft"], fix)
        await pending["view_msg"].edit(content=wf.preview(result) + "\n*(corrected)*" + FOOTER,
                                       view=DraftView())
    except Exception as e:
        log.exception("correction failed")
        await message.channel.send(f"Something went wrong: {str(e)[:300]}", reference=message,
                                   mention_author=False)
        return
    finally:
        drafting.discard(message.id)
    pending.update(draft=result, transcript=pending["transcript"] + "\n[correction] " + fix)
    pending["msg_ids"].append(message.id)
    await message.add_reaction("✏️")


# ---------- routing: correction, new note, or request ----------

ROUTE_TOOL = {
    "name": "route",
    "description": "Decide what Megan's message is.",
    "input_schema": {
        "type": "object",
        "properties": {
            "action": {"type": "string", "enum": ["correction", "new_note", "request"]},
            "draft_number": {"type": "integer", "description": "For a correction: which open draft. 0 otherwise."},
        },
        "required": ["action", "draft_number"],
    },
}

ROUTER_SYSTEM = """You route messages Megan sends to her networking CRM bot. Decide what each message is:
- correction: adds to, changes or comments on an OPEN DRAFT about the same person ("he's at MIT FutureTech", "due Friday", "remove the part about Carrick", "update his role"). Give its draft_number.
- new_note: a meeting, message, update or fact about a person who is not the subject of any open draft, to be filed in the CRM ("Carrick Flynn is an advisor at 80,000 Hours", "messaged Eugene on LinkedIn").
- request: asks the bot to write something or answer something ("draft an email to Carrick", "who do I know at Google?", "what should I do this week?").
Use the recent chat to work out who "he", "she" or "they" refers to. If a message mixes types, pick its main point. Always answer by calling the route tool."""


def open_drafts_list() -> list[dict]:
    return sorted(drafts.values(), key=lambda d: d["view_msg"].id)


def drafts_summary() -> str:
    lines = []
    for i, d in enumerate(open_drafts_list(), 1):
        x = d["draft"]
        lines.append(f"{i}. {x['person']} ({x.get('org') or 'org unknown'}): {x['summary']}"
                     + (f" Next step: {x['next_step']}" if x.get("next_step") else ""))
    return "\n".join(lines)


def route(text: str, recent: str) -> tuple[str, dict | None]:
    listed = open_drafts_list()
    prompt = (f"Open drafts (not saved yet):\n{drafts_summary() or 'none'}\n\n"
              f"Recent chat:\n{recent or '(none)'}\n\n"
              f'New message from Megan:\n"""{text}"""')
    resp = claude.messages.create(
        model=CLAUDE_MODEL, max_tokens=400, system=ROUTER_SYSTEM, tools=[ROUTE_TOOL],
        tool_choice={"type": "auto"}, messages=[{"role": "user", "content": prompt}])
    choice = next((b.input for b in resp.content if b.type == "tool_use"), {"action": "new_note"})
    action = choice.get("action", "new_note")
    n = choice.get("draft_number") or 0
    if action == "correction":
        if 1 <= n <= len(listed):
            return action, listed[n - 1]
        if len(listed) == 1:
            return action, listed[0]
        action = "new_note"
    return action, None


async def recent_chat(message: discord.Message, limit: int = 10) -> str:
    lines = []
    async for m in message.channel.history(limit=limit, before=message):
        who = "Bot" if m.author == client.user else "Megan"
        body = m.content.replace(FOOTER, "").strip() or ("[voice note]" if audio_of(m) else "[attachment]")
        lines.append(f"{who}: {body[:400]}")
    return "\n".join(reversed(lines))


async def answer(message: discord.Message, text: str, earlier: str = "") -> None:
    try:
        async with message.channel.typing():
            reply = await asyncio.to_thread(weekly.ask, text, earlier, drafts_summary(),
                                            await recent_chat(message))
    except Exception as e:
        log.exception("answer failed")
        await message.channel.send(f"Something went wrong: {str(e)[:300]}", reference=message,
                                   mention_author=False)
        return
    mark_handled(message.id)
    await send_long(message.channel, reply, reference=message)


async def handle_incoming(message: discord.Message) -> None:
    """Everything that isn't a command: corrections, goals, new notes and requests."""
    ref_id = message.reference.message_id if message.reference else None

    # A Reply to a draft always corrects that draft.
    if ref_id in drafts:
        await apply_correction(message, drafts[ref_id])
        return

    # A Reply to another bot message (an email draft, an answer) continues that conversation.
    if ref_id is not None:
        try:
            ref = message.reference.resolved or await message.channel.fetch_message(ref_id)
        except discord.HTTPException:
            ref = None
        if isinstance(ref, discord.Message) and ref.author == client.user:
            await answer(message, await message_text(message, []), earlier=ref.content)
            return

    if state.get("awaiting_goal"):
        mark_handled(message.id)
        async with message.channel.typing():
            goal = await message_text(message, [])
        if goal:
            await run_plan(message.channel, goal)
        return

    if not is_note(message):
        mark_handled(message.id)
        await message.channel.send("Send a voice note or a message about a meeting, or ask me something. "
                                   "Type `!help` for commands.")
        return

    wf = WORKFLOWS[runtime["workflow"]]
    drafting.add(message.id)
    try:
        async with message.channel.typing():
            text = await message_text(message, await asyncio.to_thread(wf.vocabulary))
            recent = await recent_chat(message)
            action, target = await asyncio.to_thread(route, text, recent)
    except Exception as e:
        log.exception("routing failed")
        await message.channel.send(f"Something went wrong: {str(e)[:300]}", reference=message,
                                   mention_author=False)
        return
    finally:
        drafting.discard(message.id)

    if action == "correction" and target is not None:
        await apply_correction(message, target, fix=text)
    elif action == "request":
        await answer(message, text)
    else:
        await start_draft(message, text=text, recent=recent)


# ---------- weekly loop ----------

async def run_roundup(channel) -> None:
    state["last_roundup_at"] = datetime.now(TZ).isoformat()
    store.save(state)
    try:
        async with channel.typing():
            text = await asyncio.to_thread(weekly.roundup)
    except Exception as e:
        log.exception("roundup failed")
        await channel.send(f"The weekly roundup failed: {str(e)[:300]}\nType `!roundup` to try again.")
        return
    await send_long(channel, "📋 **Weekly roundup**\n\n" + text)
    await channel.send("**What's your connection goal for this week?** "
                       "Reply by voice or text, or type `!skip`.")
    state["awaiting_goal"] = True
    state["week_of"] = datetime.now(TZ).strftime("%d %b %Y")
    store.save(state)


async def run_plan(channel, goal: str) -> None:
    week_of = state.get("week_of") or datetime.now(TZ).strftime("%d %b %Y")
    try:
        async with channel.typing():
            text = await asyncio.to_thread(weekly.plan, goal, week_of)
    except Exception as e:
        log.exception("plan failed")
        await channel.send(f"Couldn't make the plan: {str(e)[:300]}\nReply with your goal again to retry.")
        return
    state["awaiting_goal"] = False
    store.save(state)
    await send_long(channel, "🗺 **Plan for the week**\n\n" + text)


async def run_nudge(channel) -> None:
    state["last_nudge_at"] = datetime.now(TZ).isoformat()
    store.save(state)
    try:
        async with channel.typing():
            text = await asyncio.to_thread(weekly.nudge)
    except Exception as e:
        log.exception("nudge failed")
        await channel.send(f"The midweek check-in failed: {str(e)[:300]}")
        return
    await send_long(channel, "📌 **Midweek check-in**\n\n" + text)


@tasks.loop(minutes=1)
async def scheduler():
    if drafting or ROUNDUP_AT is None:
        return
    channel = client.get_channel(state.get("channel_id") or 0)
    if channel is None:
        return
    now = datetime.now(TZ)
    roundup_due = last_occurrence(now, ROUNDUP_AT)
    if ts("last_roundup_at") < roundup_due:
        await run_roundup(channel)
        return
    if NUDGE_AT:
        nudge_due = last_occurrence(now, NUDGE_AT)
        if nudge_due > roundup_due and ts("last_nudge_at") < nudge_due:
            await run_nudge(channel)


# ---------- Discord events ----------

intents = discord.Intents.default()
intents.message_content = True
client = discord.Client(intents=intents)


def readable_channels():
    for guild in client.guilds:
        for ch in guild.text_channels:
            perms = ch.permissions_for(guild.me)
            if perms.read_messages and perms.read_message_history:
                yield ch


async def catch_up() -> None:
    now = datetime.now(timezone.utc)
    if not state.get("since"):
        # First run: start from the bot's own last message, so earlier notes aren't redone.
        last_bot = None
        for ch in readable_channels():
            async for m in ch.history(limit=50):
                if m.author == client.user:
                    last_bot = max(last_bot or m.created_at, m.created_at)
                    break
        state["since"] = (last_bot or now - timedelta(hours=24)).isoformat()
        store.save(state)
    since = max(datetime.fromisoformat(state["since"]), now - timedelta(days=7))

    found = []
    for ch in readable_channels():
        async for m in ch.history(limit=100, after=since, oldest_first=True):
            if (m.author.id == ALLOWED_USER_ID and is_note(m)
                    and not is_handled(m.id) and not in_flight(m)):
                found.append(m)
    if not found:
        return
    found.sort(key=lambda m: m.id)
    await found[0].channel.send(
        f"Catching up on {len(found)} note(s) you sent while I was offline. "
        "To fix a draft, use Reply on it.")
    for m in found:
        if not is_handled(m.id) and not in_flight(m):
            await handle_incoming(m)


@client.event
async def on_ready():
    log.info("Bot running as %s. Workflows: %s.", client.user, ", ".join(WORKFLOWS))
    if "last_roundup_at" not in state:
        # First run: don't fire a roundup for a Sunday that happened before the bot existed.
        state["last_roundup_at"] = state["last_nudge_at"] = datetime.now(TZ).isoformat()
        store.save(state)
    if not scheduler.is_running():
        scheduler.start()
    await catch_up()


@client.event
async def on_resumed():
    await catch_up()


HELP = (
    "Send a voice note or text after a meeting and I'll draft a CRM entry. "
    "You can also ask me things, like \"draft a follow-up email to Carrick\" or "
    "\"who do I know at Google?\". Reply to any draft or answer to refine it.\n"
    "Commands:\n"
    "`!template` what to cover in a note (pin it!)\n"
    "`!roundup` weekly roundup now · `!nudge` midweek check-in now\n"
    "`!goal <text>` set this week's goal · `!skip` skip the goal question\n"
    "Modes: {modes}"
)


async def handle_command(message: discord.Message) -> None:
    parts = message.content[len(PREFIX):].strip().split(maxsplit=1)
    name = parts[0].lower() if parts else ""
    arg = parts[1].strip() if len(parts) > 1 else ""
    channel = message.channel
    if name in WORKFLOWS:
        runtime["workflow"] = name
        await channel.send(f"Mode: {WORKFLOWS[name].NAME}. {WORKFLOWS[name].HINT}")
    elif name == "template":
        wf = WORKFLOWS[runtime["workflow"]]
        await channel.send(getattr(wf, "TEMPLATE", "No template for this mode."))
    elif name == "roundup":
        await run_roundup(channel)
    elif name == "nudge":
        await run_nudge(channel)
    elif name == "goal":
        if arg:
            await run_plan(channel, arg)
        else:
            state["awaiting_goal"] = True
            store.save(state)
            await channel.send("What's your connection goal for this week? Reply by voice or text.")
    elif name == "skip":
        state["awaiting_goal"] = False
        store.save(state)
        await channel.send("Skipped. Type `!goal` any time to set one.")
    else:
        await channel.send(HELP.format(modes=", ".join(f"`{PREFIX}{n}`" for n in WORKFLOWS)))


@client.event
async def on_message(message: discord.Message):
    if message.author.bot:
        return
    if message.author.id != ALLOWED_USER_ID:
        if ALLOWED_USER_ID == 0:
            await message.channel.send(
                f"Your Discord user ID is {message.author.id}. "
                "Put it in ALLOWED_USER_ID in .env and restart the bot.")
        return
    if is_handled(message.id) or in_flight(message):
        return
    if state.get("channel_id") != message.channel.id:
        state["channel_id"] = message.channel.id
        store.save(state)

    content = message.content.strip()
    if content.startswith(PREFIX):
        mark_handled(message.id)
        await handle_command(message)
        return

    await handle_incoming(message)


if __name__ == "__main__":
    client.run(DISCORD_TOKEN, log_handler=None)

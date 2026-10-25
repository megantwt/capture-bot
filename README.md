# capture-bot

A voice-note agent that keeps my networking CRM up to date. After a coffee chat or call, I record a quick voice note in Discord. The bot transcribes it, drafts a structured CRM entry with Claude, and writes to my Google Sheet only after I approve the draft.

**Why I built it:** my previous CRM failed because updating it took too much effort, so it went stale. The aim here was near-zero friction to capture, while keeping a human in the loop: the agent proposes, I confirm, and nothing is written without my approval.

## How it works

```
Voice note (Discord)
   → local transcription (faster-whisper)
   → Claude drafts a structured entry via tool calling
   → I review: Save / Discard / reply to correct
   → Google Sheet (Contacts + Interactions tabs)
```

## Features

- **Voice-to-CRM:** turns a rambling voice note into a structured interaction log and contact record
- **Approval before every write:** drafts appear in Discord with Save and Discard buttons; replying to a draft corrects it
- **Several drafts at once:** each draft can be reviewed and corrected independently
- **Contact matching:** disambiguates people by name, organization and role, and flags missing fields on new contacts
- **Interactions vs notes:** separates real interactions (which reset the follow-up clock) from background facts
- **Free-form requests:** a Claude-based router sends messages to corrections, new notes, or tasks such as "draft a follow-up email to Alex"
- **Weekly review loop:** a Sunday roundup of the week, a goal-setting prompt, a ranked follow-up plan with drafted messages, and a midweek check-in
- **Catch-up:** notes sent while the laptop is off are drafted when it wakes
- **Plain-English configuration:** behavior is driven by editable prompt files in `prompts/`, so changes need no code

## Design choices

- **Local transcription.** Audio is transcribed on-device with Whisper, so recordings never leave my laptop and no extra API key is needed.
- **Structured output via tool calling.** Claude fills a defined schema instead of free text, so entries map cleanly onto spreadsheet columns.
- **A reusable workflow pattern.** The CRM is one plugin in `workflows/`; the Discord, transcription and approval layers can be reused for other workflows.

## Repo structure

| File | Purpose |
|---|---|
| `bot.py` | Discord bot: messages, voice notes, approval buttons, replies |
| `transcribe.py` | Local speech-to-text with faster-whisper |
| `workflows/crm.py` | CRM workflow: drafting, contact matching, routing |
| `store.py` | Google Sheets read/write |
| `weekly.py` | Sunday roundup and midweek check-in |
| `prompts/` | Plain-English instructions the agent follows |
| `autostart_on.sh` / `autostart_off.sh` | Run the bot in the background on macOS |

## Running it yourself

You'll need macOS, Python 3.10+ (installed via [uv](https://github.com/astral-sh/uv)), a Discord bot token, an Anthropic API key, and a Google Cloud service account with Editor access to your sheet.

1. Copy `.env.example` to `.env` and fill in your keys and sheet ID.
2. Put your service account key in the project folder as `service_account.json`.
3. Copy `prompts/goals.example.md` to `prompts/goals.md` and write your own goals.
4. Install dependencies with `uv pip install -r requirements.txt`, then run `python bot.py`.

## Roadmap

- Ingest LinkedIn conversations from screenshots
- Import LinkedIn's message export to flag unanswered threads

---
Built with Claude as a coding partner.

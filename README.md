# Capture bot

Send a voice message or text to your bot in a private Discord server. It transcribes the audio on your laptop, has Claude draft an entry, shows you the draft, and saves it when you tap ✅ Save. Reply with a correction instead and it redrafts.

The CRM is the first workflow. Others can be added later (see "Changing it").

---

## Setup (about 30 minutes, once)

These steps assume a Mac. Discord's menus change occasionally, so a button may be worded slightly differently from what's written here.

### Step 1. Check Python
Open **Terminal** (Cmd+Space, type Terminal) and run:
```
python3 --version
```
You need 3.10 or newer. If it's older or "command not found", install Python from python.org/downloads, then close and reopen Terminal.

### Step 2. Put the folder somewhere sensible
Unzip `capture-bot.zip` into your Documents folder. Then in Terminal:
```
cd ~/Documents/capture-bot
```

### Step 3. Get Discord and make a private server
1. Install Discord on your laptop and phone (discord.com) and create an account.
2. In the left sidebar, click the **+** ("Add a Server") → **Create My Own** → **For me and my friends**.
3. Name it something like "CRM" and click Create. Nobody else is in it unless you invite them.

### Step 4. Create the bot
1. Go to **discord.com/developers/applications** and log in with the same account.
2. Click **New Application**, name it (e.g. "CRM Bot"), agree, Create.
3. In the left menu, click **Bot**.
4. Click **Reset Token** → confirm → **Copy**. Keep it for Step 7. It's shown only once; if you lose it, reset it again.
5. On the same page, scroll to **Privileged Gateway Intents** and turn on **Message Content Intent**. Click **Save Changes**. (Without this the bot can't read your messages.)
6. Optional: turn off **Public Bot** so nobody else can add it to their server.

### Step 5. Add the bot to your server
1. In the left menu, click **OAuth2** → **URL Generator**.
2. Under Scopes, tick **bot**.
3. Under Bot Permissions, tick **View Channels**, **Send Messages** and **Read Message History**.
4. Copy the generated URL at the bottom, open it in your browser, choose your CRM server, and click **Authorize**.
5. Back in Discord, the bot now appears in your server's member list (offline until you run it).

### Step 6. Get an Anthropic API key
1. Go to console.anthropic.com and sign in.
2. Add a small amount of credit under Billing (a few dollars lasts months at this usage).
3. Go to API Keys → Create Key. Copy it for Step 7. It's shown only once.

### Step 7. Let the bot write to your Google Sheet
1. If you haven't yet, import `Networking CRM.xlsx` into Google Sheets (sheets.google.com → Blank → File → Import → Upload → "Replace spreadsheet"). Check the tabs are named **Contacts** and **Interactions**.
2. Go to console.cloud.google.com. Create a new project (top bar → project dropdown → New Project). Any name, e.g. "capture-bot". Make sure it's selected afterwards.
3. In the search bar, type **Google Sheets API**, open it, click **Enable**.
4. Search for **Service Accounts** → **Create service account**. Give it any name, click **Done** (skip the optional steps).
5. Click the new service account → **Keys** tab → **Add key** → **Create new key** → **JSON**. A file downloads.
6. Rename that file to `service_account.json` and move it into the `capture-bot` folder.
7. Copy the service account's email (on its details page; it ends in `iam.gserviceaccount.com`).
8. Open your Google Sheet → **Share** → paste that email → **Editor** → untick "Notify people" → Share.

### Step 8. Fill in your settings
In Terminal (inside `capture-bot`):
```
cp .env.example .env
open -e .env
```
TextEdit opens. Fill in:
- `DISCORD_BOT_TOKEN` from Step 4
- `ANTHROPIC_API_KEY` from Step 6
- `GOOGLE_SHEET_ID`: the long part of your sheet's URL between `/d/` and `/edit`

Save and close. (`.env` is a hidden file in Finder; that's normal.)

### Step 9. Install and run
```
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
python bot.py
```
When you see `Bot running as ...`, it's working, and the bot shows as online in Discord. Leave this Terminal window open.

### Step 10. Lock the bot to you
1. In your Discord server, type anything in #general (e.g. "hi"). The bot replies with your user ID.
2. In Terminal, press **Ctrl+C** to stop the bot.
3. Run `open -e .env`, set `ALLOWED_USER_ID` to that number, save.
4. Run `python bot.py` again and type `!help`. It should reply with the current mode.

### Step 11. First test
On your phone, open the server's #general channel, hold the microphone button, and record: "Test entry. Coffee with Jane Doe from Example Corp, she offered to introduce me to her team, I'll follow up next Friday."
The first voice message downloads the speech model (about 500 MB, one time), so it may take a minute. You should get a draft back. Tap ✅ Save and check both tabs in the sheet. Then delete the test rows.

---

## Daily use

### Running in the background (recommended)
Turn on auto-start once:
```
cd ~/capture-bot
bash autostart_on.sh
```
From then on the bot starts by itself when you log in, keeps running while the laptop is awake, and restarts if it crashes. You never need to type `python bot.py`.

- Notes you send while the laptop is asleep or off are picked up when it wakes ("Catching up on N notes…"), and drafted one at a time.
- See what it's doing: `tail -f ~/capture-bot/bot.log` (Ctrl+C stops watching, not the bot).
- After changing `.env` or any `.py` file: `bash ~/capture-bot/restart.sh`. Edits to files in `prompts/` apply immediately, no restart.
- Turn it off: `bash ~/capture-bot/autostart_off.sh`.
- Don't also run `python bot.py` in Terminal while auto-start is on, or you'll get double replies.

### Talking to the bot
Just send messages naturally, by voice or text. The bot works out what each one is:
- **A note about someone** ("Carrick Flynn, advisor at 80,000 Hours, met at EAG") becomes a draft with Save and Discard buttons. Quick updates ("messaged Eugene on LinkedIn") and facts ("Note: Eugene moved to DeepMind") work too. Several drafts can be open at once.
- **More about someone with an open draft** ("he's at MIT FutureTech") updates that draft (✏️ on your message).
- **A request** ("draft a follow-up email to Carrick", "who do I know at Google?") gets an answer from your CRM.

To be certain which draft or answer you mean, long-press it and choose **Reply**. Replies to a draft always correct that draft; replies to an email or answer revise it ("shorter", "mention my paper").

Nothing is written to the sheet until you tap **Save** on a draft. If your Contacts tab has a **Latest update** column, the bot fills it with the date and summary of the latest note.

### Weekly loop
- **Sunday 6pm:** a roundup of the week, then the bot asks your connection goal for the week. Reply by voice or text and it sends a plan with follow-ups, draft messages and where to find new connections. Your goal and plan are saved to a **Weekly** tab (created automatically).
- **Wednesday 9am:** a three-line check-in.
- If the laptop was asleep at those times, they arrive when it wakes.
- Change the times with `ROUNDUP_TIME` and `NUDGE_TIME` in `.env` (e.g. `sat 10:00`, or `off`).

### Commands (type in Discord)
- `!template` checklist of what to cover in a note (pin it in the channel)
- `!roundup` roundup now · `!nudge` check-in now
- `!goal <text>` set this week's goal any time · `!skip` skip the goal question
- `!help` list commands

## Changing it

**Change how entries are written** (most common): edit `prompts/crm.md` in plain English, e.g. "keep summaries to two sentences" or "always fill in context". Takes effect on the next note. No restart, no code changes.

**Change your goals**: edit `prompts/goals.md`. Both the note drafting and the weekly loop use it.

**Change the weekly messages**: edit `prompts/weekly.md` (what the roundup, plan and check-in cover).

**Change transcription accuracy**: set `WHISPER_MODEL` in `.env` to `base` (faster) or `medium` (better with names).

**Add a new use** (e.g. expenses, reading notes, a to-do inbox): add a file in `workflows/` modelled on `crm.py`, its instructions in `prompts/`, and register it in `workflows/__init__.py`. Its name becomes a command such as `!expenses` that switches the bot into that mode; `!crm` switches back. Ask Claude to write the new workflow file for you.

---

## Privacy
- Voice is transcribed on your laptop. Only the text goes to Anthropic for drafting.
- Keep `.env` and `service_account.json` private. Don't share or upload them.

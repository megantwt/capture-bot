"""
Registry of workflows. To add a new one:
  1. Copy workflows/crm.py to workflows/<name>.py and change it.
  2. Write its instructions in prompts/<name>.md.
  3. Import it below and add it to WORKFLOWS.
Its key becomes a Telegram command (e.g. /crm) that switches the bot into that mode.
"""

from . import crm

WORKFLOWS = {
    crm.KEY: crm,
}

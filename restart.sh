#!/bin/bash
# Restarts the background bot, e.g. after changing .env or code files.
launchctl kickstart -k "gui/$(id -u)/com.capturebot" && echo "Restarted." || echo "Auto-start isn't on. Run: bash autostart_on.sh"

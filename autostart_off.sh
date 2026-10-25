#!/bin/bash
# Stops the background bot and turns auto-start off.
LABEL="com.capturebot"
launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
rm -f "$HOME/Library/LaunchAgents/$LABEL.plist"
echo "Auto-start is off and the background bot is stopped."

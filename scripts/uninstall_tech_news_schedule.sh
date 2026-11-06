#!/usr/bin/env bash
# Stop and remove the tech-news-agent daily launchd job.

set -euo pipefail

DEST_PLIST="$HOME/Library/LaunchAgents/com.tech-news-agent.daily.plist"
LABEL="com.tech-news-agent.daily"

if launchctl print "gui/$(id -u)/$LABEL" >/dev/null 2>&1; then
    launchctl bootout "gui/$(id -u)" "$DEST_PLIST" || true
    echo "Unloaded $LABEL."
else
    echo "$LABEL was not loaded."
fi

if [[ -f "$DEST_PLIST" ]]; then
    rm "$DEST_PLIST"
    echo "Removed $DEST_PLIST"
fi

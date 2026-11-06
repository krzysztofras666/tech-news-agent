#!/usr/bin/env bash
# Install the tech-news-agent launchd job (Mon/Wed/Fri 06:00 local time).

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

SRC_PLIST="$SCRIPT_DIR/com.tech-news-agent.daily.plist"
DEST_DIR="$HOME/Library/LaunchAgents"
DEST_PLIST="$DEST_DIR/com.tech-news-agent.daily.plist"
LABEL="com.tech-news-agent.daily"

if [[ ! -f "$SRC_PLIST" ]]; then
    echo "ERROR: plist not found: $SRC_PLIST" >&2
    exit 1
fi

if [[ ! -x "$SCRIPT_DIR/run_tech_news_daily.sh" ]]; then
    echo "ERROR: wrapper not executable: $SCRIPT_DIR/run_tech_news_daily.sh" >&2
    echo "  fix:  chmod +x $SCRIPT_DIR/run_tech_news_daily.sh" >&2
    exit 1
fi

mkdir -p "$DEST_DIR"
mkdir -p "$ROOT_DIR/logs"

if launchctl print "gui/$(id -u)/$LABEL" >/dev/null 2>&1; then
    echo "Unloading existing job ($LABEL)..."
    launchctl bootout "gui/$(id -u)" "$DEST_PLIST" 2>/dev/null || true
fi

cp "$SRC_PLIST" "$DEST_PLIST"
echo "Installed plist: $DEST_PLIST"

launchctl bootstrap "gui/$(id -u)" "$DEST_PLIST"
echo "Loaded $LABEL into launchd."

echo
echo "Status:"
launchctl print "gui/$(id -u)/$LABEL" \
    | grep -E "state|next fire|last exit code|path|program|stdout|stderr" \
    | sed 's/^/  /'

cat <<EOF

Done. The tech-news agent will email your LinkedIn draft every Monday,
Wednesday, and Friday at 06:00 local time to andalath@gmail.com
(override with TECH_NEWS_EMAIL_TO in .env).

Logs:
  $ROOT_DIR/logs/tech_news_run.log
  $ROOT_DIR/logs/tech_news.launchd.out.log
  $ROOT_DIR/logs/tech_news.launchd.err.log

Test it once now:
  launchctl kickstart gui/\$(id -u)/$LABEL

Or run the wrapper directly:
  $SCRIPT_DIR/run_tech_news_daily.sh

Dry-run (no send):
  $SCRIPT_DIR/run_tech_news_daily.sh --dry-run

Uninstall:
  $SCRIPT_DIR/uninstall_tech_news_schedule.sh
EOF

#!/usr/bin/env bash
# Unattended run of the tech-news agent. Invoked by launchd Mon/Wed/Fri at 06:00.

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

cd "$ROOT_DIR"

export LANG="${LANG:-en_US.UTF-8}"
export LC_ALL="${LC_ALL:-en_US.UTF-8}"

if [[ -f ".venv/bin/activate" ]]; then
    # shellcheck disable=SC1091
    source ".venv/bin/activate"
else
    echo "[$(date -Iseconds)] WARNING: .venv missing, using system python3" >&2
fi

mkdir -p logs

LOG_FILE="logs/tech_news_run.log"

if [[ -f "$LOG_FILE" ]] && [[ $(stat -f%z "$LOG_FILE" 2>/dev/null || echo 0) -gt 5000000 ]]; then
    mv "$LOG_FILE" "$LOG_FILE.1"
fi

{
    echo
    echo "===== $(date -Iseconds) run_tech_news_daily.sh start ====="
    python -m tech_news_agent send --no-browser "$@"
    rc=$?
    echo "===== $(date -Iseconds) run_tech_news_daily.sh end (exit $rc) ====="
} >> "$LOG_FILE" 2>&1

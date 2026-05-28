#!/usr/bin/env bash
# Long-running intraday engine wrapper. launchd KeepAlive=true will
# restart this on crash/exit; the Python CLI owns the inner poll
# cadence via TRADEPRO_INTRADAY_POLL_SECONDS.
#
# Pause/resume without unloading launchd:
#   touch ~/.tradepro/intraday-engine.pause
#   rm    ~/.tradepro/intraday-engine.pause
#
# Stop completely:
#   launchctl bootout gui/$UID ~/Library/LaunchAgents/com.tradepro.intraday-engine.plist
#
# Logs land in:
#   ~/.tradepro/logs/intraday-engine-YYYY-MM-DD.log
#   ~/.tradepro/logs/intraday-engine-stdout.log (launchd stdout)
#   ~/.tradepro/logs/intraday-engine-stderr.log (launchd stderr)

set -uo pipefail

PROJECT_DIR="${TRADEPRO_PROJECT_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
LOG_DIR="$HOME/.tradepro/logs"
mkdir -p "$LOG_DIR"

UV="$(command -v uv || echo /usr/local/bin/uv)"
if [[ ! -x "$UV" ]]; then
  UV="/opt/homebrew/bin/uv"
fi

cd "$PROJECT_DIR" || exit 1

DATESTAMP=$(date -u +%Y-%m-%d)
LOG_FILE="$LOG_DIR/intraday-engine-$DATESTAMP.log"

echo "[$(date -u '+%Y-%m-%dT%H:%M:%SZ')] starting tradepro-intraday-engine" >>"$LOG_FILE"
exec "$UV" run tradepro-intraday-engine >>"$LOG_FILE" 2>&1

#!/usr/bin/env bash
# Long-running tradepro-data-worker wrapper. launchd KeepAlive=true
# restarts on crash/exit; the Python CLI owns the inner poll cadence
# via --poll-interval-seconds.
#
# Pause/resume without unloading launchd:
#   touch ~/.tradepro/data-worker.pause
#   rm    ~/.tradepro/data-worker.pause
#
# Stop completely:
#   launchctl bootout gui/$UID ~/Library/LaunchAgents/com.tradepro.data-worker.plist
#
# Logs:
#   ~/.tradepro/logs/data-worker-YYYY-MM-DD.log
#   ~/.tradepro/logs/data-worker-stdout.log (launchd stdout)
#   ~/.tradepro/logs/data-worker-stderr.log (launchd stderr)

set -uo pipefail

PROJECT_DIR="${TRADEPRO_PROJECT_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
LOG_DIR="$HOME/.tradepro/logs"
mkdir -p "$LOG_DIR"

# Same uv-resolution dance as intraday-engine.sh — launchd hands us a
# minimal PATH so we probe explicit candidates after PATH extension.
export PATH="/opt/homebrew/bin:/opt/anaconda3/bin:$HOME/.local/bin:$HOME/.cargo/bin:/usr/local/bin:$PATH"
UV="$(command -v uv || true)"
if [[ ! -x "$UV" ]]; then
  for cand in /opt/anaconda3/bin/uv /opt/homebrew/bin/uv "$HOME/.local/bin/uv" "$HOME/.cargo/bin/uv" /usr/local/bin/uv; do
    if [[ -x "$cand" ]]; then UV="$cand"; break; fi
  done
fi
if [[ ! -x "$UV" ]]; then
  echo "[$(date -u '+%Y-%m-%dT%H:%M:%SZ')] FATAL: uv not found on PATH or known locations" >>"$LOG_DIR/data-worker-$(date -u +%Y-%m-%d).log"
  exit 1
fi

cd "$PROJECT_DIR" || exit 1

DATESTAMP=$(date -u +%Y-%m-%d)
LOG_FILE="$LOG_DIR/data-worker-$DATESTAMP.log"

API_BASE="${TRADEPRO_API_URL:-http://localhost:5252}"
POLL_SEC="${TRADEPRO_DATA_WORKER_POLL_SECONDS:-10}"

echo "[$(date -u '+%Y-%m-%dT%H:%M:%SZ')] starting tradepro-data-worker --api-base $API_BASE --poll-interval-seconds $POLL_SEC" >>"$LOG_FILE"
exec "$UV" run tradepro-data-worker \
    --api-base "$API_BASE" \
    --poll-interval-seconds "$POLL_SEC" \
    >>"$LOG_FILE" 2>&1

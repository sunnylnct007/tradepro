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

# Resolve uv robustly. launchd hands the wrapper a MINIMAL PATH (no
# Homebrew/anaconda/cargo), so `command -v uv` finds nothing and the
# old /opt/homebrew/bin/uv fallback didn't exist on this box (uv is in
# anaconda) — the daemon then exited 126 every minute and never traded.
# Prepend the usual install dirs, then probe explicit candidates.
export PATH="/opt/homebrew/bin:/opt/anaconda3/bin:$HOME/.local/bin:$HOME/.cargo/bin:/usr/local/bin:$PATH"
UV="$(command -v uv || true)"
if [[ ! -x "$UV" ]]; then
  for cand in /opt/anaconda3/bin/uv /opt/homebrew/bin/uv "$HOME/.local/bin/uv" "$HOME/.cargo/bin/uv" /usr/local/bin/uv; do
    if [[ -x "$cand" ]]; then UV="$cand"; break; fi
  done
fi
if [[ ! -x "$UV" ]]; then
  echo "[$(date -u '+%Y-%m-%dT%H:%M:%SZ')] FATAL: uv not found on PATH or known locations" >>"$LOG_DIR/intraday-engine-$(date -u +%Y-%m-%d).log"
  exit 1
fi

cd "$PROJECT_DIR" || exit 1

DATESTAMP=$(date -u +%Y-%m-%d)
LOG_FILE="$LOG_DIR/intraday-engine-$DATESTAMP.log"

echo "[$(date -u '+%Y-%m-%dT%H:%M:%SZ')] starting tradepro-intraday-engine" >>"$LOG_FILE"
exec "$UV" run tradepro-intraday-engine >>"$LOG_FILE" 2>&1

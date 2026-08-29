#!/usr/bin/env bash
# Post-earnings put candidates — daily screen wrapper.
#
# Runs AFTER the close so it reads a settled bar for the report reaction. It
# needs NO live market data — bars and an earnings date only — so unlike the
# wheel screen it is valid with the market shut and cannot compete for the
# single IBKR market-data session.
#
# Log: ~/.tradepro/logs/post-earnings-puts.out
set -uo pipefail
PROJECT_DIR="${TRADEPRO_PROJECT_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
LOG_DIR="$HOME/.tradepro/logs"; mkdir -p "$LOG_DIR"
LOG="$LOG_DIR/post-earnings-puts-$(date -u +%F).log"
log() { echo "[$(date -u '+%Y-%m-%dT%H:%M:%SZ')] $*" | tee -a "$LOG"; }

# launchd gives a minimal PATH; resolve uv the way every other job here does.
export PATH="/opt/homebrew/bin:/opt/anaconda3/bin:$HOME/.local/bin:$HOME/.cargo/bin:/usr/local/bin:$PATH"
UV="$(command -v uv || true)"
if [[ ! -x "$UV" ]]; then
  for c in /opt/anaconda3/bin/uv /opt/homebrew/bin/uv "$HOME/.local/bin/uv" /usr/local/bin/uv; do
    [[ -x "$c" ]] && { UV="$c"; break; }
  done
fi
[[ -x "$UV" ]] || { log "FATAL: uv not found"; exit 1; }

cd "$PROJECT_DIR" || { log "FATAL: cannot cd to $PROJECT_DIR"; exit 1; }
log "post-earnings puts screen starting"
"$UV" run tradepro-post-earnings-puts --push 2>&1 | tee -a "$LOG"
rc=${PIPESTATUS[0]}
log "finished rc=$rc"
exit "$rc"

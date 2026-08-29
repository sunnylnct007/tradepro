#!/usr/bin/env bash
# Index short-strangle paper record — decide, record, email.
#
# Runs BEFORE each market's open so the decision is made on the prior session's
# volatility close, exactly as it would be live. Needs no option chain and no
# broker: index bars and a volatility index only, so it cannot be stopped by a
# dark options feed or a contended market-data session.
set -uo pipefail
PROJECT_DIR="${TRADEPRO_PROJECT_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
LOG_DIR="$HOME/.tradepro/logs"; mkdir -p "$LOG_DIR"
LOG="$LOG_DIR/index-strangle-paper-$(date -u +%F).log"
log() { echo "[$(date -u '+%Y-%m-%dT%H:%M:%SZ')] $*" | tee -a "$LOG"; }

export PATH="/opt/homebrew/bin:/opt/anaconda3/bin:$HOME/.local/bin:$HOME/.cargo/bin:/usr/local/bin:$PATH"
UV="$(command -v uv || true)"
if [[ ! -x "$UV" ]]; then
  for c in /opt/anaconda3/bin/uv /opt/homebrew/bin/uv "$HOME/.local/bin/uv" /usr/local/bin/uv; do
    [[ -x "$c" ]] && { UV="$c"; break; }
  done
fi
[[ -x "$UV" ]] || { log "FATAL: uv not found"; exit 1; }
cd "$PROJECT_DIR" || { log "FATAL: cannot cd to $PROJECT_DIR"; exit 1; }

log "index strangle paper record starting"
"$UV" run python -m tradepro_strategies.cli.index_strangle_paper --email 2>&1 | tee -a "$LOG"
rc=${PIPESTATUS[0]}
log "finished rc=$rc"
exit "$rc"

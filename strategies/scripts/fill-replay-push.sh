#!/usr/bin/env bash
# Daily fill-replay push — replays the ICH equity clone's ACTUAL OMS fills
# (entry-extension + outcome + one-shot-vs-staggered, by universe) and pushes
# the artifact to /api/ingest/fill-replay so the cockpit card auto-refreshes.
#
# Scheduled by com.tradepro.fill-replay-push.plist at 21:45 BST (Mon-Fri) —
# AFTER the 21:30 daily bar-cache harvest, so the 200-SMA / extension calc
# reads the freshest daily bars and after the US close so fills have settled.
#
# Credentials (api-base-url + api-token) resolve via the strategies secret
# chain (env → AWS Secrets Manager → ~/.tradepro/credentials), same as the
# manual `tradepro-fill-replay --push`.
set -uo pipefail

export PATH="/opt/homebrew/bin:/opt/anaconda3/bin:$HOME/.local/bin:$HOME/.cargo/bin:/usr/local/bin:$PATH"
UV="$(command -v uv || true)"
[[ -x "$UV" ]] || { echo "FATAL: uv not found"; exit 1; }

PROJECT_DIR="${TRADEPRO_PROJECT_DIR:-/Users/skumar/sourcecode/tradepro/tradepro/strategies}"
STRATEGY="${TRADEPRO_FILL_REPLAY_STRATEGY:-ichimoku_equity_ibkr}"
LOG_DIR="$HOME/.tradepro/logs"; mkdir -p "$LOG_DIR"
LOG="$LOG_DIR/fill-replay-push-$(date -u +%F).log"
log() { echo "[$(date -u '+%Y-%m-%dT%H:%M:%SZ')] $*" | tee -a "$LOG"; }

cd "$PROJECT_DIR" || exit 1
log "fill-replay push: strategy=$STRATEGY"
"$UV" run tradepro-fill-replay --strategy "$STRATEGY" --push 2>&1 | tee -a "$LOG"
rc="${PIPESTATUS[0]}"
log "done rc=$rc"
exit "$rc"

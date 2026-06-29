#!/usr/bin/env bash
# Daily "Today's Setups" scan + push — screens large_50 by entry quality
# (Ichimoku signal + range/risk + kijun) and pushes the ranked artifact to
# /api/ingest/today-setups so the dashboard scanner card auto-refreshes.
#
# Scheduled by com.tradepro.today-setups-push.plist at 21:50 BST (Mon-Fri) —
# AFTER the 21:30 daily bar-cache harvest (fresh closes) and the 21:45
# fill-replay push. The setups reflect the latest complete daily bar, ready
# for the next session's decision.
#
# Credentials resolve via the strategies secret chain (env → AWS SM →
# ~/.tradepro/credentials), same as the manual `tradepro-today-setups --push`.
set -uo pipefail

export PATH="/opt/homebrew/bin:/opt/anaconda3/bin:$HOME/.local/bin:$HOME/.cargo/bin:/usr/local/bin:$PATH"
UV="$(command -v uv || true)"
[[ -x "$UV" ]] || { echo "FATAL: uv not found"; exit 1; }

PROJECT_DIR="${TRADEPRO_PROJECT_DIR:-/Users/skumar/sourcecode/tradepro/tradepro/strategies}"
UNIVERSE="${TRADEPRO_SETUPS_UNIVERSE:-large_50}"
LOG_DIR="$HOME/.tradepro/logs"; mkdir -p "$LOG_DIR"
LOG="$LOG_DIR/today-setups-push-$(date -u +%F).log"
log() { echo "[$(date -u '+%Y-%m-%dT%H:%M:%SZ')] $*" | tee -a "$LOG"; }

cd "$PROJECT_DIR" || exit 1
log "today-setups push: universe=$UNIVERSE"
"$UV" run tradepro-today-setups --universe "$UNIVERSE" --push 2>&1 | tee -a "$LOG"
rc="${PIPESTATUS[0]}"
log "done rc=$rc"
exit "$rc"

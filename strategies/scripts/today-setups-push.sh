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
# Scan BOTH the core (large_50) and high-beta universes — the scanner is a
# discretionary tool, so it should surface opportunities across both, with the
# risk (ATR/universe) flagged per setup. Each pushes its own artifact; the
# dashboard card merges + tags them.
UNIVERSES="${TRADEPRO_SETUPS_UNIVERSES:-large_50 high_beta}"
LOG_DIR="$HOME/.tradepro/logs"; mkdir -p "$LOG_DIR"
LOG="$LOG_DIR/today-setups-push-$(date -u +%F).log"
log() { echo "[$(date -u '+%Y-%m-%dT%H:%M:%SZ')] $*" | tee -a "$LOG"; }

cd "$PROJECT_DIR" || exit 1
rc=0
for U in $UNIVERSES; do
  log "today-setups push: universe=$U"
  "$UV" run tradepro-today-setups --universe "$U" --push 2>&1 | tee -a "$LOG"
  r="${PIPESTATUS[0]}"; [ "$r" != "0" ] && rc="$r"
done
log "done rc=$rc"
exit "$rc"

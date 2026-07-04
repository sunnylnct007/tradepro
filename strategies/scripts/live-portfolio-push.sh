#!/usr/bin/env bash
# Daily live-portfolio push — recomputes the trader's algo target portfolio from the
# latest COMPLETED daily bar and persists it as strategy_runs + strategy_decisions
# (the decision AUDIT TRAIL an agent can query). This recording DIED 2026-05-29 when
# nothing scheduled it; this wrapper + com.tradepro.live-portfolio.plist revive it.
#
# Scheduled at 21:35 BST (Mon-Fri) — after the 21:30 daily bar-cache harvest so it
# reads fresh closes. Credentials resolve via the usual secret chain.
set -uo pipefail

export PATH="/opt/homebrew/bin:/opt/anaconda3/bin:$HOME/.local/bin:$HOME/.cargo/bin:/usr/local/bin:$PATH"
UV="$(command -v uv || true)"
[[ -x "$UV" ]] || { echo "FATAL: uv not found"; exit 1; }

PROJECT_DIR="${TRADEPRO_PROJECT_DIR:-/Users/skumar/sourcecode/tradepro/tradepro/strategies}"
STRATEGIES="${TRADEPRO_LIVE_PORTFOLIO_STRATEGIES:-ichimoku_equity}"
LOG_DIR="$HOME/.tradepro/logs"; mkdir -p "$LOG_DIR"
LOG="$LOG_DIR/live-portfolio-push-$(date -u +%F).log"
log() { echo "[$(date -u '+%Y-%m-%dT%H:%M:%SZ')] $*" | tee -a "$LOG"; }

cd "$PROJECT_DIR" || exit 1
rc=0
for S in $STRATEGIES; do
  log "live-portfolio push: strategy=$S"
  "$UV" run tradepro-live-portfolio --strategy "$S" --push 2>&1 | tee -a "$LOG"
  r="${PIPESTATUS[0]}"; [ "$r" != "0" ] && rc="$r"
done
log "done rc=$rc"
exit "$rc"

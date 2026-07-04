#!/usr/bin/env bash
# Daily "Signal vs Position" audit + push. For each strategy it reads the
# broker-golden held book, re-runs the trader's stateful Ichimoku signal on each
# held name, and pushes the audit to /api/ingest/signal-audit so the desk card
# highlights exits-not-firing + the honest NLV-vs-start P&L (realized churn/costs
# no longer hidden).
#
# Scheduled by com.tradepro.signal-audit-push.plist at 21:55 BST (Mon-Fri) —
# AFTER the 21:30 harvest (fresh closes) + 21:50 today-setups, so the signal is
# evaluated on the latest complete daily bar. Credentials resolve via the usual
# secret chain (env -> AWS SM -> ~/.tradepro/credentials).
set -uo pipefail

export PATH="/opt/homebrew/bin:/opt/anaconda3/bin:$HOME/.local/bin:$HOME/.cargo/bin:/usr/local/bin:$PATH"
UV="$(command -v uv || true)"
[[ -x "$UV" ]] || { echo "FATAL: uv not found"; exit 1; }

PROJECT_DIR="${TRADEPRO_PROJECT_DIR:-/Users/skumar/sourcecode/tradepro/tradepro/strategies}"
STRATEGIES="${TRADEPRO_AUDIT_STRATEGIES:-ichimoku_equity ichimoku_equity_ibkr}"
LOG_DIR="$HOME/.tradepro/logs"; mkdir -p "$LOG_DIR"
LOG="$LOG_DIR/signal-audit-push-$(date -u +%F).log"
log() { echo "[$(date -u '+%Y-%m-%dT%H:%M:%SZ')] $*" | tee -a "$LOG"; }

cd "$PROJECT_DIR" || exit 1
rc=0
for S in $STRATEGIES; do
  log "signal-audit push: strategy=$S"
  "$UV" run tradepro-signal-audit --strategy "$S" --push 2>&1 | tee -a "$LOG"
  r="${PIPESTATUS[0]}"; [ "$r" != "0" ] && rc="$r"
done
log "done rc=$rc"
exit "$rc"

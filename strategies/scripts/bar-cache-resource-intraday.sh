#!/usr/bin/env bash
# Nightly INTRADAY RE-SOURCE — upgrade yfinance bars to IBKR once IBKR is back.
#
# THE PROBLEM THIS SOLVES (16 Aug 2026)
# -------------------------------------
# The routine intraday harvests run the full chain (ibkr_web → ibkr → ig →
# yfinance), which is correct: when IBKR is unavailable a yfinance bar is far
# better than a gap, and the owner's rule allows Yahoo as a VISIBLE fallback.
#
# But a cached partition is NEVER re-fetched. So every yfinance bar written
# during an IBKR outage became permanent, and the store slowly fossilised into
# whatever provider happened to be up when each bar was first needed. Measured
# before this existed: 1-minute bars were 99% yfinance and 5-minute 75%, while
# ibkr_web served those same windows perfectly on demand.
#
# WHY NOT JUST PUT --ibkr-only ON THE SCHEDULED JOBS
# --------------------------------------------------
# Because that trades coverage for provenance: any IBKR hiccup would leave a
# GAP instead of a Yahoo bar, and a gap is worse for every strategy that reads
# the series. Keep the fallback filling holes; fix the provenance afterwards.
#
# HOW
# ---
# Once a night, force-refresh the CURRENT month from IBKR only. Force-refresh
# is what defeats the cache hit; --ibkr-only guarantees this pass can only ever
# write golden bars — if IBKR cannot serve a symbol the existing cached bar is
# kept untouched (the store refuses to replace a partition with fewer rows, and
# never overwrites good data with an empty response).
#
# Scope is deliberately the current month: IBKR caps intraday history at ~30
# days, so older partitions cannot be re-sourced at all and attempting them
# just burns retries.
#
# Runs AFTER the last routine harvest so it doesn't compete for the single IBKR
# OAuth session — that contention was a recurring cause of failures.

set -uo pipefail

PROJECT_DIR="${TRADEPRO_PROJECT_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
LOG_DIR="$HOME/.tradepro/logs"
mkdir -p "$LOG_DIR"
LOG_FILE="$LOG_DIR/bar-cache-resource-intraday-$(date -u +%Y-%m-%d).log"

log() { echo "[$(date -u '+%Y-%m-%dT%H:%M:%SZ')] $*" | tee -a "$LOG_FILE"; }

PY="$PROJECT_DIR/.venv/bin/python"
if [[ ! -x "$PY" ]]; then
    log "FATAL: no venv python at $PY"
    exit 1
fi

# First of the current month → today. Anything older is outside IBKR's
# intraday window and cannot be re-sourced.
FROM_DATE=$(date -u +%Y-%m-01)
TO_DATE=$(date -u +%Y-%m-%d)

# US-listed symbols only. Foreign listings (0700.HK, 6758.T, AIR.PA …) fail
# IBKR contract resolution because we hold them under Yahoo-format tickers —
# a symbol-harmonization gap, tracked separately. Including them here only
# burns ~45s of retries each to fail.
SYMBOLS=$("$PY" - <<'PYEOF'
import os, sys
sys.path.insert(0, os.environ.get("PROJECT_DIR", "."))
try:
    from tradepro_strategies.cli.options_screen import DEFAULT_UNIVERSE as U
except Exception:
    U = []
print(",".join(s for s in U if "." not in s))
PYEOF
)

if [[ -z "$SYMBOLS" ]]; then
    log "no symbols resolved — nothing to do"
    exit 0
fi

for RES in 5m 1m; do
    log "re-sourcing $RES from IBKR: $FROM_DATE → $TO_DATE"
    "$PY" -m tradepro_strategies.cli.bar_cache_harvest \
        --resolution "$RES" --asset us_etf \
        --symbols "$SYMBOLS" \
        --from "$FROM_DATE" --to "$TO_DATE" \
        --ibkr-only --force-refresh --allow-partial \
        >>"$LOG_FILE" 2>&1
    log "$RES done rc=$?"
done

log "intraday re-source complete"

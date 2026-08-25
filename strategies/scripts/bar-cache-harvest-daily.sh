#!/usr/bin/env bash
# Daily 1d bar-cache harvest for the FULL tracked us_etf universe.
#
# Why this exists: the only scheduled harvest was the 1-MINUTE one (12 symbols).
# Nothing refreshed the DAILY (1d) cache for the ~127-symbol equity universe, so
# it went stale (the "0/119 good for today" data-health screen) and the daily
# Ichimoku strategies starved on "NO BARS" → stopped placing orders. This keeps
# the daily cache current so signals keep flowing.
#
# Scheduled by com.tradepro.bar-cache-harvest-daily.plist, Mon–Fri post-close.
set -uo pipefail

export PATH="/opt/homebrew/bin:/opt/anaconda3/bin:$HOME/.local/bin:$HOME/.cargo/bin:/usr/local/bin:$PATH"
UV="$(command -v uv || true)"
[[ -x "$UV" ]] || { echo "FATAL: uv not found"; exit 1; }

PROJECT_DIR="${TRADEPRO_PROJECT_DIR:-/Users/skumar/sourcecode/tradepro/tradepro/strategies}"
API_URL="${TRADEPRO_API_URL:-http://16.60.201.137}"
CACHE_DIR="$HOME/.tradepro/bar_cache/us_etf"
LOG_DIR="$HOME/.tradepro/logs"; mkdir -p "$LOG_DIR"
LOG="$LOG_DIR/bar-cache-harvest-daily-$(date -u +%F).log"
log() { echo "[$(date -u '+%Y-%m-%dT%H:%M:%SZ')] $*" | tee -a "$LOG"; }

# What to harvest = tradepro_strategies.universe.harvest_symbols(), which is the
# ONE definition of it: the committed universe UNION whatever the store already
# holds, both put through the same `_instrument_ok` filter every other screen
# uses.
#
# This used to be derived here, by `ls`-ing the cache directory and
# re-implementing the exclusions in grep. That had two failure modes, and the
# first one was silent for as long as it existed:
#
#   1. A NEW universe member was never harvested. No directory yet, so `ls`
#      could not see it, so it got no daily bars until somebody noticed. The
#      comment that used to live here conceded it: "adding new symbols is a
#      separate seed step".
#   2. The two filters had already drifted. This grep pattern
#      (`^[A-Z0-9.-]+$`) admits a `-USD` crypto pair, which `_instrument_ok`
#      rejects — nobody would have noticed until a crypto dir appeared in the
#      us_etf tree.
#
# It also once turned a mis-nested ~/.tradepro/bar_cache/us_etf/us_etf/ folder
# into a phantom "US_ETF" symbol that every provider correctly 404'd on, marking
# 37+ consecutive daily harvests FAILED while all 179 real symbols were fine.
# The ticker-shape and self-name guards now live in harvest_symbols() so a stray
# directory cannot masquerade as a ticker again, wherever it is called from.
#
# US listings only (owner call 21 Aug 2026): foreign (dot-suffixed) listings fail
# IBKR every run — Yahoo tickers, no off-platform entitlement — and only ever
# produced bronze yfinance rows. That exclusion lives in `_instrument_ok`.
# cd BEFORE the first `uv run`, not after. `uv run` resolves the project from
# the CURRENT working directory, and launchd starts this script from `/` — so
# when the harvest-symbol query below moved above the old `cd`, the very first
# SCHEDULED run after that refactor died on ModuleNotFoundError while the
# hand-run that "verified" it passed, because a hand-run starts in the project
# directory. One night of bars lost. Keep every `uv run` below this line.
cd "$PROJECT_DIR" || { echo "FATAL: cannot cd to $PROJECT_DIR"; exit 1; }

SYMS=$("$UV" run python -c "
from tradepro_strategies.universe import harvest_symbols
print(','.join(harvest_symbols('$CACHE_DIR')))
" 2>>"$LOG")
# Fail LOUD and stop. An empty list here means the universe file is missing or
# the store is gone; harvesting nothing while reporting success is how a lane
# goes quietly dark.
[[ -n "$SYMS" ]] || { log "FATAL: harvest_symbols() returned nothing (universe file missing, or $CACHE_DIR gone)"; exit 1; }
N=$(printf '%s' "$SYMS" | tr ',' '\n' | grep -c .)

# Trailing 10-day window: catches the latest sessions + backfills any small gap
# without re-harvesting decades every night.
FROM=$(date -u -v-10d +%F 2>/dev/null || date -u -d '10 days ago' +%F)
TO=$(date -u +%F)

# Telemetry to EC2 only if reachable (it auto-stops overnight) — bars still land
# locally regardless, which is what the strategies read.
API_ARGS=()
if curl --silent --head --max-time 5 "$API_URL/health" >/dev/null 2>&1; then
    API_ARGS=(--api-base "$API_URL")
    log "EC2 reachable — telemetry on"
else
    log "EC2 unreachable — local harvest only, telemetry skipped"
fi

log "daily 1d harvest (IBKR-primary: ibkr_web→ibkr→ig→yfinance): $N symbols, $FROM → $TO"
# IBKR-PRIMARY (was --no-ibkr / yfinance-only): dropping --no-ibkr makes the
# provider chain ibkr_web→ibkr→ig→yfinance. The IBKR *Web API* (central backend
# endpoint, NOT the local Gateway that used to hang) fills the cache first, so the
# strategy's daily bars are IBKR-GOOD; yfinance only fills names IBKR can't serve
# (flagged BRONZE, never silent). Needs the API reachable for ibkr_web (checked
# above); if EC2 is unreachable it degrades gracefully to yfinance.
exec "$UV" run tradepro-bar-cache-harvest --resolution 1d --asset us_etf \
    --symbols "$SYMS" --from "$FROM" --to "$TO" --allow-partial --verbose \
    "${API_ARGS[@]+"${API_ARGS[@]}"}" >>"$LOG" 2>&1

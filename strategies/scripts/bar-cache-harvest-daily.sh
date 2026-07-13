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

# Universe = every symbol already tracked in the daily cache (keeps the existing
# 127 current; adding new symbols is a separate seed step).
SYMS=$(ls "$CACHE_DIR" 2>/dev/null | tr '\n' ',' | sed 's/,$//')
[[ -n "$SYMS" ]] || { log "FATAL: no symbols in $CACHE_DIR"; exit 1; }
N=$(printf '%s' "$SYMS" | tr ',' '\n' | grep -c .)

# Trailing 10-day window: catches the latest sessions + backfills any small gap
# without re-harvesting decades every night.
FROM=$(date -u -v-10d +%F 2>/dev/null || date -u -d '10 days ago' +%F)
TO=$(date -u +%F)

cd "$PROJECT_DIR" || exit 1

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

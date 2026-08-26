#!/usr/bin/env bash
# Bar-cache daily harvest wrapper.
# Called by com.tradepro.bar-cache-harvest.plist at 21:15 UTC Mon–Fri.
#
# Behaviour:
#   1. Probes $TRADEPRO_API_URL (default http://16.60.201.137) with a HEAD
#      request, retrying up to 3 times (5 s apart).
#   2. If EC2 is reachable   → runs tradepro-bar-cache-harvest with all
#      args passed through, so telemetry/events reach the backend.
#   3. If EC2 is unreachable → runs tradepro-bar-cache-harvest WITHOUT any
#      --api-base flag; bars are written locally, telemetry silently skipped.
#
# Logs:
#   ~/.tradepro/logs/bar-cache-harvest-YYYY-MM-DD.log

set -uo pipefail

PROJECT_DIR="${TRADEPRO_PROJECT_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
LOG_DIR="$HOME/.tradepro/logs"
mkdir -p "$LOG_DIR"

DATESTAMP=$(date -u +%Y-%m-%d)
LOG_FILE="$LOG_DIR/bar-cache-harvest-$DATESTAMP.log"

log() {
    echo "[$(date -u '+%Y-%m-%dT%H:%M:%SZ')] $*" | tee -a "$LOG_FILE"
}

# ---------------------------------------------------------------------------
# Resolve uv robustly — launchd supplies a minimal PATH.
# ---------------------------------------------------------------------------
export PATH="/opt/homebrew/bin:/opt/anaconda3/bin:$HOME/.local/bin:$HOME/.cargo/bin:/usr/local/bin:$PATH"
UV="$(command -v uv || true)"
if [[ ! -x "$UV" ]]; then
    for cand in /opt/anaconda3/bin/uv /opt/homebrew/bin/uv "$HOME/.local/bin/uv" "$HOME/.cargo/bin/uv" /usr/local/bin/uv; do
        if [[ -x "$cand" ]]; then UV="$cand"; break; fi
    done
fi
if [[ ! -x "$UV" ]]; then
    log "FATAL: uv not found on PATH or known locations"
    exit 1
fi

cd "$PROJECT_DIR" || exit 1

# ---------------------------------------------------------------------------
# EC2 reachability probe — up to 3 attempts, 5 s apart.
# ---------------------------------------------------------------------------
API_URL="${TRADEPRO_API_URL:-http://16.60.201.137}"
EC2_REACHABLE=0
MAX_ATTEMPTS=3
RETRY_DELAY=5

for attempt in $(seq 1 $MAX_ATTEMPTS); do
    if curl --silent --head --max-time 5 "$API_URL/health" >/dev/null 2>&1; then
        EC2_REACHABLE=1
        log "EC2 reachable at $API_URL (attempt $attempt)"
        break
    else
        log "EC2 probe attempt $attempt/$MAX_ATTEMPTS failed — waiting ${RETRY_DELAY}s"
        if [[ $attempt -lt $MAX_ATTEMPTS ]]; then
            sleep "$RETRY_DELAY"
        fi
    fi
done

# ---------------------------------------------------------------------------
# Default to a trailing 7-day window when the caller gave no explicit --from.
# Without it the harvest runs "daily mode" for a single session that yfinance
# cannot serve as 1m (Yahoo only allows ~8 days of 1m per request) → 0 bars,
# which were then falsely logged as "bronze ok". A rolling window lets yfinance
# land real recent 1m on every run; IBKR fills deeper history when the gateway
# is reachable. (Deep historical 1m backfill still REQUIRES IBKR.)
# ---------------------------------------------------------------------------
WINDOW_ARGS=()
if [[ "$*" != *"--from"* ]]; then
    FROM_DATE=$(date -u -v-7d +%F 2>/dev/null || date -u -d '7 days ago' +%F)
    TO_DATE=$(date -u +%F)
    WINDOW_ARGS=(--from "$FROM_DATE" --to "$TO_DATE")
    log "no --from given → defaulting to trailing window $FROM_DATE → $TO_DATE"
fi

# ---------------------------------------------------------------------------
# What to harvest = tradepro_strategies.universe.harvest_symbols(), the ONE
# definition of it, shared with bar-cache-harvest-daily.sh.
#
# This block used to `ls` the cache directory and re-implement the exclusions in
# grep. The comment here even claimed it was the "same derivation as
# bar-cache-harvest-daily.sh" — and on 2026-08-25 that stopped being true, when
# the daily lane moved to harvest_symbols() and this one did not. The two
# definitions then drifted exactly as the duplicate-definition failure mode
# predicts: the daily lane ran a bounded 244 symbols while this lane ran 955,
# because `ls` sees every directory a one-off seed ever created and nothing
# bounded it.
#
# What that cost, visible in the run log: "bar-cache-harvest 5m 955 sym →
# 0G/849S/106B/0M". Zero GOLD. 955 symbols could not finish inside the lane's
# 60-minute deadline, so the sweep never reached its own completion line.
#
# harvest_symbols() carries the guards that used to live here as grep: the
# ticker-shape and self-name checks (the 2026-08-08 phantom "US_ETF" incident),
# the US-listings-only rule (owner call 21 Aug), the -USD crypto exclusion, and
# TRADEPRO_HARVEST_MAX_EXTRA, which is the bound the `ls` form never had.
# ---------------------------------------------------------------------------
SYMBOL_ARGS=()
if [[ "$*" != *"--symbols"* ]]; then
    ASSET="us_etf"
    if [[ "$*" == *"--asset "* ]]; then
        ASSET=$(printf '%s\n' "$@" | grep -A1 -- '^--asset$' | tail -1)
    fi
    CACHE_DIR="$HOME/.tradepro/bar_cache/$ASSET"
    # `uv run` resolves its project from the CURRENT directory and launchd
    # starts this script from `/`, so this must run after the cd above.
    SYMS=$(cd "$PROJECT_DIR" && "${UV:-uv}" run python -c "
from tradepro_strategies.universe import harvest_symbols
print(','.join(harvest_symbols('$CACHE_DIR')))
" 2>>"${LOG:-/dev/null}")
    if [[ -n "$SYMS" ]]; then
        N=$(printf '%s' "$SYMS" | tr ',' '\n' | grep -c .)
        SYMBOL_ARGS=(--symbols "$SYMS")
        log "no --symbols given → harvest_symbols() returned $N symbols for $ASSET"
    else
        # Fail LOUD rather than silently falling back to the harvester's
        # 12-mega-cap built-in list, which would look like a working sweep.
        log "FATAL: harvest_symbols() returned nothing (universe file missing, or $CACHE_DIR gone)"
        exit 1
    fi
fi

# ---------------------------------------------------------------------------
# Run harvest.
# ---------------------------------------------------------------------------
if [[ $EC2_REACHABLE -eq 1 ]]; then

# ---------------------------------------------------------------------------
# WALL-CLOCK GUARD (17 Aug 2026)
#
# A stalled run used to block its OWN SCHEDULE indefinitely: launchd will not
# start a new instance while the previous one is alive, so one hung harvest
# took the 5-minute lane down for NINE HOURS and the Data screen reported
# "has not run for 9h" while a process from the night before was still sitting
# on a socket. The cause that night was IBKR session contention — two jobs
# competing for the single OAuth session, each waiting on the other.
#
# `timeout(1)` is NOT present on stock macOS, so this is done by hand: run in
# background, poll to a deadline, then TERM and finally KILL. A harvest that
# cannot finish in the budget is far less harmful than one that never ends.
run_with_deadline() {
    local budget="${TRADEPRO_HARVEST_MAX_SECONDS:-3600}"
    "$@" &
    local pid=$!
    local waited=0
    while kill -0 "$pid" 2>/dev/null; do
        if [[ "$waited" -ge "$budget" ]]; then
            log "DEADLINE: harvest exceeded ${budget}s — terminating pid $pid so the next scheduled run is not blocked"
            kill -TERM "$pid" 2>/dev/null
            sleep 10
            kill -0 "$pid" 2>/dev/null && { log "escalating to SIGKILL"; kill -9 "$pid" 2>/dev/null; }
            return 124
        fi
        sleep 5
        waited=$((waited + 5))
    done
    wait "$pid"
}

    log "Running tradepro-bar-cache-harvest (with backend telemetry) args: $* ${WINDOW_ARGS[*]:-} ${SYMBOL_ARGS[*]:-}"
    run_with_deadline "$UV" run tradepro-bar-cache-harvest "$@" ${WINDOW_ARGS[@]+"${WINDOW_ARGS[@]}"} ${SYMBOL_ARGS[@]+"${SYMBOL_ARGS[@]}"} >>"$LOG_FILE" 2>&1
else
    log "EC2 unreachable — harvesting locally only, telemetry skipped"
    # Pass all args EXCEPT any existing --api-base / --api-url flags the
    # caller may have included, then run without one so the binary skips
    # the posting step gracefully.
    FILTERED_ARGS=()
    SKIP_NEXT=0
    for arg in "$@"; do
        if [[ $SKIP_NEXT -eq 1 ]]; then
            SKIP_NEXT=0
            continue
        fi
        case "$arg" in
            --api-base|--api-url)
                SKIP_NEXT=1
                ;;
            --api-base=*|--api-url=*)
                # inline value — just drop the whole token
                ;;
            *)
                FILTERED_ARGS+=("$arg")
                ;;
        esac
    done
    run_with_deadline "$UV" run tradepro-bar-cache-harvest "${FILTERED_ARGS[@]}" ${WINDOW_ARGS[@]+"${WINDOW_ARGS[@]}"} ${SYMBOL_ARGS[@]+"${SYMBOL_ARGS[@]}"} >>"$LOG_FILE" 2>&1
fi

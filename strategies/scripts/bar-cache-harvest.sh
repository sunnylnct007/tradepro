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
# is reachable. (Deep historical 1m backfill still REQUIRES IBKR — yahoo can
# only ever serve the trailing ~8 days.)
# ---------------------------------------------------------------------------
WINDOW_ARGS=()
if [[ "$*" != *"--from"* ]]; then
    FROM_DATE=$(date -u -v-7d +%F 2>/dev/null || date -u -d '7 days ago' +%F)
    TO_DATE=$(date -u +%F)
    WINDOW_ARGS=(--from "$FROM_DATE" --to "$TO_DATE")
    log "no --from given → defaulting to trailing window $FROM_DATE → $TO_DATE"
fi

# ---------------------------------------------------------------------------
# Run harvest.
# ---------------------------------------------------------------------------
if [[ $EC2_REACHABLE -eq 1 ]]; then
    log "Running tradepro-bar-cache-harvest (with backend telemetry) args: $* ${WINDOW_ARGS[*]:-}"
    exec "$UV" run tradepro-bar-cache-harvest "$@" ${WINDOW_ARGS[@]+"${WINDOW_ARGS[@]}"} >>"$LOG_FILE" 2>&1
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
    exec "$UV" run tradepro-bar-cache-harvest "${FILTERED_ARGS[@]}" ${WINDOW_ARGS[@]+"${WINDOW_ARGS[@]}"} >>"$LOG_FILE" 2>&1
fi

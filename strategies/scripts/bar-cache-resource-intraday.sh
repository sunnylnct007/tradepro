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

# THE WINDOW IS TRAILING, NOT MONTH-TO-DATE (20 Sep 2026).
#
# Month-to-date grew linearly with the calendar and crossed the fixed 90-min
# budget around day 17-20 of EVERY month: the 1m step was killed nightly from
# 19-28 Aug and again from 17 Sep — late-month 1m data rotted by design, and
# the desk read "Intraday bars (1m): broken". A night's real delta is one day;
# a 7-day trailing window covers it plus a week of outage backlog in ~25 min,
# bounded forever. Saturdays widen to 28 days (IBKR serves ~30) — the full
# sweep that keeps the no-yfinance-fossil guarantee, run when the session has
# no market to serve and a long run can hurt nothing. Trailing windows also
# cross month boundaries, which the old month-start scope never re-sourced.
if [[ "$(date -u +%u)" == "6" || "${TRADEPRO_RESOURCE_FULL_SWEEP:-0}" == "1" ]]; then
    FROM_DATE=$(date -u -v-28d +%Y-%m-%d)
    # 4x the work needs 4x the budget — but only where the caller has not
    # already chosen one. No market is open; a long Saturday run hurts nothing.
    : "${TRADEPRO_RESOURCE_MAX_SECONDS:=21600}"
    : "${TRADEPRO_RESOURCE_MAX_WALL_SECONDS:=28800}"
    export TRADEPRO_RESOURCE_MAX_SECONDS TRADEPRO_RESOURCE_MAX_WALL_SECONDS
    log "FULL SWEEP window (Saturday/forced): 28 trailing days, budgets ${TRADEPRO_RESOURCE_MAX_SECONDS}s awake / ${TRADEPRO_RESOURCE_MAX_WALL_SECONDS}s wall"
else
    FROM_DATE=$(date -u -v-7d +%Y-%m-%d)
fi
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

# DEADLINE. The wall-clock guard added to bar-cache-harvest.sh does NOT cover
# this script — it invokes the module directly. Consequence, found 18 Aug: the
# 22:30 re-source was still running FOURTEEN HOURS later at 12:30 the next day,
# holding the single IBKR OAuth session straight through the following market
# open. A background job with no deadline is not patient, it is stuck.
# THREE LIMITS, because one was defeated by sleep (measured, not guessed).
# `waited` ticks only while the Mac is AWAKE — a 22:00 run that slept overnight
# resumed on wake with its budget barely touched and held the single IBKR OAuth
# session until 11:18 (26 Aug) and even 20:08 (28 Aug): straight through the
# trading day, the exact thing this guard exists to prevent.
#   1. awake budget  — bounds actual work (unchanged)
#   2. wall deadline — a sleep gap cannot extend a run past ~4h of real time
#   3. RTH cutoff    — belt-and-braces: a weekday run still alive at 13:15Z is
#      killed BEFORE the US open needs the session, whatever the clocks say
run_bounded() {
    local budget="${TRADEPRO_RESOURCE_MAX_SECONDS:-5400}"
    local wall_budget="${TRADEPRO_RESOURCE_MAX_WALL_SECONDS:-14400}"
    local start_epoch; start_epoch=$(date -u +%s)
    "$@" >>"$LOG_FILE" 2>&1 &
    local pid=$! waited=0
    while kill -0 "$pid" 2>/dev/null; do
        local reason=""
        if [[ "$waited" -ge "$budget" ]]; then
            reason="awake budget ${budget}s exceeded"
        elif [[ $(( $(date -u +%s) - start_epoch )) -ge "$wall_budget" ]]; then
            reason="wall clock ${wall_budget}s exceeded (the Mac likely slept mid-run)"
        else
            local dow hm
            dow=$(date -u +%u); hm=$(date -u +%H%M)
            if [[ "$dow" -le 5 && $((10#$hm)) -ge 1315 && $((10#$hm)) -le 2005 ]]; then
                reason="US session is OPEN (weekday ${hm}Z)"
            fi
        fi
        if [[ -n "$reason" ]]; then
            log "DEADLINE: $reason — terminating pid $pid so it cannot hold the IBKR session into the next session"
            kill -TERM "$pid" 2>/dev/null; sleep 10
            kill -0 "$pid" 2>/dev/null && kill -9 "$pid" 2>/dev/null
            return 124
        fi
        sleep 10; waited=$((waited + 10))
    done
    wait "$pid"
}

# 1m DROPPED — NOTHING READS IT (26 Sep 2026).
#
# Owner: "we dont need to harvest 1m candle if we see its a potential issue".
# It is an issue, and a bigger one than the storage:
#
#     5m done rc=124   <- 4-hour wall clock EXCEEDED
#     1m done rc=124   <- 4-hour wall clock EXCEEDED
#
# Both halves time out, so this lane holds an IBKR session for 8+ hours a
# night. That is the resource this desk can least afford to waste: we have ONE
# market-data session, and over its subscription cap IBKR serves EMPTY FIELDS
# rather than an error — the "flaky feed" that cost days of diagnosis was us
# oversubscribing ourselves.
#
# And 1m has no consumer. intraday-engine, intraday-enqueue and
# paper-intraday-flat are all unloaded; intraday_flat is retired with -3,019
# realised. The live sleeves are daily. 3.4M rows across 169 symbols were being
# re-sourced nightly, timing out, and read by nothing.
#
# 5m STAYS: it is the input to the spike-fade study the owner wants for
# intraday shorts, and it is scoped to candidate names rather than the universe.
#
# To bring 1m back, add it here — and give it a consumer first.
for RES in 5m; do
    log "re-sourcing $RES from IBKR: $FROM_DATE → $TO_DATE"
    run_bounded "$PY" -m tradepro_strategies.cli.bar_cache_harvest \
        --resolution "$RES" --asset us_etf \
        --symbols "$SYMBOLS" \
        --from "$FROM_DATE" --to "$TO_DATE" \
        --ibkr-only --force-refresh --allow-partial
    log "$RES done rc=$?"
done

log "intraday re-source complete"

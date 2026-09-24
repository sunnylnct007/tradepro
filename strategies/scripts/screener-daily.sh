#!/usr/bin/env bash
# Daily wheel + swing screener run — fires DURING US market hours so IBKR's live
# IV / options feed is warm. (The /screener/run market-closed guard SKIPS a cold
# feed to avoid emailing a misleading "0 candidates", which is exactly why a
# manual after-hours click sends no email.) Emails top-5 wheel (put-selling) +
# top-5 swing to the screener recipient. Mon-Fri.
#
# Scheduled by com.tradepro.screener-daily.plist at 16:00 London ≈ 11:00 ET
# (London is a steady ~5h ahead of US Eastern, so this stays mid-session
# year-round). Idempotent: re-running just re-screens + re-emails.
set -uo pipefail

LOG_DIR="$HOME/.tradepro/logs"; mkdir -p "$LOG_DIR"
LOG="$LOG_DIR/screener-daily-$(date -u +%F).log"
log() { echo "[$(date -u '+%Y-%m-%dT%H:%M:%SZ')] $*" | tee -a "$LOG"; }

CRED="$HOME/.tradepro/credentials"
BASE="$(python3 -c "import json;print(json.load(open('$CRED'))['api_base_url'])" 2>/dev/null || true)"
TOKEN="$(python3 -c "import json;print(json.load(open('$CRED'))['api_token'])" 2>/dev/null || true)"
[[ -n "$BASE" && -n "$TOKEN" ]] || { log "FATAL: no api_base_url/api_token in $CRED"; exit 1; }

log "screener run -> ${BASE%/}/api/screener/run"
RESP="$(curl -s -m 590 -X POST "${BASE%/}/api/screener/run" \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" -d '{}')"
# Surface ok / skipped(reason) / candidate counts in the log so a missing email
# is diagnosable without guessing (market-closed skip vs a real 0-candidate day).
log "response: ${RESP:0:600}"

# ── THE CANONICAL WHEEL (Phase 1, 1 Sep 2026) ───────────────────────────────
#
# `screener/daily_run.py`'s wheel half is now OFF by default: there were two
# things called "wheel" and they disagreed on the same afternoon — 21 eligible
# from the gate-based screen against 0 from the score-based one, on different
# universes, different logic and different data. Owner: "we want a coherant and
# trustworthy data and not scattered data ... not 2 diff emails".
#
# The canonical wheel is tradepro-options-screen. Its launchd agent was retired
# in the 22 Aug desk cut, so without this line nothing runs it on a schedule and
# its change-detected email never fires. It runs HERE, in the same mid-session
# slot, for the same reason that slot exists: the chain and IV need to be warm.
#
# It emails only when the ELIGIBLE SET CHANGES, so this cannot spam — and a
# no-change day correctly produces silence rather than a daily false "0".
log "canonical wheel -> tradepro-options-screen"
# Resolve the strategies dir from THIS script's location (scripts/ -> ..), the
# same way options-screen.sh does. Not inherited: this script never defined it.
STRAT_DIR="${TRADEPRO_STRATEGIES_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
export PATH="/opt/homebrew/bin:/opt/anaconda3/bin:$HOME/.local/bin:$HOME/.cargo/bin:/usr/local/bin:$PATH"
if ( cd "$STRAT_DIR" && uv run tradepro-options-screen >>"$LOG" 2>&1 ); then
  log "wheel screen: ok"
else
  log "wheel screen: FAILED (exit $?) — see $LOG"
fi

# ── ONE EMAIL (Phase 5, 1 Sep 2026) ─────────────────────────────────────────
#
# Owner: "not 2 diff emails", "as user i dont have to think many screens".
#
# Four senders used to mail this account on different schedules with different
# universes — two of them both called "wheel", reporting 21 eligible and 0
# candidates on the same afternoon. The wheel and swing senders are now off by
# default; this is the single digest that replaces them.
#
# It runs LAST, after the screens above have published, so it reads today's
# artifacts rather than yesterday's. It knows nothing about any strategy's
# private shape — it reads the common record, so a fifth strategy costs nothing.
log "candidates digest -> tradepro-candidates-digest"
if ( cd "$STRAT_DIR" && uv run tradepro-candidates-digest >>"$LOG" 2>&1 ); then
  log "candidates digest: ok"
else
  log "candidates digest: FAILED (exit $?) — see $LOG"
fi

# SIGNAL WATCH IS NOT RUN HERE — it has its own lane (24 Sep 2026).
#
# This step called `tradepro-signal-watch`, an entry point that no longer
# exists: the module became cli/trade_alerts.py and took the launchd agent
# com.tradepro.trade-alerts with it. So every run of this script has been
# failing that step and logging "FAILED" into a file nobody reads, while the
# work itself was being done correctly by the other lane all along.
#
# Two separate faults, and the second is the one worth remembering: a renamed
# entry point left a caller behind, and the caller reported its own failure to
# a log instead of to anything that would be noticed. A step that cannot
# succeed must be DELETED, not left failing quietly — a daily "FAILED" line
# that is already expected is indistinguishable from a real one.

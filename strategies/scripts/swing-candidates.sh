#!/usr/bin/env bash
# Swing-candidate refresh. Runs on a short interval THROUGH the session so the
# list tracks the market as it moves (owner: "a daily list that can refresh
# itself at frequent interval as market progresses").
#
# Reads the bar cache only — NO IBKR market-data calls — so it can never
# compete for the single market-data session the options desk needs. That is
# deliberate: the screen is built on daily bars, which are already harvested.
set -uo pipefail
PROJECT_DIR="${TRADEPRO_PROJECT_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
LOG="$HOME/.tradepro/logs/swing-candidates-$(date -u +%Y-%m-%d).log"
mkdir -p "$(dirname "$LOG")"
PY="$PROJECT_DIR/.venv/bin/python"
[[ -x "$PY" ]] || { echo "[$(date -u +%FT%TZ)] no venv python" >>"$LOG"; exit 1; }
echo "[$(date -u +%FT%TZ)] refresh starting" >>"$LOG"
"$PY" -m tradepro_strategies.cli.swing_candidates --universe swing --push >>"$LOG" 2>&1
echo "[$(date -u +%FT%TZ)] refresh done rc=$?" >>"$LOG"

# Forward-test scorecard rides the same cadence — it reads the OMS order
# record, so it is only ever as fresh as the last placement attempt.
# Deliberately AFTER the screen refresh and guarded with `|| true`: a missing
# scorecard is a gap, a broken screen is an outage, and the screen must not
# inherit this one's failures.
echo "[$(date -u +%FT%TZ)] scorecard starting" >>"$LOG"
"$PY" -m tradepro_strategies.cli.forward_scorecard \
    --strategy mean_reversion_swing_ibkr --push >>"$LOG" 2>&1 || true
echo "[$(date -u +%FT%TZ)] scorecard done rc=$?" >>"$LOG"

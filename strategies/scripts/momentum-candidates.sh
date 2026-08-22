#!/usr/bin/env bash
# Momentum-candidate refresh. Runs ONCE a day, after the daily harvest — on the same
# cadence as Swing, staggered 15 minutes later.
#
# Why not intraday: the entry condition is "the close came back to the 10-day
# average" on a SETTLED bar. That value cannot change until the session closes
# and the harvest lands, so a 20-minute cadence would re-emit an identical list
# 20 times an hour and burn the appearance of freshness on a stale answer.
#
# Reads the bar cache only — NO IBKR market-data calls — so it can never
# compete for the single market-data session the options desk needs. That is
# deliberate: the screen is built on daily bars, which are already harvested.
set -uo pipefail
PROJECT_DIR="${TRADEPRO_PROJECT_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
LOG="$HOME/.tradepro/logs/momentum-candidates-$(date -u +%Y-%m-%d).log"
mkdir -p "$(dirname "$LOG")"
PY="$PROJECT_DIR/.venv/bin/python"
[[ -x "$PY" ]] || { echo "[$(date -u +%FT%TZ)] no venv python" >>"$LOG"; exit 1; }
echo "[$(date -u +%FT%TZ)] refresh starting" >>"$LOG"
"$PY" -m tradepro_strategies.cli.momentum_candidates --universe momentum --push >>"$LOG" 2>&1
echo "[$(date -u +%FT%TZ)] refresh done rc=$?" >>"$LOG"

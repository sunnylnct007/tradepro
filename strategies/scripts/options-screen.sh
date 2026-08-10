#!/usr/bin/env bash
# Wheel candidate screen — runs tradepro-options-screen (OAuth-only: chain via
# G3, IV via the Web-API quote snapshot + our own options_iv_daily dataset,
# regime via bar cache; NO local Gateway dependency). Fires DURING US market
# hours so the option chain + IV snapshot are warm, Mon-Fri.
#
# Scheduled by com.tradepro.options-screen.plist at 15:45 + 19:30 London
# (≈ 10:45 + 14:30 ET — one late-morning screen, one refresh into the close).
# Each run also upserts today's IV row per symbol, so the IV-Rank window
# matures a day per trading day. Idempotent: re-running re-screens + re-pushes.
set -uo pipefail

PROJECT_DIR="${TRADEPRO_PROJECT_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
LOG_DIR="$HOME/.tradepro/logs"; mkdir -p "$LOG_DIR"
LOG="$LOG_DIR/options-screen-$(date -u +%F).log"
log() { echo "[$(date -u '+%Y-%m-%dT%H:%M:%SZ')] $*" | tee -a "$LOG"; }

export PATH="/opt/homebrew/bin:/opt/anaconda3/bin:$HOME/.local/bin:$HOME/.cargo/bin:/usr/local/bin:$PATH"

# Wheel capital sizing (owner 2026-08-09: "I can place something of 25K as
# well" — the old £10k/pos cap was the ORIGINAL £12k-pot default and was
# blocking mid-priced quality names on notional). Per-position and total
# deploy raised to £25k; pot £30k headroom; max 2 concurrent positions
# unchanged. These only gate the SCREEN's eligibility labels — no real
# orders are placed from here. Edit here (or the future UI knob) to resize.
export TRADEPRO_WHEEL_POT_GBP=30000
export TRADEPRO_WHEEL_MAX_DEPLOY_GBP=25000
export TRADEPRO_WHEEL_PER_POSITION_GBP=25000
export TRADEPRO_WHEEL_MAX_POSITIONS=2
# Vega-edge bridge threshold — OWNER DECISION 10 Aug 2026 ("0.96 might be
# fine"): 0.95 instead of the 1.00 default. Slightly under-paying premium is
# acceptable to the owner in exchange for participation; revisit with the
# paper-ledger scoreboard after a few completed cycles.
export TRADEPRO_WHEEL_IV_HV_MIN=0.95
UV="$(command -v uv || true)"
[[ -x "$UV" ]] || { log "FATAL: uv not found"; exit 1; }

cd "$PROJECT_DIR" || exit 1
log "options screen starting (project $PROJECT_DIR)"
"$UV" run tradepro-options-screen >>"$LOG" 2>&1
rc=$?
log "options screen finished rc=$rc"
exit $rc

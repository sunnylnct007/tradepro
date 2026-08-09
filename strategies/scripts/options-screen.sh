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
UV="$(command -v uv || true)"
[[ -x "$UV" ]] || { log "FATAL: uv not found"; exit 1; }

cd "$PROJECT_DIR" || exit 1
log "options screen starting (project $PROJECT_DIR)"
"$UV" run tradepro-options-screen >>"$LOG" 2>&1
rc=$?
log "options screen finished rc=$rc"
exit $rc

#!/usr/bin/env bash
# Daily catalyst extractor sweep — feeds the C-1 registry from the
# C-2 news extractor across the trader's effective universe
# (positions + watchlists + trader large_50 + baseline ETFs/FX).
#
# Designed to run daily at 06:00 UTC via launchd job
# com.tradepro.catalysts-daily-sweep.plist. Can be triggered manually:
#
#   bash strategies/scripts/catalysts-daily-sweep.sh
#   launchctl start com.tradepro.catalysts-daily-sweep
#
# What this does:
#   For each symbol in the assembled universe, fetches recent Yahoo
#   headlines, runs the keyword extractor, and POSTs every detected
#   dated catalyst to /api/catalysts/ with source="extractor.news".
#   The endpoint UPSERTs idempotently — same headline never duplicates.
#
# Logs append to ~/.tradepro/logs/catalysts-daily-sweep-YYYY-MM-DD.log
#
# Positions list — until the .NET /api/positions/all endpoint
# aggregates IBKR + T212 + IG into one source, paste your IBKR
# positions here (the symbols you actually hold). The cron picks them
# up automatically as the highest-priority slice of the universe.
#
# Override examples:
#   POSITIONS="APLD,BABA,EC,MRVL" bash catalysts-daily-sweep.sh
#   WATCHLISTS="NVDA,AMD" bash catalysts-daily-sweep.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
LOG_DIR="${HOME}/.tradepro/logs"
mkdir -p "$LOG_DIR"
LOG="${LOG_DIR}/catalysts-daily-sweep-$(date -u +%Y-%m-%d).log"

log() { echo "$(date -u +%FT%TZ)  $*" | tee -a "$LOG"; }

# Resolve `uv` from common install paths.
resolve_uv() {
  for c in \
      "$(command -v uv 2>/dev/null)" \
      "$HOME/.cargo/bin/uv" \
      "$HOME/.local/bin/uv" \
      "/opt/homebrew/bin/uv" \
      "/opt/anaconda3/bin/uv" \
      "/usr/local/bin/uv"; do
    if [[ -x "$c" ]]; then echo "$c"; return 0; fi
  done
  return 1
}

UV_BIN="$(resolve_uv || true)"
if [[ -z "${UV_BIN}" ]]; then
  log "ERROR: uv binary not found on PATH or common install dirs"
  exit 1
fi

# Operator-overridable seeds. Empty defaults are fine — the assembler
# still produces a non-empty universe via the trader_universe +
# baselines.
POSITIONS="${POSITIONS:-}"
WATCHLISTS="${WATCHLISTS:-}"
EXTRA="${EXTRA:-}"

log "catalysts-daily-sweep start"
log "  positions=${POSITIONS:-(none)}"
log "  watchlists=${WATCHLISTS:-(none)}"
log "  extra=${EXTRA:-(none)}"

cd "$PROJECT_DIR"

# Trap the exit so we always log completion.
trap 'log "catalysts-daily-sweep exit code $?"' EXIT

"$UV_BIN" run tradepro-catalysts-daily-sweep \
  --positions "$POSITIONS" \
  --watchlists "$WATCHLISTS" \
  --extra "$EXTRA" \
  2>&1 | tee -a "$LOG"

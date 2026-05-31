#!/usr/bin/env bash
# Weekly EPS snapshot — record forward-EPS estimates for every stock
# watchlist so COMPASS has 90-day revision history before Monday's open.
#
# Designed to run every Sunday at 20:00 UTC via the launchd job
# com.tradepro.eps-snapshot.plist.  Can also be triggered manually:
#
#   bash strategies/scripts/eps-snapshot.sh
#   launchctl start com.tradepro.eps-snapshot     # one-shot manual trigger
#
# What this does:
#   For each stock watchlist, calls `tradepro-refresh --eps-snapshot`
#   which appends today's forwardEps to ~/.tradepro/eps_snapshots/<SYM>.json.
#   ETFs and symbols without analyst coverage are skipped silently.
#   Same-day duplicate entries are deduplicated in the tracker itself.
#
# Logs are appended to ~/.tradepro/logs/eps-snapshot-YYYY-MM-DD.log
# so you can check what ran last Sunday with:
#   tail -100 ~/.tradepro/logs/eps-snapshot-$(date -u +%Y-%m-%d).log

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
LOG_DIR="${HOME}/.tradepro/logs"
mkdir -p "$LOG_DIR"
LOG="${LOG_DIR}/eps-snapshot-$(date -u +%Y-%m-%d).log"

log() { echo "$(date -u +%FT%TZ)  $*" | tee -a "$LOG"; }

# Resolve `uv` from multiple common install paths.
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
  echo ""
}

UV="$(resolve_uv)"
if [[ -z "$UV" ]]; then
  log "ERROR: uv not found. Install via: curl -LsSf https://astral.sh/uv/install.sh | sh"
  exit 1
fi

cd "$PROJECT_DIR"

log "===== EPS snapshot run started ====="
log "Project: $PROJECT_DIR"
log "uv:      $UV"

# Watchlists that contain single stocks. ETFs are safe to include but
# forwardEps will be None → skipped gracefully, just wastes a call.
STOCK_WATCHLISTS=(
  "us_megacap_sample"
  "us_sp100_sample"
  "us_semis"
  "us_growth_tech"
  "asia_majors"
  "europe_majors"
)

ERRORS=0

for wl in "${STOCK_WATCHLISTS[@]}"; do
  log "--- watchlist: $wl ---"
  if ! "$UV" run tradepro-refresh \
        --watchlist "$wl" \
        --provider yahoo \
        --interval 1d \
        --eps-snapshot \
        2>&1 | tee -a "$LOG"; then
    log "WARN: $wl completed with errors (continuing)"
    ERRORS=$(( ERRORS + 1 ))
  fi
done

log "===== EPS snapshot run complete. Watchlists with errors: $ERRORS ====="
[[ "$ERRORS" -gt 0 ]] && exit 1 || exit 0

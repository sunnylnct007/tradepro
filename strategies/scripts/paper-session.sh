#!/usr/bin/env bash
# Daily paper-trading hook — fires `tradepro-paper` once per symbol
# in TRADEPRO_PAPER_SYMBOLS using today's date. Invoked by launchd
# (~/Library/LaunchAgents/com.tradepro.paper.plist) at 14:30 UTC.
#
# Placement mode (auto vs manual) is NOT passed on the CLI — the
# engine reads it from /api/settings each run, so the user's UI
# toggle controls behaviour without editing this script.
#
# Logs:
#   ~/.tradepro/logs/paper-YYYY-MM-DD.log

set -uo pipefail

PROJECT_DIR="${TRADEPRO_PROJECT_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
LOG_DIR="$HOME/.tradepro/logs"
mkdir -p "$LOG_DIR"
LOG_FILE="$LOG_DIR/paper-$(date -u +%Y-%m-%d).log"
SYMBOLS="${TRADEPRO_PAPER_SYMBOLS:-AAPL}"
BROKER="${TRADEPRO_PAPER_BROKER:-t212}"
STRATEGY_ID="${TRADEPRO_PAPER_STRATEGY_ID:-orb_default}"
SESSION_DATE=$(date -u +%Y-%m-%d)

cd "$PROJECT_DIR" || exit 1

# Resolve uv reliably — launchd starts with a minimal PATH. Same
# probe-list as refresh.sh and email-digest.sh.
UV=""
for candidate in \
    "$(command -v uv 2>/dev/null)" \
    /opt/homebrew/bin/uv \
    /usr/local/bin/uv \
    /opt/anaconda3/bin/uv \
    "$HOME/.local/bin/uv" \
    "$HOME/.cargo/bin/uv"; do
  if [[ -n "$candidate" && -x "$candidate" ]]; then
    UV="$candidate"
    break
  fi
done
if [[ -z "$UV" ]]; then
  echo "tradepro-paper: no uv binary found on disk" >&2
  exit 127
fi

{
  echo "=== tradepro-paper auto-run at $(date -u +%Y-%m-%dT%H:%M:%SZ) ==="
  echo "session date: $SESSION_DATE"
  echo "symbols:      $SYMBOLS"
  echo "broker:       $BROKER"
  echo "strategy:     $STRATEGY_ID"
  echo "uv:           $UV"
  echo "(--placement-mode omitted; engine reads /api/settings)"
  echo "---"
  IFS=',' read -ra SYMBOL_ARR <<< "$SYMBOLS"
  overall_rc=0
  for SYMBOL in "${SYMBOL_ARR[@]}"; do
    SYMBOL=$(echo "$SYMBOL" | tr -d '[:space:]')
    [[ -z "$SYMBOL" ]] && continue
    echo
    echo ">>> $SYMBOL @ $(date -u +%H:%M:%S)"
    "$UV" run tradepro-paper \
      --broker "$BROKER" \
      --symbol "$SYMBOL" \
      --date "$SESSION_DATE" \
      --strategy-id "$STRATEGY_ID" \
      --push 2>&1
    rc=$?
    echo "<<< $SYMBOL exit=$rc"
    [[ $rc -ne 0 ]] && overall_rc=$rc
  done
  echo
  echo "=== done · overall_rc=$overall_rc · $(date -u +%H:%M:%SZ) ==="
  exit "$overall_rc"
} >>"$LOG_FILE" 2>&1

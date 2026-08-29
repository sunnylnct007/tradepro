#!/usr/bin/env bash
# Intraday alert for an open index strangle. Reports a MOMENT, never a decision —
# the strangle->straddle conversion is judgement and is deliberately not automated.
# Needs no broker: it reads the strikes from this morning's paper record and the
# index level from the free 5-minute lane.
set -uo pipefail
PROJECT_DIR="${TRADEPRO_PROJECT_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
LOG_DIR="$HOME/.tradepro/logs"; mkdir -p "$LOG_DIR"
LOG="$LOG_DIR/index-strangle-alert-$(date -u +%F).log"
export PATH="/opt/homebrew/bin:/opt/anaconda3/bin:$HOME/.local/bin:$HOME/.cargo/bin:/usr/local/bin:$PATH"
UV="$(command -v uv || true)"
if [[ ! -x "$UV" ]]; then
  for c in /opt/anaconda3/bin/uv /opt/homebrew/bin/uv "$HOME/.local/bin/uv" /usr/local/bin/uv; do
    [[ -x "$c" ]] && { UV="$c"; break; }
  done
fi
[[ -x "$UV" ]] || { echo "FATAL: uv not found" | tee -a "$LOG"; exit 1; }
cd "$PROJECT_DIR" || exit 1
"$UV" run python -m tradepro_strategies.cli.index_strangle_alert --email 2>&1 | tee -a "$LOG"
exit "${PIPESTATUS[0]}"

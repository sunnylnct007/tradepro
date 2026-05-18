#!/usr/bin/env bash
# Daily email digest hook — runs `tradepro-email` against the production
# API and sends the digest to the recipient in ~/.tradepro/email-creds.json.
# Invoked by launchd (~/Library/LaunchAgents/com.tradepro.email-digest.plist)
# at 23:00 UTC, after the 22:30 refresh has populated the compare cache.
#
# Logs:
#   ~/.tradepro/logs/email-YYYY-MM-DD.log

set -uo pipefail

PROJECT_DIR="${TRADEPRO_PROJECT_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
LOG_DIR="$HOME/.tradepro/logs"
mkdir -p "$LOG_DIR"
LOG_FILE="$LOG_DIR/email-$(date -u +%Y-%m-%d).log"

cd "$PROJECT_DIR" || exit 1

# Resolve uv. Same probe-list as refresh.sh — launchd starts with a
# minimal PATH so we can't rely on `command -v` alone.
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
  echo "tradepro-email: no uv binary found on disk" >&2
  exit 127
fi

API_URL="${TRADEPRO_API_URL:-http://localhost:5080}"

{
  echo "=== tradepro-email run at $(date -u +%Y-%m-%dT%H:%M:%SZ) ==="
  echo "API: $API_URL"
  echo "Project: $PROJECT_DIR"
  echo "uv: $UV"
  echo "---"
  "$UV" run tradepro-email --api-base "$API_URL"
  rc=$?
  echo "---"
  echo "exit=$rc"
  exit "$rc"
} >>"$LOG_FILE" 2>&1

#!/usr/bin/env bash
# ── RETIRED 2 Sep 2026 ───────────────────────────────────────────────────────
#
# Owner: "again this email which adds no value". Its launchd agent
# (com.tradepro.email-digest, 23:00 local) has been unloaded and the plist moved
# to ~/Library/LaunchAgents/retired/.
#
# WHY, measured rather than asserted. Across all 14 compare universes this
# digest scored 1,778 rows and bucketed them:
#
#     WAIT   1,596  (90%)
#     AVOID    175
#     BUY        7  (0.4%)
#
# and the verification gate then suppressed the remainder — 196 rows carried
# EARNINGS_UNKNOWN or EARNINGS_UNVERIFIED, and that gate is CORRECT (added after
# a UBER row counted as "verified" with its earnings input unresolved). So the
# mail arrived nightly reading "98 candidates · 0 verified BUY". Not a bug: a
# screen that says WAIT to 90% of everything will keep producing zero.
#
# Its candidate half is superseded by `tradepro-candidates-digest`, which covers
# every strategy with tier, freshness, gates and provenance — and this one spoke
# a SEVENTH vocabulary (BUY/WAIT/AVOID) matching nothing else on the desk.
#
# The one thing it had that existed nowhere else — the holdings unrealised-P&L
# chart — MOVED to the candidates digest rather than dying with it.
#
# Nothing here is deleted: run this script by hand if the compare buckets are
# ever worth mailing again.

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

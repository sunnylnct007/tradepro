#!/usr/bin/env bash
# Daily wheel + swing screener run — fires DURING US market hours so IBKR's live
# IV / options feed is warm. (The /screener/run market-closed guard SKIPS a cold
# feed to avoid emailing a misleading "0 candidates", which is exactly why a
# manual after-hours click sends no email.) Emails top-5 wheel (put-selling) +
# top-5 swing to the screener recipient. Mon-Fri.
#
# Scheduled by com.tradepro.screener-daily.plist at 16:00 London ≈ 11:00 ET
# (London is a steady ~5h ahead of US Eastern, so this stays mid-session
# year-round). Idempotent: re-running just re-screens + re-emails.
set -uo pipefail

LOG_DIR="$HOME/.tradepro/logs"; mkdir -p "$LOG_DIR"
LOG="$LOG_DIR/screener-daily-$(date -u +%F).log"
log() { echo "[$(date -u '+%Y-%m-%dT%H:%M:%SZ')] $*" | tee -a "$LOG"; }

CRED="$HOME/.tradepro/credentials"
BASE="$(python3 -c "import json;print(json.load(open('$CRED'))['api_base_url'])" 2>/dev/null || true)"
TOKEN="$(python3 -c "import json;print(json.load(open('$CRED'))['api_token'])" 2>/dev/null || true)"
[[ -n "$BASE" && -n "$TOKEN" ]] || { log "FATAL: no api_base_url/api_token in $CRED"; exit 1; }

log "screener run -> ${BASE%/}/api/screener/run"
RESP="$(curl -s -m 590 -X POST "${BASE%/}/api/screener/run" \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" -d '{}')"
# Surface ok / skipped(reason) / candidate counts in the log so a missing email
# is diagnosable without guessing (market-closed skip vs a real 0-candidate day).
log "response: ${RESP:0:600}"

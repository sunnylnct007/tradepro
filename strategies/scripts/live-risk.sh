#!/usr/bin/env bash
# Nightly READ of the live account: basis verdicts, exposure, [LIVE] mail.
#
# Read-only by construction (IBKR Flex). This lane never places an order;
# OWNER BASICS 1 (SHARED_CONTEXT, 2026-11-09): TradePro NEVER trades the
# live account.
#
# Scheduled after the daily 1d harvest lands (~22:45 local for 84 names) so
# the card and the mail describe today's close, not yesterday's. Credentials
# come from Secrets Manager (tradepro/ibkr-flex) through the AWS profile the
# plist names; env vars override if set. A lapsed SSO session makes the lane
# FAIL LOUDLY — the stderr log and a non-zero exit, never a stale artifact
# published as fresh.
set -uo pipefail
PROJECT_DIR="${TRADEPRO_PROJECT_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
cd "$PROJECT_DIR" || exit 2
LOG="$HOME/.tradepro/logs/live-risk-$(date -u +%Y-%m-%d).log"
mkdir -p "$(dirname "$LOG")"
log() { echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] $*" | tee -a "$LOG"; }
PY="$PROJECT_DIR/.venv/bin/tradepro-live-risk"
[[ -x "$PY" ]] || { log "FATAL: $PY missing — run 'uv sync' in $PROJECT_DIR"; exit 2; }
log "live-risk starting (profile=${AWS_PROFILE:-<none>})"
if "$PY" --push --email --nav-usd "${TRADEPRO_LIVE_NAV_USD:-245000}" 2>&1 | tee -a "$LOG"; then
  log "live-risk ok"
  # CLEAR IT WHERE THE OWNER LOOKS, TOO. 20 Nov 2026: this run succeeded at
  # 22:50 but the banner from the 21:45 desk check kept saying "Live book
  # BROKEN" (and "Deployed API BROKEN", fixed at 21:47) all weekend — the desk
  # check runs on weekdays only, and this script refreshed it on failure only.
  "$PROJECT_DIR/.venv/bin/tradepro-desk-check" --push --no-mail >>"$LOG" 2>&1 || true
else
  rc=${PIPESTATUS[0]}
  log "live-risk FAILED rc=$rc — the Live card is NOT refreshed; if the log says 'Unable to locate credentials', run: aws sso login --profile ${AWS_PROFILE:-<your profile>}"
  # SAY IT WHERE THE OWNER LOOKS. Until 18 Nov 2026 this line was the only
  # record: the 17 Nov run failed and the card sat two sessions old with
  # nothing on the desk saying so. The desk check grades the live card from
  # this log and publishes the banner now, not at 21:45 tomorrow (no extra mail).
  "$PROJECT_DIR/.venv/bin/tradepro-desk-check" --push --no-mail >>"$LOG" 2>&1 || true
  exit "$rc"
fi

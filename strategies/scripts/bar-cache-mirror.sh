#!/usr/bin/env bash
# Mirror the harvested bar store to S3 (owner 15 Aug 2026: "we need to move
# data into s3 and not rely on mac" — and the standing PG+S3 storage policy).
#
# Credentials come from ~/.tradepro/credentials (dedicated IAM user
# tradepro-bar-mirror, write access limited to this one bucket). Deliberately
# NOT SSO: those tokens expire within hours and this must run unattended.
set -uo pipefail
export PATH="/opt/homebrew/bin:/opt/anaconda3/bin:$HOME/.local/bin:/usr/local/bin:$PATH"
LOG_DIR="$HOME/.tradepro/logs"; mkdir -p "$LOG_DIR"
LOG="$LOG_DIR/bar-cache-mirror-$(date -u +%F).log"
log() { echo "[$(date -u '+%Y-%m-%dT%H:%M:%SZ')] $*" | tee -a "$LOG"; }

CREDS="$HOME/.tradepro/credentials"
[[ -f "$CREDS" ]] || { log "FATAL: $CREDS missing — bar store is NOT backed up"; exit 1; }
AK=$(python3 -c "import json;print(json.load(open('$CREDS')).get('bar-mirror-aws-access-key-id',''))")
SK=$(python3 -c "import json;print(json.load(open('$CREDS')).get('bar-mirror-aws-secret-access-key',''))")
[[ -n "$AK" && -n "$SK" ]] || { log "FATAL: bar-mirror AWS keys absent — bar store is NOT backed up"; exit 1; }

export AWS_ACCESS_KEY_ID="$AK" AWS_SECRET_ACCESS_KEY="$SK" AWS_DEFAULT_REGION=eu-west-2
unset AWS_PROFILE
BUCKET="${TRADEPRO_BAR_CACHE_S3_BUCKET:-tradepro-bar-cache-108703420282}"
SRC="$HOME/.tradepro/bar_cache"

# HOW FAR BEHIND IS S3? (22 Aug 2026) A backlog between the all-day harvest
# and this nightly run is NORMAL, but nothing ever reported its size — so a
# mirror that silently stopped working would look identical to one that had
# nothing to do. Count first, then sync, then confirm it reached zero.
PENDING=$(aws s3 sync "$SRC" "s3://$BUCKET/bar_cache" --dryrun \
        --exclude "*" --include "*.parquet" --include "*.manifest.json" 2>/dev/null \
        | grep -c '^(dryrun) upload:' || true)
log "pending before sync: $PENDING file(s)"

log "syncing $SRC -> s3://$BUCKET/bar_cache"
OUT=$(aws s3 sync "$SRC" "s3://$BUCKET/bar_cache" \
        --exclude "*" --include "*.parquet" --include "*.manifest.json" 2>&1)
RC=$?
UP=$(printf '%s' "$OUT" | grep -c '^upload:' || true)
ERRS=$(printf '%s' "$OUT" | grep -ci 'failed' || true)
# Anything still pending AFTER a successful sync means the mirror is not
# actually protecting the store — that must be loud, not inferred.
LEFT=$(aws s3 sync "$SRC" "s3://$BUCKET/bar_cache" --dryrun \
        --exclude "*" --include "*.parquet" --include "*.manifest.json" 2>/dev/null \
        | grep -c '^(dryrun) upload:' || true)
log "rc=$RC uploaded=$UP failed=$ERRS still_pending=$LEFT"

# STALE PREFIX CHECK: trees that exist in S3 but NOT locally. `aws s3 sync`
# never deletes, so a local reorganisation (22 Aug: us_equity retired, LSE
# ETFs moved to uk_equity) leaves the old layout live in S3 — and a
# disaster-recovery restore would resurrect exactly what we just fixed.
STALE=""
for PFX in $(aws s3 ls "s3://$BUCKET/bar_cache/" 2>/dev/null | awk '/PRE/{print $2}' | tr -d '/'); do
    if [[ ! -d "$SRC/$PFX" ]]; then
        STALE="$STALE $PFX"          # whole tree retired locally
        continue
    fi
    # SYMBOL-level too: the 22 Aug case was 43 symbols (futures, foreign,
    # crypto) left live inside us_etf after being quarantined locally — a
    # tree-level check alone would have missed every one of them.
    for SYM in $(aws s3 ls "s3://$BUCKET/bar_cache/$PFX/" 2>/dev/null | awk '/PRE/{print $2}' | tr -d '/'); do
        [[ -d "$SRC/$PFX/$SYM" ]] || STALE="$STALE $PFX/$SYM"
    done
done
[[ -n "$STALE" ]] && log "STALE IN S3 (present in bucket, absent locally):$STALE"
printf '%s\n' "$OUT" | grep -i 'failed' | head -5 >> "$LOG" || true

# Report to the central run_log so the Data screen can answer "is our data in
# S3, and as of when" instead of anyone assuming.
cd "$(dirname "$0")/.." || exit 1
uv run python - "$RC" "$UP" "$ERRS" "$BUCKET" "$LEFT" "$STALE" <<'PY' 2>>"$LOG" || true
import sys
from datetime import datetime, timezone
from tradepro_strategies.run_log import log_run
from tradepro_strategies.cli.push_to_api import load_credentials
rc, up, errs, bucket = int(sys.argv[1]), sys.argv[2], int(sys.argv[3]), sys.argv[4]
left = int(sys.argv[5]) if len(sys.argv) > 5 and sys.argv[5].strip() else 0
stale = (sys.argv[6] if len(sys.argv) > 6 else "").strip()
base, tok = load_credentials()
status = "ok" if (rc == 0 and errs == 0 and left == 0) else ("partial" if (errs or left) else "fail")
msg = f"s3://{bucket}/bar_cache — {up} uploaded, {errs} failed, {left} STILL PENDING"
if stale:
    msg += f" | stale prefixes in S3 (absent locally): {stale}"
log_run("bar-cache-mirror", "backup", status,
        summary=msg if status == "ok" else None,
        error=None if status == "ok" else msg,
        started=datetime.now(timezone.utc), base=base, token=tok)
PY
exit $RC

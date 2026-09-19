#!/bin/bash
# Mac deploy sync — THE MAC IS A DEPLOY TARGET, NOT A WORKSPACE.
#
# Production launchd lanes run from ~/tradepro-deploy, a clone that only ever
# equals origin/main. This script is the whole deploy mechanism: fetch, and if
# main moved, hard-reset to it and prewarm the uv env so the next lane run
# doesn't pay the resolve cost.
#
# WHY (19 Sep 2026): lanes used to run from the DEVELOPMENT checkout, on
# whatever branch/state it happened to hold. Chain capture died for two nights
# because a plist gained a flag before that checkout had the code. Merging to
# origin/main must BE the deploy, on every surface — AWS already works that
# way; this makes the Mac match.
#
# Quiet when nothing changed. One loud run_log row when a deploy lands or the
# fetch fails, so drift is visible on the cockpit, not in a local file.
set -u
REPO="${TRADEPRO_DEPLOY_ROOT:-$HOME/tradepro-deploy}"
UV=/opt/anaconda3/bin/uv

cd "$REPO" || exit 1
OLD=$(git rev-parse --short=12 HEAD)

post() {  # post <status> <summary>
  ST="$1" SUM="$2" bash -c "cd '$REPO/strategies' && '$UV' run python -" <<'PY' 2>/dev/null
import os
from tradepro_strategies.run_log import log_runs
log_runs([{"process": "mac-deploy", "kind": "deploy",
           "status": os.environ["ST"], "summary": os.environ["SUM"][:400],
           "error": os.environ["SUM"][:400] if os.environ["ST"] == "error" else None}])
PY
}

if ! git fetch origin main --quiet 2>/dev/null; then
  post error "git fetch failed — Mac lanes are running $OLD, drift unknown"
  exit 1
fi
NEW=$(git rev-parse --short=12 origin/main)
[ "$OLD" = "$NEW" ] && exit 0

# A deploy clone must never be dirty; if it somehow is, say so LOUDLY and
# reset anyway — production runs main, full stop.
DIRTY=$(git status --porcelain | head -5)
git reset --hard origin/main --quiet
(cd strategies && "$UV" sync --quiet --extra ibkr 2>/dev/null)

if [ -n "$DIRTY" ]; then
  post error "deployed $OLD -> $NEW but the deploy clone was DIRTY (someone edited it): $DIRTY"
else
  post ok "deployed $OLD -> $NEW"
fi

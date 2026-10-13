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

# PUBLISH THE STRATEGY REGISTRY. Only the Mac can introspect it (the API box
# has no Python), and a deploy is exactly when it can change — so the push
# belongs here rather than in a human's memory.
#
# WHY (13 Oct 2026): the catalogue had NEVER been pushed, so the API could not
# tell a RETIRED sleeve from a STOPPED one and the health panel showed five
# retired strategies as "stale — may not be trading", holding the cockpit red
# every day. Worse in the other direction: a NEWLY registered strategy missing
# from the catalogue reads as "retired", which would hide a real outage. The
# API fails closed on a stale catalogue, and this is what keeps it fresh.
#
# Reported either way. A silent push failure would reintroduce exactly the
# blind spot it exists to close.
CATALOG=$(cd strategies && "$UV" run tradepro-paper-strategies-push 2>&1 | tail -1)
case "$CATALOG" in
  *"ok: 200"*) CATALOG="strategy catalogue pushed" ;;
  *)           CATALOG="STRATEGY CATALOGUE PUSH FAILED: ${CATALOG:-no output} — the API cannot tell retired sleeves from stopped ones until this succeeds" ;;
esac

if [ -n "$DIRTY" ]; then
  post error "deployed $OLD -> $NEW but the deploy clone was DIRTY (someone edited it): $DIRTY · $CATALOG"
elif [ "${CATALOG#STRATEGY CATALOGUE PUSH FAILED}" != "$CATALOG" ]; then
  post error "deployed $OLD -> $NEW but $CATALOG"
else
  post ok "deployed $OLD -> $NEW · $CATALOG"
fi

#!/usr/bin/env bash
# Install (or reinstall) every TradePro launchd job. Idempotent: safe to
# run multiple times — stops + reloads each existing job.
#
# THE COMPARE LANE IS GONE (9 Nov 2026). This installer used to default to a
# "worker" mode whose only job was to loop scripts/refresh.sh, which ran
# tradepro-compare over every ETF universe to feed the Decide screen
# (/desk?view=decide). That screen left the rail on 1 Sep and stayed routed
# by URL; the owner found it by bookmark — "why do we still have this screen
# that shows no value" — while its lane had been running for 23 hours on 54
# symbols x 7 strategies. Screen and lane are deleted together. A lane that
# feeds nothing anyone can open is not idle, it is load on the same data
# lines the harvest needs.
#
#   bash install-launchd.sh                 # base: nothing persistent
#   bash install-launchd.sh --intraday      # + intraday engine
#   bash install-launchd.sh --algo          # + systematic slow-loop + digest
#   bash install-launchd.sh --data-worker   # + trustworthy-data ops queue
#   bash install-launchd.sh --catalysts-sweep
#
# Any previously installed com.tradepro.worker / .refresh / .heartbeat is
# unloaded so a reinstall cannot resurrect the compare loop.

set -euo pipefail

WITH_INTRADAY=0
WITH_EPS_SNAPSHOT=0
WITH_ALGO=0
WITH_DATA_WORKER=0
WITH_CATALYSTS_SWEEP=0
for arg in "$@"; do
  case "$arg" in
    --intraday|--with-intraday) WITH_INTRADAY=1 ;;
    --eps-snapshot|--with-eps-snapshot) WITH_EPS_SNAPSHOT=1 ;;
    # --algo installs the systematic-trading slow-loop + digest plists.
    # Off by default — opt-in so a fresh worker install doesn't start
    # auto-trading without explicit operator intent.
    --algo|--with-algo|--systematic) WITH_ALGO=1 ;;
    # --data-worker installs the trustworthy-data ops queue worker
    # (Phase C). Off by default; ops operators opt in to enable
    # UI-triggered validate / backfill / reload jobs.
    --data-worker|--with-data-worker) WITH_DATA_WORKER=1 ;;
    # --catalysts-sweep installs the daily catalyst-extractor cron
    # (06:00 UTC). Off by default — opt-in because populating the
    # registry across the trader's universe touches yfinance hard.
    --catalysts-sweep|--with-catalysts-sweep) WITH_CATALYSTS_SWEEP=1 ;;
    -h|--help)
      sed -n '1,/^set -eu/p' "$0" | sed 's/^# \?//'
      exit 0
      ;;
  esac
done

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TARGET_DIR="$HOME/Library/LaunchAgents"

mkdir -p "$TARGET_DIR" "$HOME/.tradepro/logs"
chmod +x \
    "$PROJECT_DIR/scripts/email-digest.sh" \
    "$PROJECT_DIR/scripts/paper-session.sh" \
    "$PROJECT_DIR/scripts/intraday-engine.sh" \
    "$PROJECT_DIR/scripts/eps-snapshot.sh" \
    "$PROJECT_DIR/scripts/data-worker.sh" \
    2>/dev/null || true

install_one() {
  local name="$1"
  local template="$PROJECT_DIR/scripts/$name.plist"
  local target="$TARGET_DIR/$name.plist"
  if [[ ! -f "$template" ]]; then
    echo "missing template: $template" >&2
    return 1
  fi

  # Substitute placeholders. Use a sentinel sed delim to avoid clashing
  # with $HOME containing '/'.
  sed \
    -e "s|{{HOME}}|$HOME|g" \
    -e "s|{{PROJECT_DIR}}|$PROJECT_DIR|g" \
    "$template" > "$target"

  # Reload — bootout is the modern launchctl verb, but unload still
  # works for older macOS. Use bootout if available, fall back.
  if launchctl print "gui/$UID/$name" >/dev/null 2>&1; then
    launchctl bootout "gui/$UID" "$target" 2>/dev/null \
      || launchctl unload "$target" 2>/dev/null || true
  fi
  launchctl bootstrap "gui/$UID" "$target" 2>/dev/null \
    || launchctl load "$target"

  echo "Installed: $target"
}

uninstall_one() {
  local name="$1"
  local target="$TARGET_DIR/$name.plist"
  if [[ -f "$target" ]]; then
    launchctl bootout "gui/$UID" "$target" 2>/dev/null \
      || launchctl unload "$target" 2>/dev/null || true
    rm -f "$target"
    echo "Uninstalled: $target (different mode chosen)"
  fi
}

# Legacy compare lanes: always removed, never installed.
uninstall_one "com.tradepro.worker"
uninstall_one "com.tradepro.refresh"
uninstall_one "com.tradepro.heartbeat"

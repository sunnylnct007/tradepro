#!/usr/bin/env bash
# Install (or reinstall) every TradePro launchd job. Idempotent: safe to
# run multiple times — stops + reloads each existing job.
#
# Two install modes:
#
#   bash install-launchd.sh             # default: WORKER mode
#       com.tradepro.worker     persistent, KeepAlive=true,
#                                runs compare every 30 min and
#                                heartbeats every 5 min internally
#
#   bash install-launchd.sh --refresh   # legacy CRON mode
#       com.tradepro.refresh    fires 4×/day at scheduled UTC times
#       com.tradepro.heartbeat  every 15 min Mac → API liveness ping
#
# Worker mode is recommended for active traders — the Mac shows as
# "alive" continuously to the API, the data is fresher, and there's
# only one job to manage. Refresh+heartbeat mode is closer to a
# traditional cron pattern; useful if your laptop sleeps a lot and
# you don't want the worker spinning.
#
# Either way, the OTHER mode's plists are unloaded if present so
# you don't end up running both.

set -euo pipefail

MODE="worker"
for arg in "$@"; do
  case "$arg" in
    --refresh|--cron) MODE="refresh" ;;
    --worker|--persistent) MODE="worker" ;;
    -h|--help)
      sed -n '1,/^set -eu/p' "$0" | sed 's/^# \?//'
      exit 0
      ;;
  esac
done

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TARGET_DIR="$HOME/Library/LaunchAgents"

mkdir -p "$TARGET_DIR" "$HOME/.tradepro/logs"
chmod +x "$PROJECT_DIR/scripts/refresh.sh" "$PROJECT_DIR/scripts/worker.sh" 2>/dev/null || true

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

if [[ "$MODE" == "worker" ]]; then
  uninstall_one "com.tradepro.refresh"
  uninstall_one "com.tradepro.heartbeat"
  install_one "com.tradepro.worker"
  cat <<EOF

Mode: WORKER (persistent, KeepAlive=true)

Cadence:
  Compare cycle every 30 min (env: WORKER_INTERVAL_SECONDS)
  Heartbeat every  5 min     (env: HEARTBEAT_INTERVAL_SECONDS)

Logs:
  ~/.tradepro/logs/worker-<date>.log
  ~/.tradepro/logs/worker-heartbeat-<date>.log

Pause without unloading:
  touch ~/.tradepro/worker.pause   # next cycle skips
  rm    ~/.tradepro/worker.pause   # resumes

Stop completely:
  launchctl bootout "gui/\$UID" ~/Library/LaunchAgents/com.tradepro.worker.plist

Switch to cron mode:
  bash strategies/scripts/install-launchd.sh --refresh
EOF
else
  uninstall_one "com.tradepro.worker"
  install_one "com.tradepro.refresh"
  install_one "com.tradepro.heartbeat"
  cat <<EOF

Mode: REFRESH+HEARTBEAT (cron-style)

Logs:
  ~/.tradepro/logs/refresh-<date>.log     (4×/day compare runs)
  ~/.tradepro/logs/heartbeat-stdout.log   (15-min liveness pings)

Manual test:
  launchctl start com.tradepro.heartbeat
  launchctl start com.tradepro.refresh

Switch to persistent worker:
  bash strategies/scripts/install-launchd.sh --worker
EOF
fi

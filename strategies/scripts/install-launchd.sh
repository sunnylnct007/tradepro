#!/usr/bin/env bash
# Install (or reinstall) every TradePro launchd job. Idempotent: safe to
# run multiple times — stops + reloads each existing job.
#
# Currently installs:
#   com.tradepro.refresh    — daily compare push at 22:30 UTC
#   com.tradepro.heartbeat  — every 15 min Mac → API liveness ping
#
# Usage:
#   bash strategies/scripts/install-launchd.sh

set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TARGET_DIR="$HOME/Library/LaunchAgents"

mkdir -p "$TARGET_DIR" "$HOME/.tradepro/logs"
chmod +x "$PROJECT_DIR/scripts/refresh.sh" 2>/dev/null || true

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

install_one "com.tradepro.refresh"
install_one "com.tradepro.heartbeat"

cat <<EOF

Logs:
  ~/.tradepro/logs/refresh-<date>.log     (daily compare runs)
  ~/.tradepro/logs/heartbeat-stdout.log   (15-min liveness pings)

Manual test:
  launchctl start com.tradepro.heartbeat
  launchctl start com.tradepro.refresh

To uninstall:
  launchctl bootout "gui/\$UID" ~/Library/LaunchAgents/com.tradepro.refresh.plist
  launchctl bootout "gui/\$UID" ~/Library/LaunchAgents/com.tradepro.heartbeat.plist
EOF

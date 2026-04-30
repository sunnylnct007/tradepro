#!/usr/bin/env bash
# Install (or reinstall) the daily ETF refresh launchd job. Idempotent:
# safe to run multiple times. Stops + reloads the existing job.
#
# Usage:
#   bash strategies/scripts/install-launchd.sh

set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TEMPLATE="$PROJECT_DIR/scripts/com.tradepro.refresh.plist"
TARGET_DIR="$HOME/Library/LaunchAgents"
TARGET="$TARGET_DIR/com.tradepro.refresh.plist"

if [[ ! -f "$TEMPLATE" ]]; then
  echo "missing template: $TEMPLATE" >&2
  exit 1
fi

mkdir -p "$TARGET_DIR" "$HOME/.tradepro/logs"
chmod +x "$PROJECT_DIR/scripts/refresh.sh" 2>/dev/null || true

# Substitute placeholders. Use a sentinel sed delim to avoid clashing
# with $HOME containing '/'.
sed \
  -e "s|{{HOME}}|$HOME|g" \
  -e "s|{{PROJECT_DIR}}|$PROJECT_DIR|g" \
  "$TEMPLATE" > "$TARGET"

# Reload (idempotent — launchctl unload is fine if the job isn't loaded).
launchctl unload "$TARGET" 2>/dev/null || true
launchctl load "$TARGET"

echo "Installed: $TARGET"
echo "Will fire daily at 22:30 UTC. Log: ~/.tradepro/logs/refresh-<date>.log"
echo "Manual test: launchctl start com.tradepro.refresh"

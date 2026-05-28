#!/usr/bin/env bash
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PLIST_DIR="$SCRIPT_DIR/launchd"
LAUNCH_AGENTS="$HOME/Library/LaunchAgents"
mkdir -p "$LAUNCH_AGENTS"

PLISTS=(
    "com.tradepro.paper-equity.plist"
    "com.tradepro.paper-fx.plist"
    "com.tradepro.paper-watch.plist"
)

for PLIST in "${PLISTS[@]}"; do
    LABEL="${PLIST%.plist}"
    SRC="$PLIST_DIR/$PLIST"
    DST="$LAUNCH_AGENTS/$PLIST"

    echo "Installing $PLIST..."
    cp "$SRC" "$DST"
    launchctl unload "$DST" 2>/dev/null || true
    launchctl load "$DST"
    echo "  -> loaded $LABEL"
done

echo ""
echo "Done. Scheduled jobs:"
echo "  Equity  — weekdays 13:35 UTC (8:35am ET)"
echo "  FX      — weekdays 22:05 UTC (6:05pm ET)"
echo "  Trigger — every 2 min (polls for UI-triggered sessions)"
echo ""
echo "Logs: /tmp/tradepro-paper-{equity,fx,watch}.log"
echo ""
echo "To uninstall: launchctl unload ~/Library/LaunchAgents/com.tradepro.paper-*.plist"

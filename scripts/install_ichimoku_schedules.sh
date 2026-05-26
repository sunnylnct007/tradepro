#!/usr/bin/env bash
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PLIST_DIR="$SCRIPT_DIR/launchd"
LAUNCH_AGENTS="$HOME/Library/LaunchAgents"
mkdir -p "$LAUNCH_AGENTS"

PLISTS=(
    "com.tradepro.daily-ichimoku-equity.plist"
    "com.tradepro.daily-ichimoku-fx.plist"
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
echo "  Ichimoku Equity — weekdays 13:25 UTC (8:25 AM ET)"
echo "    refresh-universes --push && trigger ichimoku_equity/sp500/manual"
echo "  Ichimoku FX     — weekdays 06:00 UTC (1:00 AM ET / London pre-open)"
echo "    trigger ichimoku_fx_mr/manual (G10 pairs built into strategy)"
echo ""
echo "Logs:"
echo "  /tmp/tradepro-daily-ichimoku-equity.log"
echo "  /tmp/tradepro-daily-ichimoku-fx.log"
echo ""
echo "To uninstall:"
echo "  launchctl unload ~/Library/LaunchAgents/com.tradepro.daily-ichimoku-*.plist"
echo "  rm ~/Library/LaunchAgents/com.tradepro.daily-ichimoku-*.plist"

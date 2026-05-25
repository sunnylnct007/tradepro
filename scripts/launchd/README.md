# TradePro Paper Trading — launchd Schedules

Three launchd agents that automate paper trading sessions on macOS.

## Jobs

| Plist | Schedule | What it does |
|---|---|---|
| `com.tradepro.paper-equity.plist` | Weekdays 13:35 UTC (8:35 AM ET) | Runs `tradepro-paper` with `ichimoku_equity` across 10 US large-cap symbols, $100k capital, 10-sleeve sizing, manual placement |
| `com.tradepro.paper-fx.plist` | Weekdays 22:05 UTC (6:05 PM ET / NY FX evening open) | Runs `tradepro-paper` with `ichimoku_fx_mr` across all G10 pairs (default), $50k capital, 200-bar warmup, manual placement |
| `com.tradepro.paper-watch.plist` | Every 2 minutes | Runs `tradepro-paper-watch --once` to poll the backend for UI-triggered sessions and launch them on demand |

All jobs run via `uv run` inside `/Users/skumar/sourcecode/tradepro/tradepro/strategies/`.

## Install

```bash
cd /Users/skumar/sourcecode/tradepro/tradepro/scripts
bash install_paper_schedules.sh
```

This copies the plists to `~/Library/LaunchAgents/`, unloads any prior version, and loads the new ones.

## Uninstall

```bash
launchctl unload ~/Library/LaunchAgents/com.tradepro.paper-equity.plist
launchctl unload ~/Library/LaunchAgents/com.tradepro.paper-fx.plist
launchctl unload ~/Library/LaunchAgents/com.tradepro.paper-watch.plist
```

Or remove them all at once:

```bash
launchctl unload ~/Library/LaunchAgents/com.tradepro.paper-*.plist
rm ~/Library/LaunchAgents/com.tradepro.paper-*.plist
```

## Logs

```bash
tail -f /tmp/tradepro-paper-equity.log
tail -f /tmp/tradepro-paper-fx.log
tail -f /tmp/tradepro-paper-watch.log
```

## Check Status

```bash
launchctl list | grep tradepro
```

A `0` exit code in the second column means the last run succeeded. Non-zero means the last run exited with an error.

## Notes

- `RunAtLoad` is `false` on all agents — they will not fire immediately on load.
- The equity and FX schedules are hardcoded to UTC. launchd does not adjust for DST automatically.
- The watch daemon uses `StartInterval 120` (every 2 minutes) rather than a calendar schedule.
- Logs are appended (not rotated) to `/tmp/`. Rotate manually or pipe to a logger if needed.

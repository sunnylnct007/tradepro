#!/usr/bin/env bash
# Keep this Mac awake, because the trading daemons only run when it is.
#
# WHY THIS EXISTS. On 25 Aug 2026 the Mac slept from 15:03 to 16:27 BST — the
# market opened at 14:30 — and every launchd lane stopped with it. Swing ran at
# 13:55 and not again until 16:34, a 2h40m gap straight across the open and the
# first two hours of trading. The system log is unambiguous:
#
#   15:03:58  Entering Sleep state due to 'Maintenance Sleep' ... Using Batt
#   16:27:31  Wake from Deep Idle ... due to lid/HID Activity
#
# It woke because the lid was opened, not because anything was scheduled.
#
# The setting behind it: `pmset -g custom` shows `sleep 1` on Battery Power —
# one minute of idle. So unplugged, this machine is asleep sixty seconds after
# you stop touching it, and the strategies stop with it.
#
# WHY IT MATTERS MORE THAN A MISSED SCAN. The signal is computed on the settled
# PRIOR close, so sleeping does not lose a signal — it loses the ENTRY. Swing
# buys at the next open; asleep at the open means no entry, or an entry hours
# late that forward-test gate F3 would score as enormous slippage. Exits are
# worse: a stop check that does not run is a losing position held.
#
# `caffeinate -s` holds a PreventUserIdleSystemSleep assertion for as long as it
# runs. KeepAlive in the plist restarts it if it ever dies, so the assertion is
# continuous.
#
# LIMITS, stated because this is a patch and not the fix:
#   * It cannot WAKE a machine that is already asleep. If the Mac sleeps before
#     this starts, nothing here helps — that needs `sudo pmset repeat
#     wakeorpower MTWRF 13:00:00`, which needs a password and so is the owner's
#     to run.
#   * Closing the lid still sleeps regardless of any assertion.
#   * The real fix is that trading daemons should not depend on a laptop being
#     open. The Swing path has NO LLM dependency (checked: nothing in the
#     strategy, the signal module or the screen touches Ollama), so it can move
#     to the always-on EC2 box; only the enrichment lanes need local Ollama.
#
# To disable: launchctl unload ~/Library/LaunchAgents/com.tradepro.stay-awake.plist
exec /usr/bin/caffeinate -s -i

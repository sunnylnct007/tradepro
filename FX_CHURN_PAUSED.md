# ichimoku_fx_mr — PAUSED 24 Aug 2026, churning. Diagnosis incomplete.

## What was observed

Day one of the Swing forward test, the owner asked whether anything traded.
Swing and ICH-equity produced nothing. **All 16 orders that day came from the
FX sleeve on IG demo**, on a single instrument:

    EURUSD   SELL x9   BUY x7   all FILLED
    one order roughly every 17 minutes, alternating side, all session
    18 "fire-buy"/"fire-sell" decisions logged

The daemon restarts every 900s, so it re-decides ~every 15 minutes. The order
cadence matches the restart cadence.

## What is NOT the cause — checked, not assumed

* **Not the equity sleeve's bug.** That was an in-memory MOO lock wiped by the
  restart. This strategy has no such lock and does not need one.
* **Not a forgotten position.** It seeds from the broker via
  `/api/oms/positions` and trades the DELTA (target − current).
* **Not missing a same-direction guard.** One already exists: if `current` and
  `target` share a sign, `delta = 0`. It acts only on flat→enter or a FLIP.

So the sign of the target is genuinely alternating, which means either the
signal itself flips or the seeded `current` does.

## What is unresolved

The signal is CONTINUOUS and rounded to an integer position
(`target = int(round(signal))`, clamped to ±pos_cap). Observed values
`-2.94 → target -3` and `-1.93 → target -2`. A signal drifting across a
rounding boundary changes the target, and there is **no deadband** — but
magnitude changes alone would be blocked by the same-direction guard, so
rounding cannot explain BUY/SELL alternation on its own.

Recent decisions read `"target position matches current — nothing to do"`,
so the guard IS working at times. **The churn is therefore intermittent, and
that is exactly why the diagnosis is incomplete.**

## Why PAUSED rather than fixed

1. Not confidently diagnosed. This session has repeatedly punished acting on a
   partial diagnosis, and changing an unmonitored strategy on day one of the
   forward test is the worst moment to do it.
2. Owner does not monitor FX ("we not monitoring fx but will be good to get
   that sorted") — so it is not urgent, only worth doing properly.
3. **It pollutes the trade record.** The owner wants the captured fills
   harvested for analysis; 16 churn orders a day on one pair would dominate
   any execution study of a book that otherwise sees a handful of trades.

Paused by renaming the plist — reversible, nothing deleted.

## Also worth fixing when this is picked up

`/tmp/tradepro-paper-fx.log` is **224 MB**. The equity log was 1.5 GB before
this weekend. Neither rotates.

## To resume

    mv ~/Library/LaunchAgents/com.tradepro.paper-fx.plist.PAUSED-2026-08-24 \
       ~/Library/LaunchAgents/com.tradepro.paper-fx.plist
    launchctl load ~/Library/LaunchAgents/com.tradepro.paper-fx.plist

Do NOT resume before instrumenting `signal`, `target` and `current` on every
decision and confirming which of the three is alternating.

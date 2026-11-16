# Rule 7 — breakout-bar volume (pre-registered, 16 Nov 2026)

Written BEFORE any run. Owner, 16 Nov 2026: "are we looking at trading volume
as well to diff a false breakout".

Rule 7 already requires the FIRST 5-minute candle to trade >= 2x its 20-session
average (the stock is "in play"). It does NOT look at the bar that breaks out.
Five other filters were tested on 14 Nov and failed; this is ONE more, so it is
fixed in advance and no other threshold will be tried on this history.

## Definition

Breakout-bar relative volume = the breakout bar's volume / the average volume
of the bar at the SAME time of day over the prior 20 sessions (sessions where
that bar exists).

Split: **high** >= 1.5, **low** < 1.5. Threshold fixed here; never re-tuned.

## Backtest (2 years of 5m, the 29-name forward-record universe, frozen rule 7 at 2x)

Pass if, in BOTH halves of the history, high-volume breakouts average more per
trade than ALL rule 7 trades by at least 0.10 percentage points AND are
positive, and the day-bootstrap 90% CI of the high-volume mean excludes zero
over the full period.

## Forward record

From 16 Nov every rule 7 forward trade carries `breakout_volume_x`, and the
record is split high / low at 1.5. A failed backtest does NOT stop the forward
split: it is free evidence and costs no searching.

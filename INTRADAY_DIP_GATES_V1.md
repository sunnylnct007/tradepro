# Intraday dip-entry v1 — the owner's own strategy, tested

**Status: PRE-REGISTERED. Committed BEFORE the run.**

## The idea, in the owner's words

> "we enter in case it hits low within that trading session and try to exit
> with profit booking. if it doesnt hit that profit booking we can leave it for
> next day. we only do this for stocks that are solid and not penny stocks."

Mechanically:

1. Each morning, rest a BUY limit below the open — a dip you would be happy to
   be filled at.
2. If the session trades down to it, you are long.
3. Rest a profit target. If it is reached, you are out that day.
4. If not, carry the position overnight and keep the target working, up to a
   maximum number of sessions.
5. A stop bounds the loss.

Universe: the 89 names in `strategies/universe/tradeable.json` — the "solid,
not penny stocks" rule already encoded as price and turnover floors.

## What daily bars can and cannot answer

They CAN answer both halves of the entry: the session LOW says whether the
limit filled, the session HIGH says whether the target was reachable.

They CANNOT say which came first. If a session's low hits the limit AND its
high clears the target, the trade only worked if the low came first.

So every result is reported as a BAND, and the gates are graded on the
pessimistic edge:

* **pessimistic** — a same-session low-and-high never counts as a same-day
  win; the position carries to the next session.
* **optimistic** — it always counts.

Anything that only passes on the optimistic edge has not passed.

## Variants swept (declared in advance, not chosen after)

    dip depth   0.5%  1%  2%  3%  5%   below the session open
    target      0.5%  1%  2%  3%  5%   above the fill
    stop        -4%  -8%
    max carry   1, 3, 5, 10 sessions

200 combinations. The sweep being declared up front is what stops the best
cell being reported as if it were the hypothesis.

## Gates

| # | Gate | Threshold | Why |
|---|---|---|---|
| G1 | Fill rate | >= 15% of sessions | Below this it is not a strategy you can run — you would sit unfilled for weeks. |
| G2 | Expectancy per FILLED trade | >= +0.15% (pessimistic) | Must clear a realistic round-trip cost. A positive win rate with negative expectancy is the trap this whole sleeve invites. |
| G3 | Win rate | >= 55% | The idea's appeal is booking small gains often; if it cannot win often it is not this idea. |
| G4 | Same-day exit share | >= 40% of filled trades | The premise is getting in and out. If most positions carry for days it has become the swing sleeve we already have. |
| G5 | **Survives both splits** | G2 holds in all 4 cells | Time split AND symbol split — the rule that caught momentum v3. |
| G6 | Beats buy-and-hold-a-day | expectancy > the mean next-day return of the same universe | Otherwise the dip entry is adding nothing over simply being long. |
| G7 | Sample | >= 5,000 filled trades in the winning cell | |

## Prediction, on record, before running

1. **G1 and G3 pass easily** for shallow dips (0.5-1%). A 1% dip below the
   open happens most days on a liquid name, and a 0.5% target is hit most
   times you are filled.
2. **G2 is where it dies.** I expect the shallow-dip/shallow-target cells to
   show a high win rate and expectancy near zero or negative once the -4%/-8%
   stop is paid — the same shape as the mean-reversion 5-day-SMA variant
   (76% win, +0.06%/trade) that was rejected in August.
3. **G6 is the real test** and I expect it to FAIL for most cells: in a
   rising market, a dip-entry filter mostly removes the days you would have
   made money.
4. Overall: **~25%** that any cell passes all seven. The most likely honest
   outcome is "high win rate, no edge over just being long".

If it fails, that is a real answer to a real question, and the file is kept.

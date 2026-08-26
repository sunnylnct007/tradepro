# Entering at 15:50 instead of the next open. Pre-registered.

**Written BEFORE the run, 26 Aug 2026.** Owner: *"why not use latest signal
from today as well."*

## The prize, and it is already measured

**69% of the daily return accrues OVERNIGHT** — +0.055% close→open against
+0.025% open→close over 530,286 stock-days. Entering at the next open hands
that over:

    entry at the signal CLOSE   64.9% win   +0.854%/trade
    entry at the NEXT OPEN      64.9% win   +0.769%/trade

The 0.085% gap is roughly **10% of the whole edge**.

And the timing problem has a known answer. `EARLY_ENTRY_CANDIDATE.md` measured
that the signal computed at 15:50 on a nearly-complete bar matches the final
settled answer on **99.81% of 3,595 symbol-days — with ZERO false entries.**
The early signal never fired when the close said no; the only cost was 7 missed
trades, and a missed trade costs opportunity while a false entry costs money.

## The question this study answers — and it is NOT the one above

**Filling at 15:50 is not filling at the close.** The +0.854% figure assumes a
fill AT the closing price. A market order sent at 15:50 fills at the 15:50
price, and the difference between that and the close is unmeasured. That
difference is the entire question:

    real gain = overnight drift  −  (close − 15:50 price)

If the last ten minutes systematically drift the same way as the overnight
session, the gain shrinks or vanishes. Nobody has looked.

## What is measured

For every Swing signal where 5-minute history exists, comparing three entries:

| | entry price |
|---|---|
| **A** | the next session's OPEN — what ships today, the control |
| **B** | the 15:50 price on the signal day — the candidate |
| **C** | the signal day's CLOSE — the theoretical ceiling, unreachable live |

Same exits in all three: 20-day mean target, −8% stop, 20-session timeout,
stops filled at `min(stop, open)`.

## Gates — all four required

| # | test |
|---|---|
| **E1** | B beats A on mean return per trade |
| **E2** | the improvement survives a TIME split — both halves |
| **E3** | the improvement survives a SYMBOL split — both cells |
| **E4** | B's worst trade is no more than 2 points worse than A's |

## Predictions, written before the run

**I expect B to beat A, but by LESS than the 0.085% the close-vs-open gap
implies** — because the last ten minutes of a session are part of the same
drift, so some of the overnight gain is already given back by 15:50. My guess
is roughly half: **+0.03% to +0.05% per trade.**

**I expect E2 and E3 to be the hard ones.** An 0.04% effect is small against
per-trade noise of several percent, and this is exactly the size of edge that
looks real on a full sample and evaporates on splitting — which is what
happened to momentum v3, the intraday dip study and both resting-limit studies.

**The honest constraint, stated up front: only about 30 of 244 symbols have
enough 5-minute history to test.** A pass here is SUGGESTIVE, not conclusive,
and must not be shipped as though the whole universe had been measured. If it
passes I will say so with that limit attached, and the recommendation will be
to re-run when the intraday backfill deepens — not to change the live rule on
30 symbols' evidence.

**What would kill it outright:** B losing to A, or the 15:50 price being
systematically worse than the open. That would mean the closing drift already
consumes the overnight edge, and the whole idea is dead rather than small.

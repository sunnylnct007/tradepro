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

---

# RESULT — REJECTED. And the premise was backwards.

| cell | n | A open | B 15:50 | C close | **B − A** |
|---|---|---|---|---|---|
| FULL SAMPLE | 76 | 3.915% | 2.394% | 2.976% | **−1.521%** |
| time 1st half | 38 | 4.532% | 1.303% | 1.950% | −3.229% |
| time 2nd half | 38 | 3.297% | 3.484% | 4.003% | +0.187% |
| symbols even | 40 | 4.005% | 1.462% | 2.429% | −2.543% |
| symbols odd | 36 | 3.814% | 3.429% | 3.584% | −0.385% |

Worst trade: A −8.0%, B −17.3%. **All four gates fail.**

## My prediction was wrong in DIRECTION, not just size

I predicted B would beat A by roughly half the 0.085% close-vs-open gap. It
**loses by 1.5 points**.

## The mechanism, and it inverts the premise

The 0.085% comes from overnight drift measured across **all** stock-days:
+0.055% close→open on average.

**This rule does not enter on an average day.** It enters after a **2.5σ fall**
— and a name that has just dropped that hard is not a random draw from the
overnight distribution. It is a falling knife, and the fall often continues
overnight.

So entering at 15:50 **buys** the overnight move that entering at the next open
**avoids**. Waiting is not a cost. On these entries it is a discount.

The premise of `EARLY_ENTRY_CANDIDATE.md` — *"69% of the daily return accrues
overnight, so entering at the next open hands it over"* — applies a
population-wide statistic to a subpopulation deliberately selected for being
unlike the population.

That is the same error shape as the volume ratio and the seam detector earlier
this week: **a number true of the whole, applied to a part chosen for being
unlike the whole.**

## What this result is NOT

n=76 across 25 symbols, and A's mean here (+3.9%) is three times the rule's own
+1.1% — so this sample is not representative of the strategy either. **A
rejection on 76 trades is weak evidence.**

What it is *not* is evidence of a gain. Nothing here supports entering early,
and the point estimate is strongly negative.

**Status: REJECTED**, with the sample limit attached. Worth re-running when the
5-minute backfill covers more than 25 names — but the mechanism above is a
reason to expect the answer to hold, not a reason to hope it flips.

`EARLY_ENTRY_CANDIDATE.md` should be read with this result attached; its
headline number does not apply to the trades this rule actually takes.

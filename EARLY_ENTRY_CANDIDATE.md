# Trading the CLOSE instead of the next open — candidate, not shipped

Owner, 23 Aug: *"symbols like MU and Sandisk keep on trading and don't
necessarily wait for US market open. how do we deal that. can we start early
on that."*

## Why it matters — the number behind the question

**69% of the daily return accrues OVERNIGHT** (+0.055% close→open against
+0.025% open→close, over 530,286 stock-days). Entering at the NEXT OPEN gives
that away. Measured directly on the Swing rule:

    entry at the signal CLOSE   64.9% win   +0.854%/trade
    entry at the NEXT OPEN      64.9% win   +0.769%/trade

The 0.085% gap is about **10% of the edge**, handed over for the privilege of
waiting.

## The chicken-and-egg, and the way round it

The signal needs the settled close, which you only know after the close. But
the 5-minute lane means the signal can be computed at ~15:50 on a
nearly-complete bar and acted on before the bell.

**Tested: does the 15:50 answer match the final one?**

    3,595 symbol-days · 30 symbols with >=150 sessions of 5m history

    AGREE                     3,588   99.81%
    disagree                      7    0.19%
      of which false entries      0
      of which missed trades      7

**Zero false entries.** The early signal never fired when the close said no.
The only cost is 7 missed trades in 3,595 symbol-days — and a missed trade
costs nothing but opportunity, while a false entry costs money.

## What this is NOT yet

**The gain is not automatically 0.085%.** The +0.854% figure assumes filling
AT the close. A market order at 15:50 fills at the 15:50 price, which is not
the close — so the real gain is the overnight drift MINUS whatever the 15:50
price differs from the close by. That has not been measured and is the whole
question.

Other unknowns before this could ship:
* **Only 30 symbols have >=150 sessions of 5m history.** The other 214 cannot
  be tested this way until the intraday backfill deepens.
* **Execution changes shape.** A market-on-close order or a 15:50 market order
  is not the same risk as a next-open market order; MOC in particular can move
  against you in the closing auction.
* **It would need pre-registered gates.** This was found by testing after the
  fact, which is the shape of finding this project rejects three times a month.

## Status

**CANDIDATE. Not shipped, and deliberately not changed before the forward
test.** Swing goes live tomorrow entering at the next open, which is what the
committed gates describe. Changing the execution the night before would mean
forward-testing something the backtest never measured — the same mistake
avoided with the hold change, which WAS measured first.

Worth pre-registering as the first study after the window closes, or sooner if
the intraday backfill reaches enough symbols to test properly.

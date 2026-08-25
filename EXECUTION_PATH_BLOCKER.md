# The IBKR paper order path has NEVER recorded a real fill. 25 Aug 2026.

Found by asking a question I should have asked before the forward test opened:
**has the order path Swing depends on ever actually worked?**

It has not.

## The record — every IBKR_PAPER order, 10 Jul → 24 Aug

    57 orders    45 CANCELLED    6 SUBMITTED (never resolved)    6 FILLED

And all six "fills":

    2026-08-20  HD    BUY   qty 4    avgFillPrice 0   brokerOrderId None
    2026-08-20  HD    SELL  qty 4    avgFillPrice 0   brokerOrderId 557206760
    2026-08-07  LLY   BUY   qty 1    avgFillPrice 0   brokerOrderId None
    2026-08-07  LLY   SELL  qty 1    avgFillPrice 0   brokerOrderId 922414381
    2026-07-29  KO    BUY   qty 18   avgFillPrice 0   brokerOrderId None
    2026-07-29  KO    SELL  qty 18   avgFillPrice 0   brokerOrderId 2089208171

**Six of six at a fill price of zero. Three of six with no broker order id at
all.** Nine of the 57 orders carry a broker id. Six have sat in SUBMITTED since
7 and 20 August, never acknowledged, never resolved.

For contrast, the T212 path on the same day works exactly as it should:

    2026-08-21  CLF   SELL  qty 32   avgFillPrice 11.57    brokerOrderId 53700405855
    2026-08-20  ABBV  BUY   qty 3    avgFillPrice 260.78   brokerOrderId 53650367564

Real prices, real broker ids. So this is not an OMS problem — it is specific to
the IBKR_PAPER route.

## Why this blocks the forward test rather than merely annoying it

`FORWARD_TEST_GATES_V1.md` states the window is about EXECUTION, not edge:
"do signals fire when they should, do orders reach the broker, do fills land
where the screen said, and does every fill reconcile to a signal."

Against this record:

* **F2 — every fill reconciles to a published signal.** A fill with no broker
  order id cannot be traced to a broker execution. Zero tolerance, and it fails
  on three of six.
* **F3 — entry slippage vs the RECORDED reference price.** Slippage against a
  fill price of 0 is not a measurement. This gate was already amended once on
  23 Aug for being ungradeable; it is ungradeable again, for a different
  reason, and this time the fix is not in the strategy.
* **F4 — every stop-out fills at or below min(stop, open).** Also unmeasurable
  at price 0.

Three of the six gates cannot be computed on this path. **Starting the window
tomorrow would spend twelve weeks measuring nothing** — and would produce a
P&L record that looks like data while being arithmetic on zeros.

## What this is NOT

Not a new regression. `project_ibkr_clone_unconfirmed_fills` recorded the same
shape in an earlier session: the async gateway inbox never writes a broker id,
and the recommended fix was fail-loud labels plus Web API execution. The
position-SEEDING path was moved to the Web API on 24 Aug. **The order path was
not**, and nobody checked whether it had ever confirmed a fill, because ICH's
churn produced enough T212 fills to make the book look alive.

## The order of work, and it is not negotiable

1. Route IBKR paper orders through the Web API, as the seeding path already is.
2. Prove ONE round trip end to end: a BUY that returns a broker order id and a
   non-zero fill price, and a SELL that closes it.
3. Make a fill with price 0 or a missing broker id FAIL LOUD instead of being
   recorded as FILLED. A zero that reconciles is worse than an error.
4. THEN start the twelve weeks.

Until step 2 passes, the forward test cannot begin — not because the strategy
is not ready, but because the instrument that measures it reads zero.

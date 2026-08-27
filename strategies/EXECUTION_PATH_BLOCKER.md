
---

# RESOLVED. 27 Aug 2026.

**The path records real fills. First confirmed round trip, IBKR_PAPER DUP656969:**

    BUY   broker order 1069512750   FILLED 1 KO @ 89.36   exec 00025b45.6a96ea93.01.01
    SELL  broker order 1130405308   FILLED 1 KO @ 89.66

    reconcile: brokerOrders 1, brokerExecutions 2, executionsWithOrderId 2,
               confirmed, blind FALSE

Step 2 of "the order of work" above is satisfied. The twelve weeks can start.

## What it actually was — neither of the two theories in this document

Not the gateway inbox, and not a broken read. **Two parsing/protocol errors on
our side, against a broker that was behaving correctly the entire time.**

**1. `/iserver/account/orders` is PRIMED.** The first call after a session goes
ready returns a placeholder that declares itself:

    call 1  {"orders":[],"snapshot":false}
    call 2  {"orders":[{"orderId":1069512750,"status":"Filled",
                        "filledQuantity":1.0,"avgPrice":"89.36"}],"snapshot":true}

Reconcile called once. Every time. `"snapshot": false` was in every response we
ever received and nothing read it. `/trades` primes identically but is a bare
array with no flag, so unprimed and genuinely-quiet are indistinguishable —
hence the bounded re-ask. (e1ed6e4)

**2. Executions carry `order_id`, as a NUMBER.** ParseTrades looked for
`order_ref`, `orderId`, `ibOrderId`. None appear. So even after the blotter read
worked, every execution parsed with a null order id and the join dropped it —
`executionsWithOrderId: 0` with the execution sitting right there. (209d4aa)

## Why it took two months

The tests. `IBKRTradeOrderIdTest` had a case for each of the three guessed
names, all green, while production dropped the order id on every single
execution. **A test written from a guess confirms the guess, not the
integration.** The file now pins verbatim captured payloads.

And nothing in the system ever showed the RAW broker response. Row counts
cannot separate "IBKR has nothing", "IBKR sent a shape we drop" and "we asked
the wrong question" — and all three were live at once. That ambiguity, not the
underlying bugs, is what cost the weeks.

## Left in place so this is never re-diagnosed from scratch

* `GET /api/integrations/ibkr/diagnose-fills` — RAW bodies of /iserver/accounts,
  /orders and /trades next to the parsed counts.
* `POST /api/integrations/ibkr/bind-account` — bind the account to the live session.
* reconcile returns `ordersError` / `executionsError` + httpStatus
  (httpStatus 0 = the call never completed; 200 with `[]` = the broker really
  said nothing).

## Known caveat

`/api/integrations/ibkr/positions` LAGS — it still read KO 2.0 immediately after
the closing SELL. Executions are authoritative for fills; positions are not.

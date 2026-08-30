# Placing option orders — scope, 30 Aug 2026

Owner asked for post-earnings puts to be paper-traded, not just screened. This is
what that actually takes. Written before any code, because the last three
execution changes shipped green and broke in production.

## What already exists (checked, not assumed)

| piece | state |
|---|---|
| `GetOptionStrikesAsync(conid, month)` | works — strikes for a month |
| `GetOptionContractsAsync(conid, month, strike, right)` | works — resolves ONE option to its conid |
| `PlaceMarketOrderAsync(long conid, ...)` | takes a **conid**, so it can already place an option |
| `PlaceMarketOrderConfirmedAsync` | handles IBKR's reply/confirm loop |
| fill reconcile from executions | fixed 29 Aug, verified unattended in 65s |

**The part I expected to be hard is done.** Placement takes a conid, and the
chain code already resolves option conids for the wheel screen.

## What is missing, and none of it is cosmetic

**1. The OMS cannot represent an option.** `oms_orders.symbol` is a bare TEXT
column — no expiry, strike, right or multiplier. Encoding
`MRVL_20260918_P_195` into `symbol` would work for a day and then break every
join that assumes a ticker: `PositionReconcile.Canonical()`, strategy
attribution, the P&L views, the desk's symbol filter. Needs real columns, with
`symbol` staying the UNDERLYING so existing joins keep working.

**2. A market order is the wrong instrument.** Equities market-order fine.
Options do not — MRVL's 195 put can be 2.10/2.55 wide, and a market order pays
the offer. Needs LIMIT orders: the OMS already supports LMT, the IBKR placement
path is market-only.

**3. Nothing models collateral.** A cash-secured put ties up strike x 100. The
screen computes it (MRVL: $6,663); the OMS has nowhere to put it, so concurrent
puts could commit capital that is not there.

**4. Nothing models assignment.** A short put that goes in-the-money becomes 100
shares at the strike. Today that arrives as a mystery equity position the
reconciler cannot attribute — the exact drift the position reconciler exists to
stop.

**5. Multiplier.** Option P&L is x100. Every existing P&L path assumes 1.

## Staged plan — each stage independently verifiable

**S1 — record intent (no orders).** The screen already publishes candidates
daily from Monday. Add the `signal_*` capture the Swing sleeve got on 29 Aug, so
each candidate is a dated, priced record. *Gate: 10 sessions captured with
spot/strike/DTE/vol.* **This is the forward-test evidence and needs no order
path at all.**

**S2 — OMS understands options.** Migration adding `expiry`, `strike`, `right`,
`multiplier`, `collateral_usd`. *Gate: an option order round-trips through the
OMS, the desk renders it, and no equity path regresses.*

**S3 — limit placement.** Extend the IBKR path to LMT with a price. Place one
probe: a 1-lot at a limit far from the market, then cancel it. *Gate: a real
broker order id, a working order visible in the blotter, a clean cancel.* Same
shape as the KO probe that proved the equity path.

**S4 — one real paper round trip.** Sell one put, buy it back. *Gate: both legs
recorded with real prices and a broker id, reconciled unattended within 90s.*

**S5 — assignment.** Only then, and it needs a deliberate in-the-money test.

## The risk that is different from equities

A long equity position can lose what you put in. **A short put is an obligation.**
Mis-sized it commits capital that is not there; assigned it doubles a position
without asking. The owner already carries this consciously on APLD — that is a
deliberate position, not a template for an automated one.

S2-S5 stay behind the existing `AllowOrders` kill-switch and the PAPER account
only. No live account, no exceptions, until S4 has run clean for a fortnight.

## Honest estimate

S1 lands Monday (it is a capture change). S2 and S3 are a day of careful work
each, most of it verification rather than typing. S4 is a day of waiting on the
market.

**Calling this "paper trading from Monday" would be wrong.** The candidate
RECORD starts Monday; the orders follow as each gate passes.

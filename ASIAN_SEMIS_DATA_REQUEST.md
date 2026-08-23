# Asian semiconductor data — AVAILABLE from IBKR. Verified 23 Aug 2026.

Owner: *"symbols like MU and Sandisk keep on trading and don't necessarily wait
for US market open"* → *"can we harvest them from ibkr or they are not
available"*.

## What was checked, against the live IBKR connection

| instrument | exchange | contract_id | price history |
|---|---|---|---|
| **SK Hynix** | KRX (Korea) | **17382246** | **WORKS** — full OHLCV, 1 year returned |
| TSMC | TWSE (Taiwan) | 37928709 | contract resolves; price history returned an error |
| TSMC ADR (TSM) | NYSE | — | already in the store, 4,184 bars from 2010 |

SK Hynix returned a clean year: 220 sessions of open/high/low/close/volume in
KRW. Two sessions carry a zero close (2025-12-18, 2026-01-02) and must be
filtered.

The TWSE error read "Details currently unavailable… try again later", which is
ambiguous between a missing entitlement and a transient fault. Worth one retry
before concluding Taiwan is unavailable.

## Why this is worth harvesting — the timing is the whole point

The Korean session ends **~06:30 UTC**; the US opens **13:30 UTC**. So SK
Hynix's close on date D is known **seven hours before** the US session on the
same date. That is a genuine information lead, and it is exactly the window in
which 69% of the daily return accrues (measured: +0.055% overnight against
+0.025% intraday, over 530,286 stock-days).

**The ADR does not give you this.** TSM is already in the store, but it trades
on NYSE — it gaps at the US open at the same moment MU does, so it carries the
same information with no lead.

## The test this unlocks, and its likely answer

Two questions, and only the second is tradeable at the open:

1. **SKH move on D → MU's overnight GAP on D.** Probably strong — but if so
   the information is already IN the open price, and a strategy entering at
   the open captures none of it.
2. **SKH move on D → MU's INTRADAY move on D (open→close).** The residual,
   after the market has priced the Asian session. This is the only part
   capturable at the open, and I would expect it to be small.

**Deliberately not tested with hand-copied data.** Pulling 220 numbers out of
an MCP response into a one-off script is precisely the fragile, unreproducible
analysis this project has been repeatedly burned by — and the mean-reversion
v1 harness that no longer exists is the standing example. It gets tested when
the data is harvested into the store, with a committed harness.

## Ask of the data lane

Add **KRX** (and TWSE if the entitlement is real) to the harvest for a short
list — SK Hynix 000660 at minimum, Samsung 005930 and TSMC 2330 if available.
Daily bars are sufficient; the lead comes from the session boundary, not from
intraday resolution.

**Not urgent, and explicitly NOT during the forward-test window** — a new
symbol class touches the store, and G4 moves with population while G5 has 1.1
points of slack. This is a post-window item.

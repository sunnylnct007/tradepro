# SELLOFF TECHNICALS V1 — two short-term technical rules for a volatile tape

Pre-registered 21 Nov 2026, BEFORE any result. Owner: "with market so
volatile now do we have any strategy that can work on technical parameter and
highlight some stocks worth getting into or short selling for a short term".
Honest answer today: no — every tested technical short failed (TECHNICAL_SHORTS_V1)
and buying a sell-off has no edge over the stock's own baseline
(SELLOFF_PLAYBOOK_GATES_V1). These two are the untested leads.

Data: the golden daily store, harvested universe (survivor bias stated, as in
SHADOW_BOOKS_V1), 2006–2026 where bars exist. Entry at the NEXT open after
the signal close; 0.10% round trip; $10,000 a trade; one position per symbol.

## R1 — relative-strength leaders on a sell-off day (LONG)

Signal day: SPY close ≤ −2.0% on the day. Candidate: a stock whose close is
UP on that day (≥ 0%) AND whose 5-session return beats SPY's 5-session return
by ≥ 3 points. Hold 3 sessions, exit at the open of session 4. No stop (the
hold is the bound; stated).

Null: all stocks on the same signal days, same hold.

## R2 — the dip rule BELOW the 200-day (LONG)

The swing entry (close < 20d mean − 2.25σ) taken when close is BELOW its
200-SMA — the half the sigma-band study never tested. Exits as the swing rule:
20d mean (recomputed), −8% stop, 20-session timeout.

Null: the same rule above the 200-SMA on the same dates (the live rule).

## R3 — relative-WEAKNESS laggards on a rally day (SHORT) — the mirror of R1

Signal day: SPY close ≥ +2.0%. Candidate: close DOWN on the day AND 5-session
return trails SPY by ≥ 3 points. Short at the next open, cover at the open of
session 4. Borrow 1%/yr pro-rata.

## Pass marks — fixed here

For each rule, n ≥ 200 trades else "too few". PASS: mean per trade ≥ +0.40%
(R3: ≥ +0.40% on the short side) AND positive in all four two-split cells
(first/second half of time × first/second half of symbols) AND beats its null
by ≥ 0.20 points. FAIL: mean ≤ 0 or beaten by its null. Between: "not enough
to show".

## What a pass earns

A shadow book and a verdict-tagged screen row — not an order. A fail is
recorded and not re-raised.

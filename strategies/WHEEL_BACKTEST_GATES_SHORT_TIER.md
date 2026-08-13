# Short-dated CSP tier (TIER_SHORT) — PRE-REGISTERED gates

**Committed BEFORE the first short-tier run** (13 Aug 2026). Thresholds are
the owner's own draft from `SPEC_OPTIONS_SHORTDATED_AND_EARNINGS_2026_08_12.md`
§4.4, adopted unchanged. Sibling files — `WHEEL_BACKTEST_GATES.md` (v1,
`5817fe2`) and `WHEEL_BACKTEST_GATES_V2.md` (`cb51600`) — stay immutable.

## Why this test exists

TIER_SHORT **already trades on the live desk** (shipped `00a24e6`, 13 Aug) and
has never been backtested. That is the trust gap this closes. It is
independent of the open v2 G4 decision and does not touch it.

## What is simulated

The tier is an *exception path*, not a strategy: it only acts where the
standard 25–50 DTE band is vetoed by an earnings print. So the sim runs the
**v2 wheel** and, at each moment v2 would decline a new put *because of the
earnings veto*, attempts instead:

1. an expiry in **7–24 DTE** (the live tier's band after the ORCL dead-zone
   fix — short tier abuts the standard band by construction),
2. clearing the print by **≥ 3 trading sessions** (XNYS),
3. clearing the **stricter tier gates**: premium ≥ $0.50/share, annualised
   yield ≥ 25%, and the regime gate unchanged (GREEN/YELLOW, no falling
   knife).

If no expiry satisfies all three, the sim stays flat — exactly as the live
screen does. Everything else (universe rule, $25k slice, 1 contract, 5% OTM
strike, costs = 5% haircut + $1.50/leg, idle cash 4%) is identical to v2 so
the two are directly comparable.

## Modelling caveats — declared before the numbers exist

1. **Weekly-expiry availability is assumed, not verified.** We hold no
   historical listing data, so the sim assumes a weekly expiry exists on
   every Friday. For this universe (large, liquid US names) that is true in
   the modern era but it is an assumption, and it makes the tier look
   *slightly* more available than it was.
2. **Strike rule is 5% OTM, not delta ≤ 0.30.** Kept identical to v1/v2 so
   results are comparable. At short DTE and typical vol, 5% OTM sits near
   0.15–0.20 delta — *inside* the live tier's 0.30 cap — so the sim is not
   systematically more aggressive; on very high-vol names it can drift
   slightly past 0.30.
3. **The IV proxy is CONSERVATIVE here, unlike v2.** Real implied vol ramps
   into a print, so genuine premiums for an expiry just before earnings run
   *above* trailing realised vol. Our Black-Scholes-on-realised proxy
   therefore **understates** short-tier income — the opposite direction to
   the post-gap overstatement that flatters v2. A short tier that clears its
   yield gate on this proxy would clear it more easily in reality.
4. Expiry-only assignment and adjusted-close space carry over from v1/v2.

## Gates (spec §4.4, adopted verbatim)

| # | Test | Pass |
|---|------|------|
| S1 | Win rate (put expires OTM, full premium kept) | ≥ 80% |
| S2 | Return on collateral, annualised, net of costs | ≥ 25%/yr |
| S3 | Worst single-position loss | ≤ 2× median premium collected |
| S4 | Assignments with a print inside the holding window | **exactly 0** (correctness) |

S4 is a correctness gate: the tier's entire premise is clearing the event.
Any occurrence invalidates the run rather than scoring it.

Disclosures (reported, never gated): **trade count** (the spec's stated risk —
"this may fire a handful of times a quarter"), the share of earnings-vetoed
opportunities the tier converts into trades, utilisation, and cost drag.

## Expected outcome — recorded before the work

The spec predicts Part A "will probably pass, with trade count the real
risk". I agree on direction and add a sharper concern: **S2 (≥25%/yr on
collateral) is the bar most likely to fail on our conservative proxy**,
because it understates exactly the pre-print premium the tier exists to
harvest. If S2 fails while S1 and S4 pass, the honest reading is "the tier is
sound but unmeasurable at this pricing fidelity" — which is an argument for
the forward option-quote capture, not for tuning the gate.

My prediction: **S1 passes, S4 passes, S2 fails or lands marginal, trade
count is low (single digits per symbol per year).**

## Scope

Passing these gates does NOT close Phase 1 — Phase 1 belongs to
`WHEEL_BACKTEST_GATES_V2.md` and its open G4 decision. This file certifies
only that the short-dated tier is safe to keep trading.

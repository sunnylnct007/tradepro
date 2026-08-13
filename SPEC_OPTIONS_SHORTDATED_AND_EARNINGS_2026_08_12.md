# Spec — Short-Dated CSPs + Earnings Straddle Scanner

**Date:** 2026-08-12
**Repo state at drafting:** `9766af4`
**Status:** specification only. Nothing here ships to the live desk until its
backtest clears pre-registered gates (§4).

---

## 0. Why

Two gaps surfaced from live trading on 2026-08-12.

**Gap 1 — the DTE band and the earnings veto can conflict.** MRVL reported on
27 Aug. The Sep04 expiry (23 DTE, inside the 25–50 band after rounding) held
*through* the print; the Aug21 expiry (9 DTE) cleared it by six days. Only one of
the two gates could be satisfied. The owner chose the earnings veto and sold
**MRVL Aug21'26 200P at $2.90** ($290 gross, ~58.6%/yr on $20k, 9 DTE). The
screen could not have proposed that trade — `dte_min = 25` excludes it.

**Gap 2 — no long-volatility structure exists.** Every screen in the repo sells
premium. There is no scanner for buying it. The owner's stated interest is a
**long ATM straddle around earnings**, exited the day after the print.

These are opposite sides of the same instrument and must not share a gate file.

---

## 1. Part A — short-dated CSP tier

### 1.1 What changes

Add a second, tightly-constrained DTE tier. Do **not** widen the existing band.

```
TIER_STANDARD   dte 25–50   (unchanged, current defaults)
TIER_SHORT      dte  7–21   (new, stricter gates below)
```

`TIER_SHORT` is **not** a general-purpose relaxation. It is admissible only when
it does work the standard tier cannot:

**Admissibility rule (both must hold):**
1. The underlying has a **confirmed** earnings date (from the central
   `earnings_calendar` store — `eventCount ≥ 1`, not merely "no event found"),
   **and**
2. Expiry falls **≥ 3 trading days before** that earnings date.

If earnings is unknown, unconfirmed, or outside the horizon, `TIER_SHORT` is not
available and the standard band applies. This keeps the short tier as an
*earnings-avoidance mechanism*, not a yield-chasing one.

### 1.2 Why short DTE needs stricter gates

Gamma. At 9 DTE, delta moves far faster than at 35 DTE — a candidate that is
0.25 delta on Monday can be 0.60 by Thursday, with no time to repair and no
realistic roll. The premium is compensation for that, not a free lunch.

Proposed `TIER_SHORT` overrides:

| Gate | Standard | Short tier | Rationale |
|---|---|---|---|
| `dte` | 25–50 | **7–21** | the change itself |
| `delta_max` | 0.35 | **0.30** | less time to repair a breach |
| `min_ann_yield_pct` | 8.0 | **25.0** | must be paid properly for gamma |
| `min_premium_usd` | 0.20 | **0.50** | commission + spread is a larger share of a short-dated credit |
| `oi_min` | 250 | **500** | must be able to exit fast, not just enter |
| `spread_max_pct_of_mid` | 0.15 | **0.12** | round-trip cost matters more over 9 days than 35 |
| `iv_rank_min` | 30.0 | **30.0** (unchanged) | the vega edge test does not change |

The MRVL trade clears all of these: 0.28 delta, 58.6%/yr, $2.90 premium,
9 DTE, and the earnings gap satisfied. It is the worked example — the tier
should be tuned so this trade is admissible and marginal ones are not.

### 1.3 Surfacing

- Every short-tier row renders a **`SHORT-DATED`** badge plus the reason:
  *"9 DTE — clears MRVL earnings 27 Aug by 6 days."*
- The why-not column must distinguish *"outside standard DTE band"* from
  *"short tier unavailable: no confirmed earnings date"*. These are different
  states and the second is a data condition, not a market one.
- Standard-tier candidates rank above short-tier ones at equal yield. The short
  tier is an exception path, and the UI ordering should say so.

### 1.4 Explicitly out of scope

0-DTE and weekly-churn selling. The admissibility rule confines the short tier to
earnings avoidance; it is not a licence to sell 7-DTE premium continuously.

---

## 2. Part B — earnings straddle scanner (long volatility)

### 2.1 The structure

Buy an ATM straddle — same strike, both legs long — before an earnings print;
exit the day after. Owner's stated exit is **square off the day after the print,
not hold to expiry**, so expiry choice is a greeks dial, not a holding period.

Directionally agnostic: profits if the underlying moves far enough either way.

### 2.2 The honest problem this scanner must solve

**Buying straddles into earnings is negative expectancy by default.** The market
prices the expected move into the options; after the print, implied vol collapses
and both legs lose extrinsic value. A stock can move 10% and the straddle still
lose, because the volatility premium paid evaporates the moment uncertainty
resolves.

So the scanner's entire job is to find the exception: **cases where the implied
move is cheap relative to what this company actually does on earnings.**

If it cannot measure that, it should return nothing rather than rank by IV.

### 2.3 The core metric

```
implied_move_pct   = ATM straddle mid / spot        (nearest expiry after print)
realised_move_pct  = |close(T+1) / close(T-1) - 1|  per historical print
edge_ratio         = median(realised_move_pct, last N) / implied_move_pct
```

`N ≥ 8` prints. Report the full distribution, not just the median — the p25 and
p75 matter more than the point estimate, because the trade is a bet on the tail.

**Candidate is interesting only when `edge_ratio > 1.0`** — the stock has
historically moved more than the options are currently charging for.

Worked example (MRVL, 2026-08-12, for calibration during build):
spot ~$222, Aug28 ATM straddle ≈ $31 → implied move ≈ **14%**. If MRVL's last 8
prints have a median absolute move of 10%, `edge_ratio ≈ 0.71` and the trade is
**not** a candidate at that price, however attractive the setup feels.

### 2.4 Supporting gates

| Gate | Threshold | Why |
|---|---|---|
| `edge_ratio` | **> 1.15** | needs a margin, not a coin flip, to survive costs |
| `sample_size` | **≥ 8 prints** | fewer and the median is noise |
| IV percentile at entry | **< 50** | buy vol cheap; this is the INVERSE of the wheel's `iv_rank_min > 30` |
| IV/HV | **< 1.0** | implied below realised favours the buyer |
| Spread (per leg) | **≤ 8% of mid** | two legs in and two out — four crossings |
| OI (per leg) | **≥ 500** | must be exitable the morning after |
| Cost as % of NAV | **≤ 1.5%** | can lose most of its value in a day |
| Confirmed earnings date | required | the whole premise |

### 2.5 Entry timing — two variants, both to be tested

**V1 — hold through the print.** Enter 1–3 sessions before earnings, exit the
morning after. Maximum exposure to both the move and the IV crush.

**V2 — ramp harvest, never hold the event.** Enter 10–15 sessions before
earnings when IV is still low, exit the afternoon *before* the print into the
elevated IV. Captures the vol build; takes zero gap risk. Loses if the ramp
doesn't materialise or theta outpaces the vega gain.

These are different strategies with different risk. **Backtest them separately
and report separately.** Do not blend into one headline number.

### 2.6 Expiry selection

Nearest expiry after the print maximises gamma *and* maximises crush damage —
those pull in opposite directions and the net is an empirical question. Test
nearest-after and one-further-out as a parameter. Owner's stated preference is
the near expiry (to minimise theta), but that preference should be checked
against the result, not assumed.

---

## 3. What must NOT be shared between A and B

Separate config objects, separate gate files, separate backtests.

The wheel wants **high** IV rank (sell rich premium). The straddle wants **low**
IV percentile (buy cheap premium). A single "IV gate" serving both would be
incoherent. If a name qualifies for both screens simultaneously, that is a bug
worth surfacing, not a feature.

---

## 4. Part C — backtest requirements

Nothing here reaches the live desk before its gates clear. Same protocol that
produced the QDB kill and the wheel's 3-of-4 failure.

### 4.1 The blocking technical risk — read this first

**The existing wheel backtest's IV proxy is unsuitable for earnings work and
must not be reused as-is.**

`wheel_backtest.py` prices options via Black-Scholes using **trailing realised
vol** as an IV stand-in. Three failures for this use case:

1. **It spikes after a gap, when real IV collapses.** Verified case: the
   backtest's META 2022-02-03 put priced at IV 95% for $19.04, the day after a
   −26% earnings gap. Real 30-day IV that day was ~45–50%; the true premium was
   nearer $8.50. The model collected **2.2× what was available** — precisely on
   the trades that then did the damage.
2. **It cannot model the pre-earnings ramp** — the thing V2 exists to harvest.
3. **It cannot model the post-earnings crush** — the thing that kills V1.

An earnings-straddle backtest built on this proxy would be measuring an artefact.

**Required:** either (a) real historical option quotes for the tested names and
expirations, or (b) an explicit event-aware IV term-structure model with the
ramp and crush parameterised from observed data, whose assumptions are stated in
the run log and testable independently.

If neither is available, **say so and stop** — do not run the backtest on the
existing proxy and report a number. This is the single largest risk in the spec.

### 4.2 Pre-registration

Before any run, commit a gates file containing the universe, date range, IS/OOS
split, all parameters, and the pass/fail thresholds. Thresholds do not move after
the result. Every run writes to the central `run_log` with the code SHA and the
gates SHA, per the existing wheel-backtest pattern.

### 4.3 Metrics that must be reported, gate or not

- **Utilisation** — the wheel sim ran ~95%; the live desk runs ~6%. Return on
  collateral without utilisation alongside it is not a return on NAV, and the
  gap between those two numbers is what makes the difference between a strategy
  that matters and one that doesn't.
- **Trade count** — a short-tier or straddle screen that fires twice a year
  cannot move a book regardless of per-trade economics.
- **Cost drag** — commission plus modelled slippage, stated separately. Four
  crossings on a straddle is material.
- Full trade log, per-leg, inspectable — the hand-check on the wheel backtest
  found three real flaws, and it only worked because every price was dumped.

### 4.4 Suggested pre-registered gates (owner to confirm or replace before commit)

**Part A — short-dated CSP tier**
- Win rate ≥ 80% (short-dated puts should rarely be breached at 0.30 delta)
- Return on collateral ≥ 25%/yr annualised, net of costs
- Max single-position loss ≤ 2× median premium collected
- No assignment inside 3 days of an earnings print (the tier's whole premise —
  any occurrence is a correctness failure, not a performance one)

**Part B — earnings straddle, V1 and V2 separately**
- Net expectancy per trade > 0 after costs — this is the bar the strategy is
  most likely to fail, and failing it is a valid, useful outcome
- Hit rate ≥ 40% (convex payoff tolerates a low hit rate; expectancy is the real
  test)
- Max drawdown ≤ 15% of allocated sleeve
- `edge_ratio` must demonstrate predictive power: trades with `edge_ratio > 1.15`
  must outperform those below it by a pre-stated margin. **If it doesn't, the
  screen has no edge and the whole of Part B is dead** — that is the single most
  important test in this document.

---

## 5. Expected outcome — recorded before the work, so the result is informative

**Part A** will probably pass. The MRVL trade's economics are real, and
short-dated premium on high-IV names is a known, if narrow, seam. The risk is
trade count: the admissibility rule confines it to earnings-avoidance windows,
so this may fire a handful of times a quarter.

**Part B is more likely to fail than pass.** Earnings straddles are widely
studied and generally found to be negative expectancy for buyers, because the
implied move is an efficient estimate of the realised one. V2 (ramp harvest) has
better odds than V1 (hold through) because it avoids the crush entirely.

Recording this now so that if Part B passes, the result is surprising and gets
scrutinised properly; and if it fails, that outcome is not treated as a reason to
tune parameters until it clears.

---

## 6. Non-goals

- Do **not** widen the standard 25–50 DTE band.
- Do **not** build 0-DTE selling.
- Do **not** merge the straddle scanner into the wheel's Options Desk gate logic.
- Do **not** ship either to the live desk before §4 gates clear.
- Do **not** reuse the trailing-realised-vol IV proxy for earnings work (§4.1).
- Do **not** add parameters to Part B after seeing the first result.

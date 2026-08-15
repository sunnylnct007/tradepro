# Wheel backtest v3 — PRE-REGISTERED pass/fail gates

**Committed BEFORE the first v3 run** (15 Aug 2026). v1 (`WHEEL_BACKTEST_GATES.md`,
`5817fe2`) and v2 (`WHEEL_BACKTEST_GATES_V2.md`, `cb51600`) are immutable and stay
as the record of those tests. This is a NEW file for a NEW test, per v2 §4.

**Thresholds are UNCHANGED from v2, which were unchanged from v1.** That includes
**G4 at ≤ 40% — the gate v2 FAILED** (META −50.9%). Moving a bar after watching it
fail is the one thing the protocol forbids, and it is forbidden in BOTH directions:
loosening it would be tuning-to-pass, and tightening it would quietly change the
question so that a v3 pass could not be compared with v2's failure. The clean test
is whether one new *rule* clears the *same* bar.

## Why there is a v3 at all

v2 failed exactly one gate, G4, and the failure was hand-checked and understood:

> META's killer Feb-2022 entry WAS blocked by the regime gate. It was then
> assigned via a **later, legitimately constructive** entry (28 Apr 2022, regime
> OK, premium clearing floors, spot 203.94 → assigned 194) and ground to ~88.
> Slice net was −0.1% — premium almost fully offset the share loss — but the
> mark-to-market excursion hit −50.9%.

That is the wheel's structural risk, not a modelling artefact: **once assigned,
you hold it.** The decision taken (15 Aug 2026) was option 2 of the three
recorded — add a structural rule and re-run — over accepting the risk (option 1)
or excluding high-beta names (option 3, rejected as hindsight-fitting: dropping
META *because META failed* is a blacklist, not a rule).

## The one change: the primary-trend floor

| Modelled in v3 | Rule |
|---|---|
| **Primary-trend floor** | **No NEW cash-secured put on a name whose close is below its own 200-day SMA** |

Everything else — premium floor, regime gate, earnings veto, idle cash at 4% —
is v2, unchanged.

**Why 200-SMA, and why this is not a tuned parameter.** 200 days is the standard
definition of the primary trend, and it is the length the equity book already
uses (`project_equity_entry_chases_tops`). It was chosen because the failure
mechanism *is* a primary-trend failure — being assigned into a name already in a
downtrend — not because it scored well. **No sweep was run over this number.
Tuning it after seeing v3's result invalidates the test.**

**It applies to NEW puts only, never to the covered-call repair leg.** Once the
shares are already held, refusing to sell calls would remove the only income
repairing the position — it would make the G4 failure worse, not better. This
asymmetry is deliberate and registered here before the numbers exist.

**No look-ahead.** The floor compares bar *i*'s close against the SMA of bars
ending at *i* — the same convention v2's regime series already uses
(`regime_from_closes(closes[:i+1])`). The 200 bars are drawn from the FULL
history including pre-window data, which the runner already requires (≥260
pre-window bars); computing the SMA from the window slice alone would leave it
undefined for ~200 of a ~315-bar window and silently convert the floor into
"no data, so allow everything". Bars where the SMA is genuinely undefined are
blocked **conservatively** (an unknown primary trend must never read as a
constructive one) and are **reported per run** — if that count is large, the
floor is being enforced by ignorance rather than by trend, and the run must say
so rather than bank the resulting safety.

## Coverage caveats — carried forward from v2, all still live

1. **Earnings gate is UNMODELLED in the 2020 window** (yfinance serves ~24 prints
   per name, earliest ~Oct 2020). 2022 and full model it; 2020 does not.
2. **The IV proxy is still wrong in a known direction** — premiums are
   Black-Scholes on trailing 30d realised vol, which spikes after a gap when real
   IV collapses (verified META case: modelled $19.04 vs a real ~$8.50). This
   **overstates income**, most on the worst trades.
3. **Assignment is expiry-only**; early assignment is not modelled, removing real
   losses.
4. **Adjusted-close space** — dividends implicitly credited; the 200-SMA is
   computed on this same series, consistently with everything else.

Caveats 2–4 all flatter the wheel. **A failing v3 is a strong result; a passing
v3 must be read against them.**

## Gates — IDENTICAL to v2

| # | Window | Test | Pass |
|---|--------|------|------|
| G1a | 2022 | Portfolio net total return | ≥ −10% |
| G1b | 2022 | Net return minus same-universe buy-and-hold | ≥ +8 pts |
| G1c | 2022 | Portfolio max drawdown | ≤ 25% |
| G2a | 2020 | Portfolio net total return | ≥ 0% |
| G2b | 2020 | Portfolio max drawdown | ≤ 30% |
| G3 | Full | Net CAGR on total NAV (idle collateral included) | ≥ 8%/yr |
| G4 | Each | Worst single-symbol max drawdown on its slice | ≤ 40% |
| G5 | 2022 + full | CSPs opened whose expiry window contained a known print | exactly 0 |

Disclosures (reported, never gated): utilisation, trade count, peak simultaneous
assigned names, cost drag, premium income vs realised share P&L, earnings-gate
coverage, **trend-floor block count**, and **undefined-SMA bar count**.

## Expected outcome — recorded before the full run

An exploratory single-symbol check (META, 2022, labelled EXPLORATORY, not a gate
run) confirmed the mechanism: the floor blocks 62 bars, META sells **zero** puts
in 2022, and its slice drawdown goes −50.9% → 0.0%. So:

- **G4 almost certainly PASSES.** The rule was aimed at it and demonstrably
  disarms the failing case. This is close to a foregone conclusion and therefore
  deserves the *least* credit of any result here.
- **G3 is where this is decided, and I predict it FAILS.** Here is the tension,
  stated plainly before the numbers: **put premium is richest precisely when a
  name is falling.** The trend floor removes exactly the highest-income trades.
  v2 cleared G3 by 0.25pt (8.25% vs 8.0%) *with* those trades, and that margin
  already sits inside the three optimistic biases above. Strip the downtrend
  premium out and NAV return leans harder on 4% idle cash. My honest prediction:
  **G3 fails, G4 passes, G1b improves** (not selling into downtrends helps most
  where buy-and-hold hurts most).
- G1a / G1c / G2a / G2b should hold or improve — less exposure, less drawdown.

**If G3 fails, that is the answer, not a prompt for a v4.** It would mean the
wheel's income does not survive its own risk control — that the edge measured in
v2 was substantially payment for the assignment risk G4 flagged. The next move
would then be a decision about the wheel, not another parameter.

## Phase 1

Phase 1 closes when **G1–G5 all pass on these numbers as committed**. Nothing
else closes it.

**Additional standing condition, recorded here (15 Aug 2026):** even on a full
pass, G3 clearing by a hair (as in v2) is not sufficient to fund real money,
because the declared biases plausibly exceed the margin. Real capital requires
G3 clearing with room to absorb caveats 2–4. Paper first regardless.

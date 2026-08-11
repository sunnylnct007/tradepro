# Wheel backtest — PRE-REGISTERED pass/fail gates

**Committed BEFORE the first backtest run** (11 Aug 2026, owner directive:
"write down what counts as pass or fail before you see the output — without
it you run the test, see a number, and decide afterwards whether it's good.
That's not a test."). The QDB precedent applies: gates declared up front
(hit ≥60%, expectancy ≥+2%), result 48.7%/+0.59%, strategy killed. Same
discipline here. **Any change to these thresholds after a run = a NEW file
and a NEW run.** Git history is the audit trail.

## What is being tested

`quant_engine/options/wheel_backtest.py` (CSP → assignment → covered-call,
daily-stepped, assignment/call-away logic exact) wired via
`tradepro-wheel-backtest`, portfolio-aggregated across the wheel universe.

**Pricing caveat (registered up front):** premiums are Black-Scholes on
trailing 30d realised vol — no volatility-risk-premium, no crisis IV spike.
That UNDERSTATES income (2020 real premiums were far richer), so income
gates passed on model premiums would also pass on real premiums. Drawdown
gates are share-price-driven and largely pricing-model-independent.

**Structure caveat:** one independent wheel per symbol, collateral fully
reserved per position (cash-secured by construction — no leverage, no
margin-call path). The 2020 test therefore measures correlated drawdown,
simultaneous-assignment pile-up and recovery, not cash shortfall.

## Fixed parameters (not tunable after the fact)

- Universe: every symbol in the screen's `DEFAULT_UNIVERSE` (82 names incl.
  SPY/QQQ/DIA) with ≥260 daily bars in the yahoo 1d cache before the
  window's effective start. No other filter — no cherry-picking survivors.
- Capital: $25,000 slice per symbol (the owner's ~£25k/pos scale), 1
  contract per wheel. A name whose strike×100 exceeds its slice simply
  stays flat (visible in utilisation — that IS the capital answer).
- Strikes ~5% OTM, 30 DTE (module defaults; the live screen's shape).
- Costs, net of which ALL gates are graded: 5% haircut on every premium
  (half-spread) + $1.50 commission per contract per leg.
- Windows: **2020** = bars from 2019-10-01 → 2020-12-31; **2022** = bars
  from 2021-10-01 → 2022-12-31 (≈60 trading days warm-up each, so the
  effective windows are the calendar years). **Full period** (for G3) =
  2019-10-01 → latest cached bar.

## Gates

| # | Window | Test | Pass |
|---|--------|------|------|
| G1a | 2022 (the decisive one) | Portfolio net total return | ≥ −10% |
| G1b | 2022 | Net return minus same-universe buy-and-hold | ≥ +8 pts |
| G1c | 2022 | Portfolio max drawdown | ≤ 25% |
| G2a | 2020 (correlated assignment) | Portfolio net total return | ≥ 0% |
| G2b | 2020 | Portfolio max drawdown | ≤ 30% |
| G3 | Full period | Net CAGR on total NAV (idle collateral included) | ≥ 8%/yr |
| G4 | Each window | Worst single-symbol max drawdown on its slice | ≤ 40% |

Disclosures (reported, no pass/fail): capital utilisation per window (share
of symbol-days with a position on), peak simultaneous assigned names,
return-on-deployed-collateral vs return-on-NAV (the 30%-utilisation → ⅓
return question), premium income vs realised share P&L split.

## Phase-1 gate

Phase 1 (wheel = the one trusted stream) closes when **G1–G4 all pass on
these numbers as committed**. Merged code does not close Phase 1; a passing
pre-registered backtest does. A failed gate = the wheel's config changes
(strike distance, universe, sizing) and the WHOLE suite re-runs against a
new gates file — or the approach is killed, QDB-style.

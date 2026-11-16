# Momentum rotation — sector, correlation and per-stock scaling: RESULT (16 Nov 2026)

Pre-registered in MOMENTUM_ROTATION_GATES_V3_SECTOR_CORRELATION.md (bcbfb681),
committed before the run. 142 symbols, 2006-08 -> 2026-11-13, 0.1% a trade.

| Variant | Full CAGR | Full DD | Full Sharpe | 2008-17 Sharpe / CAGR | 2018-now Sharpe / CAGR | Pass |
|---|---|---|---|---|---|---|
| **Control** | **9.68** | **-14.36** | **0.98** | 0.81 / 6.93 | 1.11 / 12.46 | — |
| 1 Sector ETF above 50-day | 10.02 | -14.04 | 1.05 | 0.96 / 7.91 | 1.12 / 12.09 | fail (2018-now) |
| 2 Sector ETF above 200-day | 9.66 | -14.10 | 0.97 | 0.80 / 6.87 | 1.11 / 12.50 | fail |
| 3 Trail 10% in a sector sell-off | 8.20 | -11.63 | 0.93 | 0.85 / 6.76 | 0.96 / 9.40 | fail |
| 4 Relative-to-sector stop 15% | 9.15 | -14.52 | 0.96 | 0.81 / 6.81 | 1.07 / 11.43 | fail |
| 5 Correlation cap 0.7 | 10.42 | -14.30 | 1.03 | 0.89 / 7.71 | 1.14 / 13.13 | fail (2018-now Sharpe +0.03 < +0.05) |
| 6 Volatility-scaled stop | 9.94 | -13.25 | 0.98 | 0.83 / 7.21 | 1.09 / 12.64 | fail |
| 7 Vol-adjusted score | 8.96 | -13.88 | 1.00 | 0.82 / 5.98 | 1.13 / 11.95 | fail |
| 8 Positive 3-month return | 9.25 | -14.36 | 0.95 | 0.78 / 6.59 | 1.08 / 11.88 | fail |
| 9 Skip the latest month | 9.25 | -12.22 | 0.95 | 0.70 / 5.92 | 1.14 / 12.58 | fail |

**Decision: the control stays.** No variant cleared the pre-registered bar.

Nearest: **5, the correlation cap**. Higher CAGR in BOTH halves, higher Sharpe
in both, same drawdown; it misses only on the size of the 2018-now Sharpe gain
(+0.03 against the +0.05 required). Nine variants were run, so a near-pass is
weak evidence on its own. It is the one candidate worth FORWARD evidence; its
threshold (0.7) must not be re-tuned on this history.

Sector filters help 2008-17 (variant 1: Sharpe 0.81 -> 0.96) but not 2018-now,
where sector sell-offs were mostly bought back quickly. Tighter stops in a
sector sell-off (3) cut drawdown but cost a quarter of the 2018-now return —
the same pattern as every tighter-stop variant so far.

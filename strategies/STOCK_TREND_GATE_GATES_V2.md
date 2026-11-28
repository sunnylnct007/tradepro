# Stock-or-sector trend gate for new short puts — gates, v2

Status: PRE-REGISTERED 28 Nov 2026, committed before any per-stock trend
state was computed. Follow-up to `SECTOR_TREND_GATE_RESULT_V1.md` (FAILED G1:
the sector ETF turned after the stocks did).

**Honest limit of this pre-registration.** The 61 entries and their forward
returns are the v1 set and have already been seen. What has not been seen is
any stock's own trend state on its entry date. To limit hindsight, the rule
below is the v1 formula applied unchanged to the stock, and the v1 thresholds
are kept. This is an in-sample follow-up on one account's five months; a
pass is a reason to test out of sample, not proof.

## Rule under test (fixed)
For an entry on day t, data through the close of t−1:

TREND_DOWN(x) = close < SMA50  AND  min(low, last 10 sessions) < min(low,
the 20 sessions before those 10).

**BLOCK = TREND_DOWN(stock) OR TREND_DOWN(sector ETF)** — sector map as v1.
A stock with fewer than 50 sessions of history is treated as NOT down
(cannot be evaluated), and listed.

Also reported, not gated: TREND_DOWN(stock) alone.

## Data
Entries and outcomes exactly as v1 (61 entries; "bad" = underlying −10% or
worse over the next 20 sessions). Daily bars from IBKR, regular hours.

## Gates (unchanged from v1)
- **G1 protects:** ≥ 60% of bad entries blocked.
- **G2 does not choke:** ≤ 30% of the other entries blocked.
- **G3 separates:** blocked entries' mean forward return ≥ 5 points below
  allowed entries'.
- **G4 availability:** across the entry underlyings, the stock-level
  condition is true on ≤ 35% of stock-sessions from 9 Jul to 30 Sep 2026.

PASS only if all four hold. No re-tuning in this run.

## Prediction (recorded before the run)
G1 improves to roughly 60–70% and may pass; G2 is the risk — high-volatility
names sit below their 50-day often, so the rule may block good summer
entries; G3 passes; G4 uncertain.

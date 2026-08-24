# The Scanner and the backtests read DIFFERENT history. 24 Aug 2026.

Found by the owner asking what "beats avg" means. Working the example through
on WCC gave 5 trades; his screen showed 20. That gap is not a display bug.

## The divergence

| source | WCC bars | from |
|---|---|---|
| **API** (`/api/integrations/ibkr/bars`) — what the SCANNER reads | **5,000** | 2006-10-05 |
| **parquet store** — what every BACKTEST reads | **1,164** | 2022-01-03 |

The Scanner computes on twenty years. The harness that produced every shipped
number computes on four and a half.

## How widespread

Of 60 universe symbols checked, **11 are materially shallower locally**, and
across those eleven the parquet store holds **14,322 bars against the API's
47,328 — 70% of the available history missing.**

    STX  FIX  WDC  COHR  TER  CIEN   1,164 local  vs  5,000 available
    HD                              1,416        vs  5,000
    DIA                             1,797        vs  5,000
    LITE VRT DELL                   1,164-1,797  vs  2,024-2,787

The local first-bar dates cluster at 2022-01-03, 2021-01-04 and 2019-07-01 —
the fetch-window artefacts the data lane already identified. What is new is
that **the deeper history EXISTS and is being served by the API**; it simply
was never written to the parquet store the studies read.

## What this does and does not affect

**Does NOT affect the forward test.** The rule is unchanged, the live daemon
reads the same store it always has, and the window grades EXECUTION rather than
edge.

**DOES affect the evidence base.** Every backtest — the 2,310-trade Swing
result included — ran on truncated history for roughly a fifth of the universe.
More history means more trades and a different mix; the numbers are not wrong
so much as computed on less data than we have.

**DOES affect what the owner sees.** The Scanner's per-symbol record is
computed from the API, so it can legitimately differ from any number I quote
from a Python study. Two surfaces, two datasets, same label — which is the
same class of defect as the duplicated constants, in the data layer.

## Not acting on it tonight

Backfilling the parquet store changes the trade population, and G4 moves with
population while G5 has 1.1 points of slack. That is a store-wide change during
the forward-test window, which is exactly what the freeze exists to prevent.

Raised with the data lane as a post-window item, alongside the XLC/XLRE depth
and the adj_factor migration already parked there.

**Until then, treat the Scanner's per-symbol counts as the deeper (and better)
number, and the study figures as the conservative one.**

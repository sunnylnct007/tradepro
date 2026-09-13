# SWING ROBUST SIGMA GATES V1 — spike-proof the entry. Frozen BEFORE the run.

13 Sep 2026. Owner: *"sometimes one day spike or 2 day spike pollutes the
figure... how do other financial institutions handle that"*.

## The problem, measured
A single +30% day inside a 20-day window (injected into AAPL's live window):

| statistic | before | after | change |
|---|---|---|---|
| 20-day mean | 316.63 | 321.42 | +1.5% |
| 20-day **stdev** | 7.42 | **22.84** | **+208%** |
| the σ a close of 332.27 reads | **+2.11σ** | **+0.48σ** | signal destroyed |

The mean barely moves — it is one price in twenty. The **standard deviation
triples**, and it is the denominator of the entry rule. So a genuine −2.5σ
dip in a name that spiked three weeks earlier reads −0.7σ and never fires.
The bias is toward MISSED trades rather than bad ones, which is the safer
direction, but it blinds the rule on exactly the names that just moved.

Incidence: 349 single-day moves beyond ±25% in 653,915 bars (0.053%). Rare,
but one is enough to blind a symbol for a month. Splits are NOT the cause —
the store is verified split-adjusted (AAPL 4:1, NVDA 10:1, TSLA 3:1, AMZN
20:1 all show normal daily moves).

## The change under test
Replace the population standard deviation with a **MAD-based scale**, the
standard robust substitute:

    median20 = median(closes[-20:])
    MAD      = median(|closes[-20:] - median20|)
    sigma_robust = (close - median20) / (1.4826 * MAD)

The 1.4826 factor makes MAD consistent with the standard deviation for
normally-distributed data, so **the −2.25 threshold keeps its meaning** and
does not need recalibrating. One estimator changes; nothing else does.

## Gates — the swap is adopted only if ALL hold
R0 **equivalence on clean data**: across windows containing no move beyond
   ±10%, the two σ values agree within 0.15 on average. (If they disagree on
   clean data the scaling is wrong and the rest is meaningless.)
R1 n ≥ 300
R2 win rate ≥ 65%
R3 mean/trade ≥ +0.75%
R4 all four time×symbol two-split cells positive
R5 worst trade ≥ −25%
R6 **mean/trade ≥ control − 0.05pp** — robustness may not be bought by
   giving up edge

## Prediction (recorded before the numbers)
It fires MORE often (the inflated denominator stops suppressing signals) at
a similar or slightly LOWER mean per trade — the extra trades are ones the
old estimator hid, and hidden is not the same as good. I give it ~55% to
pass all seven. If R6 fails, the spike suppression was accidentally acting
as a quality filter, which would be worth knowing on its own.

Run AFTER the 2006–2010 crisis backfill completes, so both studies read the
same store. Harness: a variant of backtests/studies/mean_reversion_v2.py.

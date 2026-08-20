# ICH support-distance entry filter — PRE-REGISTERED gates

**Committed BEFORE the first run** (20 Aug 2026). Same protocol as
`WHEEL_BACKTEST_GATES_V2/V3.md` and `SR_LEVEL_STUDY_GATES_V1.md`: thresholds
fixed in advance, a prediction on record, and a standing rule that a failure is
an answer rather than a licence to tune.

## What prompted this

`ichimoku_equity` has been live for six weeks and its realised record,
reconstructed from priced fills (the P&L endpoint refuses to report — 141 fills
carry no broker price), is bad:

    15 closed round trips, 10 Jul -> 20 Aug
    realised   -$376.16
    win rate   20%   (3W / 12L)
    avg win    +$7.09
    avg loss   -$33.12      <- losers 4.7x the size of winners
    same-day round trips 20%

A trend follower producing tiny wins and large losses is doing the opposite of
what it is for.

## The candidate mechanism — and what is NOT the mechanism

The standing explanation in the project notes is that ICH "buys EXTENDED
momentum (+42% over 200SMA for losers)". **Measured on the 15 live trades, that
is not true**: winners were 17.7% above the 200-SMA and losers 16.3%. Extension
does not separate them. Nor does the 13-week range percentile (65 vs 67).

The one thing that did separate them was **distance to the nearest support
level at entry** — winners 2.3 ATR clear, losers 0.9 ATR. Three winners is not
evidence, so it was tested at scale first (25,120 ICH BUY entries, 240 symbols,
3 years, buckets fixed in advance, signal taken from the LIVE `market_state()`):

    <1 ATR (hugging support)   15,501   +0.62%   55% win
    1-2 ATR                     5,706   +0.61%   55% win
    2-3 ATR                     2,236   +1.40%   59% win
    >3 ATR (clear)              1,677   +1.39%   60% win

A clean step at 2 ATR — the two buckets below are identical, the two above are
identical. **62% of all ICH entries fall in the worst bucket.**

## The rule under test

> **Do not open a long when price is within `MIN_SUPPORT_ATR` of the nearest
> pivot support level.** Default 2.0 ATR(14). Support levels come from
> `quant_engine/sr_levels.py` — the VERBATIM port of the chart's `pivotLevels`
> (win=5, clusterPct=0.005, maxScan=240), using `levels_asof` so only pivots
> confirmed at the decision bar are visible.

Nothing else changes. Same universe, same Ichimoku parameters (5/32/50,
shift 32), same exits, same costs.

## Coverage caveats — declared before the numbers exist

1. **The forward-return study is not a backtest.** It measured a fixed 10-day
   forward return with no exits and no costs. This run uses the strategy's real
   exits and real costs, and may therefore disagree with it. If it does, the
   backtest wins.
2. **The filter cuts ~84% of entries** (25,120 -> ~3,900 in the study window).
   A large drop in trade count raises variance and can flatter per-trade
   averages while reducing total return. G3 exists to catch exactly that.
3. **Survivorship**: the universe is today's cached symbol list, so names
   delisted before today are absent. Same limitation as every prior study here.
4. **Costs are modelled, not observed.** The live `intraday_flat` post-mortem
   found costs were what actually killed it, so a filter that survives raw
   returns still has to survive spread + commission.

## Gates

| # | Test | Pass |
|---|------|------|
| **V0** | Filtered trade count (validity, not performance) | ≥ 300 |
| **G1** | Filtered win rate − unfiltered win rate | ≥ +4 pts |
| **G2** | Filtered avg P&L per trade − unfiltered | ≥ +50% relative |
| **G3** | Filtered TOTAL net return ≥ 60% of unfiltered total | true |
| **G4** | Filtered max drawdown ≤ unfiltered max drawdown | true |
| **G5** | The loss/win size ratio improves (filtered < unfiltered) | true |

**G3 is the honest counterweight.** A filter that keeps only the best 16% of
trades will almost certainly improve per-trade quality; the question is whether
it leaves enough total return on the table to be worth it. Cutting 84% of
trades to gain 4 points of win rate is not obviously a good deal, and G3 is
where that gets decided rather than assumed.

**G5 targets the actual observed failure** — losses 4.7x wins. If the filter
does not narrow that ratio it has not fixed the thing it was chosen for, no
matter what the averages do.

## Prediction — recorded before the work

**G1, G2 and G5 PASS; G3 is the coin-flip and the one I expect to fail.**

Reasoning, stated so it can be judged: the forward-return study's step at 2 ATR
is large and clean across 25,120 samples, so per-trade quality should improve.
But removing 84% of entries removes 84% of the compounding opportunities, and
the surviving trades would need to be ~6x better in aggregate to hold total
return — the study suggests roughly 2.25x. So I expect **better trades, less
money**, and the interesting question becomes whether a LOWER threshold (1 ATR,
which the study says is worthless) or a different formulation keeps more of the
trade count.

If G3 fails, the answer is NOT to sweep the threshold until it passes. It is
that a support-distance filter improves selection but costs too much throughput,
and a v2 would need a different construction with its own gates file.

## Phase

This gates file governs the ICH entry-filter question only. It does not
re-open the wheel decision ([[project_wheel_backtest_v3_result]]: do not fund)
and does not license live trading of anything.

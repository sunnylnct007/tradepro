# Momentum / strength continuation — PRE-REGISTERED gates

**Committed BEFORE the first run** (22 Aug 2026). Same protocol as the wheel,
S/R, Ichimoku-exit and mean-reversion studies.

## Why this study exists

The mean-reversion screen refuses to buy strength — correctly, by design. Asked
what it would have done with MU on Friday (close 966.78, 69% above its 200-SMA,
up ~22% in ten sessions) the answer was: nothing. Its trigger sat 24% lower.

Owner: *"can we test that as well. with calculated risk we should be able to
leverage."* Fair — "the screen won't touch it" is not the same as "there is no
trade there", and that claim should be measured rather than asserted.

This is a THIRD family, deliberately separate from the two already tested:

| | Ichimoku | mean reversion | momentum (this) |
|---|---|---|---|
| buys | trend breaks | stretched pullbacks | strength/new highs |
| hold | 41+ bars to pay | 3-4 days | unknown — the question |
| win rate | 39% (ceiling ~44%) | 62-65% | expected LOWER |

## What is being tested

All entries additionally require `close > 200-SMA` — this is continuation of an
established uptrend, not bottom-fishing a bounce.

| # | Entry |
|---|-------|
| N1 | close at a 20-day high |
| N2 | close at a 52-week high |
| N3 | 10-day return > +10% (thrust) |
| N4 | close > 20-SMA and 20-SMA > 50-SMA, entering on a pullback to the 10-SMA |

| # | Exit |
|---|------|
| E1 | close < 10-day SMA |
| E2 | trailing 8% stop from the highest close since entry |
| E3 | fixed 10 bars |

Costs 5bps/side, MOO fills (signal on settled close, fill next open), same
universe and window as the other studies, same data guards INCLUDING the
wrong-venue quarantine (6 symbols dropped).

## Gates

| # | Test | Pass |
|---|------|------|
| **V0** | Trades (validity) | ≥ 1,000 |
| **G1** | Win rate ≥ 45% — the owner's stated floor | true |
| **G2** | Mean return per trade, NET of costs | > 0 |
| **G3** | Median hold ≤ 20 bars | true |
| **G4** | Top-1% profit share ≤ 35% | true |
| **G5** | Worst single trade ≥ −25% | true |

**G4 is set at 35%, not the 25% used for mean reversion.** Momentum is
legitimately more tail-dependent — riding a few big trends IS the mechanism, not
a defect. Holding it to the mean-reversion bar would be testing it against the
wrong standard. 35% is still a real constraint: beyond that it is a lottery.

**G1 at 45% is the owner's floor and is deliberately hard for this family.**
Momentum classically wins less than half the time and pays through size of
winner. If it fails G1 while passing G2/G4 handsomely, that is an informative
result, not a pass — and the honest conclusion would be that this family cannot
meet the stated constraint even if it makes money.

## Prediction — recorded before the work

**G1 FAILS on N1/N2/N3 (win rates in the high 30s to low 40s) and G2 passes.**
Buying new highs is a positive-expectancy, low-hit-rate trade; that is the whole
character of the family.

**N4 (pullback-to-10-SMA within an uptrend) is the one I expect to clear G1** —
it is a hybrid: momentum context, mean-reversion timing. If anything here
satisfies both the owner's win-rate floor and the "leverage strength" intent,
it is that.

**E2 (trailing stop) should beat E1/E3 on total return and lose on win rate.**

If G1 fails everywhere, the answer is NOT to lower it — it is that strength
trading on this universe cannot meet the constraint, and MU on Friday was
correctly a no-trade.

## Scope

Candidate generation only. Clearing these gates would license building a second
screen alongside Swing — not live trading, which needs its own paper evidence.

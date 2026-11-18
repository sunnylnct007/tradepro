# Short with a tight profit target — pre-registered (18 Nov 2026)

Owner, 18 Nov: "i still feel if we keep profit margin less we shd be able to
gain from shorts". Written BEFORE the run below; not edited after it.

## Already known when this was written
- Rule 2 (gap-down opening-range short), target 1.5% = stop 1.5%: n=251,
  win 51%, −0.02%/trade (PROJECT memory, 14 Nov).
- Rule 7 short side (first-candle volume >= 2x), targets none / 2% / 1.5% / 1%:
  +0.07 / +0.05 / +0.05 / +0.005 %/trade, win 45 / 49 / 52 / 59 %, n=240.
  A 1% target raised the win rate and cut the average to zero.

## The variants (6, all reported)
Both rules exactly as frozen in their 14 Nov backtests (same signals, same
stop, same flat-at-close, same 0.06% round-trip cost, $10,000 notional), only
the profit target changes:
- Rule 2 short, target 0.50% / 0.75% / 1.00% (stop +1.5%).
- Rule 7 short side, volume >= 2x, target 0.50% / 0.75% / 1.00% (stop = top
  of the first 5m candle).
Data: the IBKR 5m bars Aug 2024 – Nov 2026 already in the store, same names.

## Pass mark (a variant passes only if ALL hold)
1. n >= 150 trades.
2. Average >= +0.10% a trade after costs.
3. t >= 2.5 (six variants tested, so the usual 2.0 is raised).
4. Positive in BOTH halves of the period.
A pass earns a forward shadow record (no orders), not a paper book.

## Result (run 18 Nov 2026, after the commit above) — ALL SIX FAIL

| Variant | n | Win | Avg win | Avg loss | Avg/trade | t | Halves |
|---|---|---|---|---|---|---|---|
| Rule 2, target 0.50% | 253 | 73% | +0.44% | −1.56% | −0.098% | −1.75 | −0.12 / −0.08 |
| Rule 2, target 0.75% | 253 | 65% | +0.69% | −1.53% | −0.085% | −1.27 | −0.08 / −0.09 |
| Rule 2, target 1.00% | 253 | 59% | +0.93% | −1.49% | −0.076% | −1.00 | −0.14 / −0.02 |
| Rule 7 short, target 0.50% | 241 | 74% | +0.44% | −1.22% | +0.005% | +0.10 | −0.03 / +0.04 |
| Rule 7 short, target 0.75% | 241 | 66% | +0.68% | −1.27% | +0.005% | +0.08 | +0.01 / −0.00 |
| Rule 7 short, target 1.00% | 241 | 59% | +0.87% | −1.26% | −0.002% | −0.03 | +0.00 / −0.01 |

A tighter target buys win rate and pays for it in win size, one for one: at
0.5% the short wins three times in four, and 0.74 x 0.44 − 0.26 x 1.22 ≈ 0.
The stop still takes full-size losses. On these signals the price path after
entry carries no structure a target can harvest. No shadow record is earned.

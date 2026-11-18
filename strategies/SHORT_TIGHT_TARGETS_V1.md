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

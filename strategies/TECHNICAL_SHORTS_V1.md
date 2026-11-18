# Technical short strategies — pre-registered (18 Nov 2026)

Owner, 18 Nov: "i am still not convinced abt not having the shorting
stratgey" / "got to be techoncal parameters that we can use". Written BEFORE
any run below; not edited after it. All five variants are reported.

## Known when this was written
- Intraday shorts with changed EXITS all failed (SHORT_TIGHT_TARGETS_V1.md):
  the signal, not the exit, carries no edge. So every variant here changes
  the SIGNAL or adds a condition known at entry.
- The long swing rule (buy < 20-day mean − 2.25σ while above the 200-day,
  exit at the mean / −8% / 20 sessions) is the one short-term rule with a
  measured edge (+0.9%/trade out of sample).

## Variants
Daily, on the 244-name sector-labelled universe, every stored session; signal
on a settled close, short at the NEXT open; cost 0.10% round trip plus borrow
at 1%/yr for the days held; one position per name at a time, every signal
taken (no cap), $10,000 notional.
- **D1 Mirror swing short.** Close > 20-day mean + 2.25σ AND close < its
  200-day SMA. Cover at the first close <= the 20-day mean (recomputed daily),
  or a close >= entry +8% (stop), or after 20 sessions.
- **D2 D1, only when SPY closed below its own 200-day SMA** on the signal day.
- **D3 RSI rollover in a downtrend.** RSI(14, Wilder) closes back below 70
  after closing above it, with close < 200-day SMA. Cover at RSI < 50, a
  close >= entry +8%, or after 10 sessions.

Intraday, exactly as frozen in their 14 Nov backtests (same names, same 5m
bars, same stops/targets/costs), gated on a fact known before the open:
- **I1 Rule 2 gap-down short, only on days SPY's prior close < its 200-day SMA.**
- **I2 Rule 7 short side (volume >= 2x), same gate.**

## Pass mark (a variant passes only if ALL hold)
1. n >= 150 trades (fewer = "too few to judge", not a pass).
2. Average >= +0.10% a trade after costs.
3. t >= 2.5 (five variants, so the usual 2.0 is raised).
4. Positive in BOTH halves of its period.
Also reported for context, not graded: the same stocks shorted at random
entry days for the same hold (the null a short must beat, since stocks drift up).
A pass earns a forward shadow record (no orders), not a paper book.

## Result (run 18 Nov 2026, after the commit above) — NONE PASSES

Universe note: the sector-labelled harvested universe resolved to **142**
names, not the 244 written above (244 was the older swing universe). Every
name that had trades is reported; 2006-01-04 to 2026-11-18.

| Variant | n | Win | Avg/trade | t | Halves | Random-entry null |
|---|---|---|---|---|---|---|
| D1 mirror swing short | 1,985 | 56% | −0.685% | −4.10 | −0.66 / −0.71 | −1.155% |
| D2 D1 + SPY below 200-day | 705 | 51% | −1.282% | −3.96 | −0.38 / −2.18 | −0.684% |
| D3 RSI rollover, downtrend | 565 | 49% | −0.103% | −0.38 | −0.69 / +0.48 | −0.860% |
| I1 rule 2, SPY below 200-day | 29 | 62% | +0.266% | +0.98 | −0.35 / +0.84 | (ungated −0.019%) |
| I2 rule 7 short, SPY below 200-day | 28 | 39% | +0.171% | +0.41 | +0.19 / +0.15 | (ungated +0.074%) |

- D1 and D2 lose money with high confidence. D2 (shorting in a bear tape)
  is worse, not better: bear-market rallies are violent.
- D1 and D3 do beat shorting the same stocks at random (by 0.47 and 0.76
  points), so the signals carry information — but not enough to overcome
  the upward drift of these stocks. CAVEAT: the universe is today's names,
  survivors that mostly went up, which biases EVERY short test against
  shorting. That bias cannot be removed with this store.
- I1 and I2 are positive but 29 and 28 trades are too few to judge; the 5m
  store starts Aug 2024 and SPY spent only 55 sessions below its 200-day.
  The existing no-order intraday record (rules 2 and 7) keeps running; its
  trades can be tagged by this gate from now on at no cost.

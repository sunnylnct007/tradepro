# Earnings pop-and-fade — result V1 (28 Nov 2026)

Gates: `EARNINGS_POP_FADE_GATES_V1.md` (pre-registered; data-handling amendments made before any result).
Data: 22 names, 356 report dates, 107 earnings gap-ups of ≥ +4% with 10 days of follow-through
(2022-06 → 2026-11; DELL's 25 Nov 2026 pop has too few days after it and is not counted).

| Group | n | Day fade (close < open) | Week fade (close R+5 < close R) | Median R→R+5 | Median R→R+10 | +7% breach within 10d |
|---|---|---|---|---|---|---|
| All pops | 107 | 49.5% | 42.1% | +1.2% | +0.9% | 42.1% |
| Sector WEAK (ETF < 50-day) | 38 | 57.9% | 34.2% | +2.4% | +3.0% | 44.7% |
| Sector STRONG | 69 | 44.9% | 46.4% | +0.5% | +0.6% | 40.6% |
| WEAK + failed gap (F) | 22 | 100% | 36.4% | +2.4% | +1.5% | 45.5% |
| STRONG + failed gap | 31 | 100% | 51.6% | −0.2% | +0.9% | 38.7% |
| WEAK, before Sep 2024 | 18 | 61.1% | 38.9% | +2.3% | +2.7% | 44.4% |
| WEAK, after Sep 2024 | 20 | 55.0% | 30.0% | +2.6% | +3.0% | 45.0% |

## Gates
- G1 FAIL: WEAK week-fade 34.2% (needed ≥ 60%, and 15 points above STRONG). It is 12 points BELOW STRONG.
- G2 pass: 38 WEAK pops.
- G3 FAIL: WEAK + failed-gap breach rate 45.5% (needed ≤ 15%).
- G4 FAIL: WEAK fades less than STRONG in both halves.

## Verdict: FAILED — do not use
A gap-up in a weak sector often gives back part of the gap on the day itself (58%), but over the
following week it tends to keep rising (median +2.4%). A short call ~7% above the entry close is
crossed within 10 days about 45% of the time. Selling a call spread to fade an earnings pop has no
edge on this data and loses about half the time. Recent fades (e.g. DELL, Nov 2026) are anecdotes, not the base rate.

Limits: daily bars only (no intraday VWAP); report sessions unknown before Aug 2026 (reaction day
taken as the larger gap of T / T+1); 22 tech-heavy names; credit and IV crush not modelled.

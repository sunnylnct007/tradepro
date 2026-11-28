# Sector trend gate for new short puts — result, v1

Run 28 Nov 2026 against `SECTOR_TREND_GATE_GATES_V1.md` (committed before the
data was pulled). No threshold was changed.

## VERDICT: FAILED (G1). Three of four gates pass.

| Gate | Threshold | Result | |
|---|---|---|---|
| G1 protects | ≥ 60% of bad entries blocked | 10 of 24 = **42%** | FAIL |
| G2 does not choke | ≤ 30% of other entries blocked | 3 of 37 = **8%** | pass |
| G3 separates | blocked ≥ 5 pts worse than allowed | −19.4% vs −4.9% = **14.5 pts** | pass |
| G4 availability | SMH blocked ≤ 35% of Jun–Sep sessions | 21 of 85 = **25%** | pass |

61 put entries, 24 of them "bad" (underlying −10% or worse within 20
sessions), 13 blocked.

## Why G1 failed: the rule triggers late

| Period | Entries | Bad | Blocked | Bad and blocked | Mean 20-session return |
|---|---|---|---|---|---|
| 9 Jul – 30 Sep | 29 | 2 | 2 | 1 | +1.7% |
| 1 – 20 Oct | 15 | 9 | **0** | 0 | −13.9% |
| 21 Oct – 27 Nov | 17 | 13 | 11 | 9 | −19.2% |

The gate turned on for SMH on 21 Oct, for QQQ on 30 Oct and for IGV and SPY
on 2 Nov. Nine bad entries were made between 1 and 20 Oct while every sector
ETF was still above its 50-day average: the individual stocks broke before
the sector did. From 21 Oct the gate blocked 9 of 13 bad entries, including
the largest losers (DELL ×2, STX ×2, AMD, MRVL ×2, WDC, SKHY); the four it
missed were software and non-tech names whose ETF had not yet turned (MDB,
SNOW, HOOD, VLO).

## What this means
- The rule is worth having — it never choked the profitable summer (2 of 29
  blocked) and the entries it blocked did 14.5 points worse — but **it is not
  sufficient on its own**, and it must not be presented as the protection
  against a repeat of October.
- The first-half-October damage was a *stacking* problem: 15 new puts in
  three weeks. The limits that address it are the cash-coverage rule, the
  two-CSP cap and the option-adjusted sector cap (operating model A), not a
  trend filter.
- A faster or stock-level trigger (the stock's own 50-day and lower lows, or
  the sector's 20-day) may close the gap. That is a **v2 with its own
  pre-registered gates**; nothing was re-tuned here.

## Deviations and limits (read before relying on this)
- **Window starts 9 Jul, not 1 Jun**: the trade history pulled covers Q3 and
  the last 90 days.
- **Puts were identified by inference.** IBKR trade records carry no strike
  or right. An opening option sale was counted as a put when fewer than 100
  shares per contract were held at that moment, plus two documented puts
  (APLD 19 Aug, GOOGL 7 Oct). 36 sales were excluded as covered calls or
  ambiguous (shares held), including every GOOGL sale after 18 Aug except
  7 Oct and all ORCL sales after the 24 Sep buy-write. Some excluded rows
  were puts; some included rows may be rolls.
- **Outcome is the underlying's move, not the put's P&L.** Strikes and
  premiums are not in the data.
- **Late entries have short windows** (to 27 Nov); GOOG's series ended
  25 Nov. ABBV (23 Nov) is the short leg of a spread.
- Prices: IBKR daily bars for the ETFs and 7 underlyings; TradePro
  `get_hypothetical_return` (adjusted closes) for the other 38 entries.

## Prediction vs outcome
Predicted G1 "passes narrowly" with the named risk that the rule triggers
late. The risk was the outcome: G1 failed on timing. G2–G4 as predicted.

# Stock-or-sector trend gate for new short puts — result, v2

Run 28 Nov 2026 against `STOCK_TREND_GATE_GATES_V2.md` (committed before any
stock-level trend state was computed). No threshold was changed.

## VERDICT: FAILED (G1, by one entry). Three of four gates pass.

| Gate | Threshold | v2: stock OR sector | v1: sector only |
|---|---|---|---|
| G1 protects | ≥ 60% of bad entries blocked | 14 of 24 = **58%** — FAIL | 42% |
| G2 does not choke | ≤ 30% of other entries blocked | 9 of 37 = **24%** — pass | 8% |
| G3 separates | blocked ≥ 5 pts worse | −14.1% vs −4.2% = **9.9 pts** — pass | 14.5 pts |
| G4 availability | stock rule true ≤ 35% of stock-sessions, 9 Jul–30 Sep | **23%** — pass | 25% (SMH) |

Stock rule alone (reported, not gated): G1 50%, G2 22%, G3 7.8 pts.

## By period (stock OR sector)

| Period | Entries | Bad | Blocked | Bad and blocked | Good and blocked |
|---|---|---|---|---|---|
| 9 Jul – 30 Sep | 29 | 2 | 6 | 1 | 5 |
| 1 – 20 Oct | 15 | 9 | 4 | 3 | 1 |
| 21 Oct – 27 Nov | 17 | 13 | 13 | 10 | 3 |

Adding the stock's own trend caught 3 of the 9 early-October bad entries
that the sector rule missed entirely, at the cost of blocking 5 good summer
entries instead of 2.

## The ten bad entries no trend rule caught
WDC 2 Oct, GOOGL 7 Oct, MRVL 9 Oct (−49%), NVDA 9 Oct, PLTR 19 Oct, CRDO
20 Oct, SKHY 28 Sep, MDB 27 Oct, SNOW 29 Oct, VLO 28 Oct.

Most of these puts were sold while the stock was **still above its 50-day
average, several at or near their highs** (MRVL on 9 Oct was at its peak
close). A trend filter cannot flag a stock that has not started falling.
For these, the only protection is structural: defined risk on every short
put, the cash-coverage rule, the two-CSP cap and the option-adjusted sector
cap (operating model A).

## What this means
- Use stock-or-sector trend as **a check in the gate, shown with its
  numbers** — blocked entries did about 10 points worse and it did not choke
  the book — but it failed its own bar and must not be described as
  sufficient.
- Stop looking for a better trend trigger for this problem. Two rules have
  now failed G1; a third variant fitted to the same 61 entries would be
  curve-fitting. The remaining gap is a sizing problem, not a signal problem.
- Next test worth pre-registering: the **size limits** themselves — replay
  the cash-coverage rule, the CSP cap and the sector cap over the same
  entries and count how many of the 24 bad entries (and how much of the
  obligation) they would have refused.

## Limits
Everything in v1's "Deviations and limits" applies (window from 9 Jul; puts
identified by inference; outcome is the underlying's move). In addition:
- The entries and outcomes had been seen before v2 was registered; only the
  stock trend states were unseen. This is an in-sample follow-up.
- SKHY (listed 10 Jul 2026) could not be evaluated for its 21 Aug entry and
  was treated as not blocked, per the gates file.
- Bars for SKHY and CRWV were transcribed by hand from IBKR output (their
  short histories would not download to file); entry-day closes for all
  other underlyings were checked against TradePro's adjusted closes.

## Prediction vs outcome
Predicted G1 "roughly 60–70% and may pass": it reached 58% and failed.
Predicted G2 as the main risk: it rose from 8% to 24% but stayed inside the
bar. G3 and G4 passed as expected.

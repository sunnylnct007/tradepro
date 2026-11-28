# Sector trend gate for new short puts — gates, v1

Status: PRE-REGISTERED 28 Nov 2026, committed before the entry list or the
June–September sector data were pulled. No threshold below may change after
the run; a different rule is a v2 with its own file.

## Question
Would the rule "no new short puts while the sector ETF is below its 50-day
average and making lower lows" have blocked the owner's damaging put sales
of autumn 2026 without blocking the profitable summer ones?

## Rule under test (fixed)
For an entry on day t, using only data through the close of t−1:

BLOCK = close < SMA50  AND  min(low, last 10 sessions) < min(low, the 20
sessions before those 10).

Sector ETF per underlying: SMH for semis, memory and AI hardware (MU, SNDK,
STX, WDC, AMD, NVDA, MRVL, SKHY, CRDO, INTC, DELL, APLD, CRWV, AVGO); IGV for
software (SNOW, MDB, PLTR, ORCL, IBM, ACN, UBER); QQQ for GOOGL, META, MSFT,
AAPL, HOOD; SPY for everything else.

## Data
- Entries: every opening put sale on a single stock in the live IBKR trade
  history, 1 Jun – 27 Nov 2026 (ETF legs and the long legs of spreads
  excluded; a roll counts as a new entry on its date).
- Prices: IBKR daily bars, regular hours.
- Outcome per entry: the underlying's return from the entry-day close to the
  close 20 sessions later (to 27 Nov if fewer remain). A "bad" entry is one
  where that return is −10% or worse.

## Gates
- **G1 protects:** at least 60% of bad entries were blocked.
- **G2 does not choke:** at most 30% of the other entries were blocked.
- **G3 separates:** mean forward return of blocked entries is at least 5
  percentage points below that of allowed entries.
- **G4 availability:** the gate blocked SMH on at most 35% of sessions from
  1 Jun to 30 Sep 2026.

Verdict: PASS only if all four hold. Fewer than 8 bad entries or fewer than
8 blocked entries = INCONCLUSIVE, reported as such. No re-tuning in this run.

## Prediction (recorded before the run)
G1 passes narrowly, G2 passes, G3 passes, G4 passes. Main risk: the rule
triggers late — the first October entries were made before the 50-day broke —
so G1 may fail on timing.

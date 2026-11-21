# Momentum rotation — sell-off exits (pre-registered, 21 Nov 2026)

Written BEFORE any run. Owner, 20–21 Nov 2026, after a seven-session sell-off
took the rotation book to −9.5% with no rule acting: "past few sessions have
been unusual sell off and our strategy shd be able to handle those",
"our strategy didnt managed to handle it any well".

## Why the control rode it down

The control checks the market (SPY vs its 200-day) ONLY at the monthly
decision. The 13 Nov decision saw SPY above it and bought at the 16 Nov open;
SPY then fell below it within days, and nothing could act before 15 Dec except
the 20% per-name trailing stop (AMD closed 28 cents above its stop on 20 Nov).

## Control

The decided design as it trades on paper (`Params()` defaults): monthly
decision on the 15th, SPY-200 market filter at the decision only, 20%
trailing stop, 25% vol target, 0.1% cost a trade.

## Variants — each ONE change, all run, all reported

`market_exit` — between decisions, when the condition is met at a CLOSE, the
whole book is sold at the next open; it re-enters only at the next scheduled
decision, and only if that decision's own market filter passes.

1. `spy200_daily` — SPY closes below its 200-day average.
2. `spy200_buffer3` — SPY closes 3% or more below its 200-day (whipsaw guard).
3. `spy200_weekly` — SPY below its 200-day at the last session of a week.
4. `spy50_daily` — SPY closes below its 50-day average (faster, more trades).

No combinations unless two single changes pass on their own.

## Pass mark — the V2 mark, unchanged

A variant replaces the control only if ALL hold:
- In BOTH halves, 2008-01-01..2017-12-31 and 2018-01-01..latest:
  Sharpe at least control + 0.05, AND CAGR at least the control's.
- Full period: worst drawdown no more than 2 percentage points deeper.
- Costs included. Trades, turnover and time in cash reported.

Also reported (descriptive, not part of the mark): the book's return through
each named sell-off window — 2008-09..2009-03, 2011-07..2011-10,
2015-08..2016-02, 2018-10..2018-12, 2020-02..2020-04, 2022-01..2022-10, and
2026-11-13..latest — for control and every variant.

A variant that fails the mark but improves drawdown materially earns a
FORWARD shadow record, not a switch.

## Known limits

The same store as V1–V3 (mixed adjusted sources; survivor universe of ~142
names with full history). Equal for every variant, so the comparison is fair.
Exits fill at the next open — a gap-down open is taken in full, as it would be.

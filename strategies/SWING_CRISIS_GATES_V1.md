# SWING CRISIS GATES V1 — does the edge survive 2008? Frozen BEFORE the data lands.

13 Sep 2026. Found while answering "why am I seeing so many missing days":
**the universe's history begins 2010-01-04.** MSFT, NVDA, GOOGL, JNJ, KO and
XOM each held exactly 4,198 bars from that identical date; only a handful
(AAPL, ADBE) reached back to 2006, and SPY — the regime classifier — had a
34-month hole from Feb 2007 to Jan 2010.

So every number this desk publishes about the swing rule was measured on a
sample that **starts after the financial crisis ended**. The screen's own
"This loses money in a bear market" panel splits by S&P drawdown and reports
`drawdown over 15%: 133 trades, 53.4% win, −0.28%/trade` — that bucket can
only have been built from 2011, 2018, 2020 and 2022. The deepest modern
stress for a dip-buying rule has never been in the sample. That is not a
wrong number; it is a narrower one than it looks, and the difference matters
more than anything else before real money.

A backfill of 2006-01-03 → 2010-01-03 is now running (IBKR gold source,
MSFT verified at 5,171 bars spanning 2006–2026). This document freezes what
the re-run has to show BEFORE the result exists.

## The run
Re-run `backtests/studies/mean_reversion_v2.py` unchanged on the extended
store, then split trades into:
- **POST-2010** — the current sample, which must reproduce (control).
- **CRISIS 2007-01-01 → 2009-12-31** — the newly visible trades.

## Gates — the rule KEEPS its current standing only if ALL hold
C0 the post-2010 split reproduces today's published figures within 0.15pp
   mean/trade and 1.5pp win rate (if it does not, the backfill changed the
   sample and nothing else here is interpretable)
C1 crisis n ≥ 100 (below this the crisis split is anecdote, and we say so
   rather than concluding)
C2 crisis mean/trade > 0.00% — the rule may earn less in a crash; it must
   not lose
C3 crisis worst trade ≥ −40% (the published worst is −23.9%; a crash may
   exceed it, but not without limit)
C4 the full-sample mean/trade stays ≥ +0.75% — the S2 bar every previous
   swing study has been held to

## Consequences, pre-stated
- **All five pass** → the edge is confirmed across a crisis and every
  published figure is re-stated on the fuller sample. This strengthens the
  case for funding more than any other evidence we hold.
- **C2 fails** → the rule loses money in a real crash. It stays live on
  paper, but FUNDING_GATES_V1 gains a regime condition: no swing capital
  while the S&P is in a drawdown beyond the level where the sign flips.
  This would be the single most valuable thing this desk has learned.
- **C1 fails** → report the crisis split as INDICATIVE and do not act on it.
- **C0 fails** → stop, diagnose the backfill, change nothing.

## Prediction (recorded before the numbers)
C2 fails. Buying 2.25σ dips above a 200-day average through 2008 means
buying a falling knife in the one regime where the 200-day floor also
breaks — I expect the crisis mean to be NEGATIVE, somewhere around −1 to
−3%/trade, with a worst trade beyond −30%. I also expect C1 to pass
comfortably (2008 generated dips constantly), and C0 to pass.

If I am right, the honest headline changes from "72.8% win, +1.06%/trade"
to "…in every market we have data for except the one that matters most."

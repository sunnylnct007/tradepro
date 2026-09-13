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

## RESULT — run 13 Sep 2026, after the 2006–2010 backfill

Store: 165 of 244 symbols now carry pre-2007 history. The 69 that failed are
mostly companies that did not exist (TSLA listed 2010, META 2012). Provenance
is MIXED: 25 symbols carry IBKR gold bars, the rest yfinance; where both
existed the store kept the gold row per session.

| split | n | win | mean | worst | cells |
|---|---|---|---|---|---|
| POST-2010 | 4768 | 70.0% | +0.80% | −29.9% | all + |
| CRISIS 07–09 | 606 | 69.0% | **+0.90%** | −32.6% | all + |
| FULL | 5392 | 69.9% | +0.81% | −32.6% | all + |

**C0 FAILED**, so under the rule I wrote, nothing here is adopted. Diagnosed,
two causes, neither a data fault:

1. **My gate was mis-specified.** C0 compares against the published +1.06% /
   72.8%, which is the record of the **2.5σ** rule. The live rule is 2.25σ
   (widened 8 Sep on its own pre-registered study). I froze a gate against
   figures for a rule we no longer run.
2. **The backfill legitimately revealed 2010.** Previously the first ~220
   bars of each series were consumed as indicator warm-up, so 2010 was
   invisible. It is now tradeable — 144 trades at **−0.61%**, the worst year
   in the sample. Post-2010 drops from +0.84% (2011+) to +0.80% purely by
   admitting it.

## The finding the aggregate hides

| phase | n | win | mean | worst |
|---|---|---|---|---|
| 2007 pre-crash | 291 | 71.8% | +0.71% | −32.6% |
| **2008 THE CRASH** | 142 | **42.3%** | **−2.49%** | −13.7% |
| 2009 recovery | 173 | 86.1% | **+4.02%** | −10.1% |
| **Sep-08 → Mar-09** | 32 | **25.0%** | **−4.93%** | −13.7% |

The crisis nets positive ONLY because the 2009 recovery repays the 2008
crash. Through the worst six months the rule wins one trade in four and
loses ~5% on each. **My prediction was wrong on the aggregate and right on
the mechanism**: I said buying 2.25σ dips through 2008 would lose money, and
2008 alone is −2.49%/trade.

Two things the trend filter did do: it halved activity in the crash (142
trades vs 291 the year before), and it never produced a worse single trade
than −13.7% in 2008 — the −32.6% worst sits in 2007, not the crash.

## Consequence

The honest headline is NOT "the edge survives a crisis". It is: **the rule
loses roughly 5% a trade for six months and makes it back in the rebound.**
Whether that is acceptable is a funding question about surviving the
drawdown to collect the recovery, not a backtest question — it belongs in
FUNDING_GATES_V1 as a regime disclosure, and it is the single most useful
thing this desk has measured.

NEXT: re-run C0 like-for-like (2.25σ baseline, 2011+ only) before any claim
is adopted. The phase table above stands as diagnosis regardless.

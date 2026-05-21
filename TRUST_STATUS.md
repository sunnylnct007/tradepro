# TradePro — Trust Status

**Last updated:** 2026-05-21 (Decide-page surfaces)

Per-metric audit of every number a user sees on the Decide page. Each
metric is graded **🟢 Green / 🟡 Yellow / 🔴 Red** against this rubric:

| Grade | Meaning |
|---|---|
| 🟢 Green | Source verified · regression test exists · at least one bug-and-fix cycle survived · sensible to act on |
| 🟡 Yellow | Source known and code is correct under inspection · NO regression test OR recently changed OR a known caveat the user must read · use as input not decision |
| 🔴 Red | Known issue · don't act on this number until it moves to Yellow |

**Honest baseline today (2026-05-21):** the user cannot rely on TradePro
for unsupervised trading decisions. The verdict columns + bucketing
logic are the most-trusted layer; everything peripheral (R/R, swing
score under specific regimes, analyst feed) has gaps.

---

## Decide page — verdict layer

### Verdict / bucket (BUY / WAIT / AVOID)
🟡 **Yellow.** Authoritative pricing-rules layer; survived two
high-impact bug cycles (BABA trend-coherence 2026-05-20, BUY-near-
highs range-guard 2026-04). Currently the most-tested number on the
page. Caveats:
- Two reviewer-flagged miscategorisations in the last 30 days. Pattern
  suggests there are more edge cases not yet surfaced.
- Doesn't factor in upcoming earnings (no flatten-before-earnings rule).
- Uses Yahoo daily-close prices — stale or wrong adj_close cascades
  into a wrong verdict.

**Promote to Green when:** earnings-blackout rule lands AND multi-source
price consensus (Phase 6.8) is on.

Source: `strategies/tradepro_strategies/compare.py:compute_bucket`
Regression: `features/market_state_classify.feature` (8 scenarios)

### Top candidate card
🟢 **Green** (the *label*, not the recommendation).
After 2026-05-21 fix, when there's no BUY, the card explicitly labels
itself "Watchlist · closest to a buy (still WAIT/AVOID)" with an
italic disclaimer. The underlying symbol selection is just `bestRow`
sort — trivially correct.

Source: `frontend/src/pages/Compare.tsx:VerdictHeadline`

### Per-strategy "Long / Flat / Short" cell
🟢 **Green.** Direct read of the strategy's last-emitted signal.
Each of the 5 base strategies has its own regression coverage
(`features/*.feature` — sma_crossover, rsi_mean_reversion,
macd_signal_cross, donchian_breakout, ichimoku_cloud,
bollinger_bounce). Buy-and-Hold trivially correct.

---

## Decide page — composite scores

### Swing score (0-8)
🟡 **Yellow.** Pure aggregation of 4 sub-scores (quality / valuation /
event / price). The math is correct. But:
- Sub-scores depend on upstream data: weak/missing Sharpe → quality
  layer is 0. Weak/missing earnings → event layer is 0. Quietly
  scoring 0 vs explicitly "no data" hides a coverage gap.
- The `evaluate_swing` fallback path in horizons was buggy until
  2026-05-20 (Bug #11 fixture fix). Anyone reading the swing column
  in the last few weeks may have been seeing wrong numbers when
  composite-score data was incomplete.

**Promote to Green when:** missing-data states render as "—" instead
of silent 0.

Source: `strategies/tradepro_strategies/swing.py`
Regression: `features/horizons.feature` (6 scenarios)

### Best sharpe / cagr
🟢 **Green.** Computed from adj_close return series. Straightforward;
all 5y backtest stats use the same pipeline. No known bugs.

Source: `strategies/tradepro_strategies/stats.py`

---

## Decide page — price target / risk-reward (Ichimoku)

### Price target · stop · R/R
🟡 **Yellow** (after 2026-05-21 fix).
The "→ X · stop Y · R/R Z×" sub-row.
- Previously surfaced "R/R -4.0×" on MTUM (price already above the
  cloud → senkou_b sits below as support, not a target → negative
  reward → meaningless ratio). Fixed by nulling target + R/R when
  reward would be ≤ 0. Pinned with a regression scenario.
- **Caveat:** for ABOVE-cloud setups (the common bullish case),
  TradePro now shows NO target. That's honest but not actionable.
  A projection-based target (entry + 2× cloud-thickness or similar)
  would unblock this.

**Promote to Green when:** ABOVE-cloud setups show a sensible
projection-based target with documented methodology.

Source: `strategies/tradepro_strategies/strategies/ichimoku_cloud.py:ichimoku_targets`
Regression: `features/ichimoku.feature` (5 scenarios)

---

## Decide page — context badges

### "Xd stale" badge
🟢 **Green.** Reads `bestRow.data_age_days` directly. Trivially
correct. Threshold at 7 days is conservative.

### Universe pills + counts
🟢 **Green.** Just `filter().length`. No room for error.

### Sentiment-demotion banner
🟡 **Yellow.** Logic is correct (rule fires only when mean +
material-negative-count both clear thresholds). But:
- News coverage on UK / EU stocks is patchy via Finnhub — the
  demotion CAN fail to fire on real bad-news days simply because
  the news wasn't seen.
- No regression test pinning the exact thresholds.

---

## Research / Signals page

### Per-strategy scan (BUY / SELL / HOLD)
🟡 **Yellow** (after 2026-05-21 fix).
- Was showing "7 failed" on every strategy when Yahoo 404'd a typo'd
  ticker (VGGS instead of VGGS.L). Fixed: 404 now degrades to HOLD
  with "No data returned by the provider" reason.
- Symbol-resolution layer ("VGGS → VGGS.L?") not yet built — typing
  a UK ticker without `.L` still produces HOLD across all strategies.

### Price-history chart
🟡 **Yellow.** Loads via the same provider chain as the matrix. No
known issues but no regression coverage.

---

## Order flow — Approve / Reject

### Pending orders queue
🟢 **Green.** Postgres-backed (`pending_orders` table). Receives
intents from Mac engine in manual mode. State machine
Pending→Placed/Failed/Rejected enforced by SQL CHECK.

### Approve → T212 demo
🟡 **Yellow** (after 2026-05-21 fix).
- Approve was throwing JSON-parse errors on every call. The
  Trading212{,Demo}Client was parsing a TRUNCATED snippet (with "…")
  and crashing at byte 203. Fixed: parse full body, snip only for
  logs.
- Fill-price reconciliation: today we record the bar-at-emit close
  as the fill price, NOT T212's real fill. Likely off by a few bps
  on liquid names; potentially material on illiquid ones.

**Promote to Green when:** T212 order-stream subscription lands so
recorded fills match broker fills exactly.

### Approve → T212 LIVE
⚠️ Not in the user-facing flow today (demo-only path is wired). When
this lights up it will need its own row.

---

## Intraday automation (Task #69)

### Continuous-mode engine
🟡 **Yellow.** End-to-end claim → run → complete roundtrip verified
against the live API on 2026-05-20. But:
- Every order queues as Pending (manual mode), nothing reaches T212.
  By design until the pre-trade gate enforcement lands.
- Yahoo intraday bars are unreliable — smoke test got 0 bars for
  AAPL during US market hours. Polygon.io intraday (Phase 7.2) is
  the unblocker.

### Strategy leaderboard
🟡 **Yellow.** Just shipped today. SQL rollup is direct; tested by
hand against the one completed session. No regression coverage yet.

---

## What I'd act on today (TL;DR)

If you asked me which numbers I'd personally trust to inform a real
trade today, with the bar set at "would I bet £500 of my own money
on this":

| Surface | Bet £500? | Why |
|---|---|---|
| Per-strategy long/flat/short | **Yes** | Direct backtest output; well-tested |
| Verdict (BUY / WAIT / AVOID) | **Yes — but verify the bucket reason** | Recently bug-cleared; visible reason is the audit trail |
| Top buy (when bucket = BUY) | **Yes** | Just a sort over verified verdicts |
| Best sharpe / cagr ranking | **Yes** | Straightforward math, stable inputs |
| Swing score | **Read but don't lead with it** | Sub-score data gaps hide as 0; coverage isn't visible |
| Price target / R/R | **No** | Even after fix, no target shown for the common bullish case |
| Sentiment-demotion banner | **Read as confirmation, not signal** | Coverage gaps on EU/UK |
| Intraday engine output | **No** | Manual mode only; bar data unreliable |
| Symbol Deep Dive sections 5-10 | **Read for context** | Several sections still TODO |

---

## In-progress, not yet in this audit

Pages that exist but haven't been audited line-by-line:
- Portfolio
- Backtest / Simulations
- Paper page (live tab)
- Charts
- Symbol Deep Dive

Next audit pass will cover Portfolio + Symbol Deep Dive — those are
the next-most-decision-load-bearing surfaces.

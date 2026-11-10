# Sell‑off playbook — PRE‑REGISTERED gates, v1

**Committed BEFORE any run**, 10 Nov 2026. Grader: `backtests/studies/selloff_playbook_v1.py`
(written after this file; it grades these questions and emits NO signals — a study is not a
strategy until the gates say so).

## Why this study exists

Owner, 9–10 Nov 2026, after the post‑election AI/semiconductor sell‑off that took the live
book deep underwater: *"there was a decent opportunity to make money in this sell off … we
miss the boat completely"*; *"minimize loss is also a profit"*; *"the biggest mistake of
live account was too much exposure"*. OWNER BASICS 5 (sector cap 40–45%), 6 (cash reserve,
no naked puts) and 7 (read the stock against its sector and the market index) were set the
same day, and observations O3 (short‑term trades ride sentiment) and O4 (market/sector
events are key) were logged UNTESTED.

**What is NOT re‑asked.** O1 — "post‑earnings drops in good names recover" — was graded on
29 Aug 2026 (`earnings_recovery_v1.py`, commit a50dea8a): drops DO recover (+17% mean over
120 sessions, 65% positive) and still UNDERPERFORM the same stock bought on a random day
(−7pt at 120 sessions, behind at 3 of 4 horizons). The intuition measures market drift, not
the event. This study does not re‑run the single‑name earnings drop. It asks about the
**market / sector sell‑off** shape — the thing that actually happened in November.

## Definitions, frozen

* **Sector ETFs** (SPDR, GICS): XLB Materials · XLC Communication Services · XLE Energy ·
  XLF Financials · XLI Industrials · XLK Information Technology · XLP Consumer Staples ·
  XLRE Real Estate · XLU Utilities · XLV Health Care · XLY Consumer Discretionary.
  **Market**: SPY and QQQ. Membership of a stock = the `sector` field in
  `strategies/universe/tradeable.json` (sp500 GICS, `include: true`), mapped to the ETF above.
* **Episode** (per ETF): the first session whose close ≤ 0.92 × that ETF's rolling
  20‑session high (≥ 8% off a one‑month high). The next episode for the same ETF cannot start
  within 40 sessions. Severity tier A = 8–12% off, tier B = > 12%.
* **Market episode cluster**: episodes on different ETFs whose start sessions fall within 10
  sessions of each other are ONE cluster (a 2008 or 2020 crash is one event, not eleven).
  Every count below is reported both raw and clustered; gates use CLUSTERED counts where
  they say "episodes".
* **Trend condition** (quality proxy, the only one this repo has): the stock's close 20
  sessions BEFORE the episode start was above its own 200‑day SMA. This asks "was it in an
  uptrend before the sell‑off hit", not "is it fundamentally sound" — the same limitation
  the 29 Aug study stated, stated again.
* **Participation**: the stock's own drawdown from ITS 20‑session high at the episode close
  is ≥ the sector's (it sold off at least as hard as its sector).
* **Bars**: the settled daily bar store only. **Costs**: 10 bps per side on every arm,
  including null arms. **Universe**: `universe_symbols(harvested_only=False)` — the full
  tradeable list, NOT the 84‑name harvest flag (the flag limits what is fetched nightly, not
  what history exists).
* **Null arm** (the whole test, as in the 29 Aug study): the same stock, same horizon, same
  trend condition, entered at the close of a random session that is ≥ 40 sessions from any
  episode of its sector ETF and of SPY. Three draws per trade, averaged.

## S1 — the opportunity: buy constituents at the episode close

Entry at the close of the episode session, every constituent meeting trend + participation.
Hold **20 / 40 / 60** sessions, exit at the close. **No stop** — the question is recovery,
and a stop answers a different question (the 29 Aug lesson).

| # | Test | Threshold |
|---|------|-----------|
| S1‑0 | Trades | ≥ 300, across ≥ 8 sector ETFs and ≥ 15 clustered episodes |
| S1‑1 | Mean net return beats the null, 20 sessions | by ≥ +1.0pt |
| S1‑2 | Mean net return beats the null, 40 sessions | by ≥ +1.0pt |
| S1‑3 | Win rate at 40 sessions | ≥ 55% |
| S1‑4 | 5th‑percentile trade at 40 sessions | ≥ −15% |
| S1‑5 | Two‑split (episodes before/after 2016‑01‑01 × symbol halves by name) | edge vs null at 40 sessions > 0 in ALL FOUR cells |

S1 passes only if every row passes. **S1b** (reported, not gated): the same entry on the
sector ETF itself, and on SPY for market clusters.

## S2 — the owner's instrument: would a short put sold into the sell‑off have expired worthless?

No option history exists here, so the question is asked of the stock: a 10%‑OTM put with
21 sessions to expiry, sold at the episode close, expires worthless iff
close(T+21) ≥ 0.90 × close(T). Same trend + participation filter as S1. Null: same stock,
random non‑episode day. This is P(no assignment), not P&L — premium is not modelled.

| # | Test | Threshold |
|---|------|-----------|
| S2‑0 | Trades | ≥ 300 |
| S2‑1 | P(expires worthless) | ≥ 90% |
| S2‑2 | Beats the null P(expires worthless) | by ≥ +2.0pt |
| S2‑3 | Worst 21‑session close vs the 0.90 strike (max breach) | ≥ −20% below the strike |

## S3 — "minimize loss is also a profit" (basics 5 and 6) — DESCRIPTIVE, no gate

For every clustered episode, the 20‑session forward drawdown (worst close vs entry) of three
books held from the episode‑start session − 20 (i.e. the book you already had when the
sell‑off hit):
* **Concentrated**: 100% in the ETF of the sector that sold off hardest in the cluster;
* **Capped**: 45% in that ETF, 55% equal‑weight across the other ten sector ETFs;
* **Capped + reserve**: the capped book at 80%, 20% cash.
Report median and worst drawdown of each, and the avoided loss (concentrated − capped).
No gate: a diversified book having a smaller drawdown is not a hypothesis, it is arithmetic;
the number is reported so the cost of November's concentration is on record.

## S4 — basic 7, the correlation lens — DESCRIPTIVE, no gate

For S1 trades, the stock's 63‑session beta to its sector ETF measured over the window ending
20 sessions BEFORE the episode (no look‑ahead). Report S1's 40‑session edge vs null by beta
tercile (low / mid / high). If the high‑beta tercile carries the edge, basic 7's "read it
against the sector" has a measurable use in sell‑offs; if the edge is flat across terciles,
beta is not the lens.

## Hazards named before the run

* **Survivorship**: `tradeable.json` is today's list. Names that failed in a past sell‑off
  are absent, which flatters every recovery number, null arms included. The edge vs null is
  partly protected; the raw returns are not.
* **Coverage**: 98 of the original 244 names are capped near 4 years by IBKR's 1000‑bar
  limit; every episode a stock cannot be graded for is COUNTED and reported.
* **Overlap**: one market crash hits every sector ETF; clustering (above) is how it is
  counted once. Raw counts are shown beside clustered so the clustering cannot hide.
* **Regime**: 2006–2026 is mostly one long bull market with sharp interruptions. The time
  split in S1‑5 is the only defence; it is a weak one.

## Predictions, recorded before the run

* S1 raw returns positive at every horizon (the market rose). Versus null: a SMALL positive
  edge at 20 sessions (sharp sector drops mean‑revert over weeks), fading by 60. I expect S1‑1
  to pass and S1‑5 to FAIL on at least one cell.
* S2: P(expires worthless) BELOW the null — a stock that has just fallen ≥ 8% with its sector
  is more likely to fall another 10% in a month than one picked on a quiet day. I expect
  S2‑2 to fail. If it passes, the owner's instinct that November was a put‑selling
  opportunity is supported on this data.
* S3: the capped book's median 20‑session drawdown roughly half the concentrated book's.
* S4: edge concentrated in the high‑beta tercile.

## Status — RUN ONCE, 10 Nov 2026 (grader `selloff_playbook_v1.py`, artifact `~/.tradepro/research/selloff_playbook_v1.json`)

Episodes: 399 raw / 133 clustered across 13 ETFs (2006–2026). Trades: **4,143** across 11
sector ETFs and 117 clusters (tier A 3,998 / tier B 145). Not graded, counted: 5,405 failed
the trend condition, 3,557 did not participate, 2,224 episode dates outside the stock's bars
(coverage cap), 632 insufficient history/future.

**S1 — FAILS** (S1‑0 PASS, S1‑1/2/4/5 FAIL, S1‑3 PASS)

| h | mean | null | edge | win | p5 | worst |
|---|------|------|------|-----|----|-------|
| 20 | +2.55% | +2.19% | **+0.36pt** | 59.5% | −19.3% | −60.3% |
| 40 | +3.45% | +4.41% | **−0.96pt** | 58.2% | −24.3% | −81.8% |
| 60 | +6.74% | +5.40% | +1.34pt | 63.5% | −22.5% | −86.6% |

Two‑split edge@40: pre2016×A −2.50 · pre2016×B +1.25 · post2016×A −1.10 · post2016×B −1.31.
Buying constituents into a sector sell‑off recovers — and does not beat buying the same stock
on a quiet day. The sign flips across horizons and cells: there is no edge here to size.
S1b (the ETF itself) is positive at every horizon for every ETF but XLP@20 and XLV@40 — the
same drift the null measures.

**S2 — FAILS, and by a lot.** P(10%‑OTM 21‑session put expires worthless) = **86.0%** vs
**96.1%** on the null (−10.1pt). Worst close vs strike: −70%. **Selling puts into a sector
sell‑off is the single worst time to sell them on this data** — one in seven finishes through
a strike that a quiet day breaches one time in twenty‑five. This is the November book, graded.

**S3 — descriptive**, 130 clusters, 20‑session drawdown of the book already held when the
sell‑off hit: concentrated median **−7.3%** (worst −51.4%) · capped 45% median **−4.7%**
(worst −39.2%) · capped + 20% cash median **−3.8%** (worst −31.4%). Median avoided loss from
the cap alone: 2.25pt; from cap + reserve: ~3.6pt. Basics 5 and 6 priced.

**S4 — descriptive**: edge vs null by pre‑episode beta tercile — low −0.78/−2.40, mid
−0.30/−1.36, **high +2.16/+0.83** (20/40 sessions). What edge exists in a sell‑off sits in
the high‑beta names; the lens in basic 7 has a measurable use, the trade still fails the gates.

**Predictions vs outcome**: S1‑1 predicted PASS → **FAILED** (edge +0.36 < 1.0); S1‑5 predicted
FAIL → failed; S2 predicted FAIL → failed, far harder than predicted; S3 predicted ~half →
64%; S4 predicted high‑beta → confirmed.

**Decision**: no sell‑off entry rule ships. What ships is the negative: the options screens
should treat "sector ETF ≥ 8% off its 20‑session high" as a NAMED risk on every short‑put
row (like an event), because S2 says the base rate of assignment is 3.5× a quiet day's.

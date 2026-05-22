# TradePro — Data Coverage Roadmap

External reviewer feedback (May 2026): TradePro has strong methodology
(ensemble voting, regime-aware backtests, transparency) but is missing
load-bearing data points a serious trading platform needs. This doc
inventories every data category, marks what we have, what's missing,
and sequences the close-the-gap work.

> Companion to `ROADMAP.md` — that file owns shipping order; this file
> owns the **data shape** that every shipping phase needs to consume.

**Legend**
- ✅ Shipped + production-quality
- 🟡 Partial — some data, gaps in coverage / freshness / accuracy
- 🔴 Missing entirely
- 💰 Cost notes: free / paid / "talk to vendor"

---

## How to read this doc

Each category has four sub-sections:

1. **Why it matters** — the decisions this data feeds. If a category
   doesn't change a verdict, we don't add it.
2. **What we have today** — current sources + freshness + coverage.
3. **What's missing** — specific data points + their downstream
   consumer (which strategy / view needs them).
4. **Provider + cost shortlist** — vendors evaluated, ranked by
   $/leverage. Always at least one free option even if quality is
   degraded — for users who can't justify paid feeds yet.

The categories below are roughly ordered by **decision impact per $**
— top of list = biggest correct-decision lift for least effort/cost.

---

## 1. Pricing & quotes — daily bars

**Why it matters.** Every strategy, every regime calc, every backtest
reads this. Wrong prices = wrong everything; this MUST be the most
robust feed in the stack.

**What we have today**
- ✅ Yahoo Finance (free, free-tier quota) — primary daily-bar source.
  Adjusted + unadjusted close, OHLC, volume, dividends, splits.
- ✅ EODHD EOD All World (€19.99/mo) — secondary; planned consensus
  validation per Phase 6.8.
- ✅ Auto-fallback chain: yfinance → Finnhub → Stooq, with logging.
- ✅ Split & dividend adjustments — adjusted-close used for trend
  math; unadjusted for trade simulation.

**What's missing**
- 🟡 **Multi-exchange coverage gaps.** Yahoo's UK and EU coverage
  is patchy: `VGGS.L` works but `VGGS` 404s (user typed without `.L`
  and got 7 strategy failures). Need: a symbol-resolution layer that
  knows "VGGS" on UK = "VGGS.L" on Yahoo, and falls back to other
  providers if one source is missing.
- 🟡 **Adjusted-close discrepancies between providers.** A single
  reverse-stock-split can put Yahoo's adj_close 5% off Stooq's for
  weeks. Phase 6.8 multi-source consensus catches this — not yet
  wired into the comparator critical path.
- 🔴 **Pre/post-market quotes.** Trading platforms need extended-hours
  pricing to evaluate gap risk before a 9:30 ET open. Currently
  absent entirely.
- 🔴 **ADR / local-share cross-references.** BABA ADR vs 9988.HK
  local share — same underlying, different supply/demand. Reviewer
  caught this in the BABA case study. Need an `instrument_link`
  table.

**Provider shortlist**
| Provider | Cost | Notes |
|---|---|---|
| Yahoo + Stooq (current) | Free | Best free combo; gaps in EU, no pre/post-market |
| EODHD EOD All World | €19.99/mo | Wider EU coverage, pre/post-market on US |
| Tiingo | $10/mo IEX feed | US-only but cleaner adjustments |
| Polygon.io stocks starter | $30/mo | US tick + 1m, no EU |
| Alpaca | Free w/ broker | US-only, real-time when integrated |

**Sequencing**
- 6.8a (next) — wire EODHD into the consensus layer, surface per-bar
  quality flags on the row.
- 6.8b — symbol-resolution layer ("VGGS → VGGS.L on UK exchange").
- 7.1 — pre/post-market quotes for gap-risk on holdings.

---

## 2. Pricing & quotes — intraday ticks

**Why it matters.** Intraday automation (Task #69) needs real
1m / tick bars to evaluate ORB / VWAP / Bollinger entries. Today the
engine uses Yahoo intraday which is replay-only and hit-or-miss.

**What we have today**
- 🟡 `YfinanceIntradayBus` — fetches one full session's bars then
  replays. Hit rate low: in this session's smoke test, AAPL returned
  0 bars during US market hours.
- 🔴 No live tick stream. No bid/ask. No order-book depth.

**What's missing**
- 🔴 **Reliable 1m bars** for the live US session.
- 🔴 **Streaming** so the engine doesn't pre-fetch then replay.
- 🔴 **Bid/ask spread.** The pre-trade gate's `maxSpreadPct` check
  is currently inert because no spread is available — it's a config
  field with no input.
- 🔴 **Volume profile / cumulative volume delta** — needed for VWAP
  strategies to fire accurately.

**Provider shortlist**
| Provider | Cost | Notes |
|---|---|---|
| Polygon.io stocks starter | $30/mo | US: tick + 1m + L1 quotes. Best $/feature. |
| Alpaca Market Data | Free w/ broker | US-only, real-time once we have an account |
| IEX Cloud | $9/mo (Launch) | Limited free tier, US-only |
| IBKR (live account) | Free w/ funded account | US + global, requires gateway |
| Trading 212 stream | N/A | T212 has no public market-data stream |

**Sequencing**
- 7.2 — wire Polygon.io intraday. Pre-trade gate's spread + R/R
  inputs become real numbers. **Prerequisite for trusting auto-place.**
- 7.3 — backtest validator over 30 days of Polygon ticks per
  watchlisted symbol. Gates Task #69's auto-place rollout.

---

## 3. Fundamentals & valuation

**Why it matters.** A "BUY on Lucid because price dipped 30%" recommendation
is wrong if you don't see the cash burn / dilution / declining revenue.
This is the biggest current gap vs Stock Rover / Morningstar.

**What we have today**
- 🟡 `valuation_flag` — basket-relative cheap / fair / expensive
  using P/E (stocks) or dividend yield (ETFs). Honest but coarse.
- 🟡 Forward P/E from Finnhub on Compare row.
- 🟡 Dividend yield, expense ratio (ETFs), holdings count.

**What's missing**
- 🔴 **Full income statement** (annual + quarterly) — revenue, gross
  margin, operating margin, net income, EPS basic + diluted.
- 🔴 **Balance sheet** (annual + quarterly) — total assets, total
  debt, cash, working capital, book value per share, share count.
- 🔴 **Cash flow** (annual + quarterly) — operating CF, free CF,
  capex, buybacks, dividends paid.
- 🔴 **Multi-period growth metrics** — revenue CAGR 3y/5y, EPS
  growth, gross-margin trend.
- 🔴 **Valuation ratios beyond P/E**: P/B, P/S, EV/EBITDA, EV/Sales,
  FCF yield, PEG.
- 🔴 **Quality scores**: ROIC, ROE, ROA, debt/equity, current ratio,
  interest coverage, Altman Z-score, Piotroski F-score.
- 🔴 **Historical valuation context**: current P/E vs 5-year avg /
  vs sector / vs index. Resolves the "is 30 P/E expensive?" question
  without arbitrary thresholds.
- 🔴 **Per-share metrics history**: EPS, BVPS, FCF/share, sales/share
  — for trend analysis.
- 🔴 **Dividend history + sustainability**: payout ratio, FCF
  coverage, dividend-aristocrat status, cuts.
- 🔴 **Share-count history** — dilution detection (key for tech
  growth stories) + buyback tracking.

**Provider shortlist**
| Provider | Cost | Notes |
|---|---|---|
| Finnhub Pro | $50/mo | Has most of the above; weaker on history depth |
| SimplyWall.St API | $30-80/mo | Snowflake scores + 10y history |
| FMP (Financial Modeling Prep) | $14-49/mo | Best $/coverage for ratios |
| Stock Analysis API | $59/mo | Very deep history, clean schema |
| EODHD Fundamentals | €29.99/mo | Bundles with our existing EODHD sub |
| Tiingo Fundamentals | $30/mo | Quarterly history, US-focused |

**Sequencing**
- 8.1 (Phase 6.12) — pick provider, ingest "core 8": revenue, net
  income, FCF, share count, total debt, cash, ROE, gross margin.
  Per-quarter for the last 12Q.
- 8.2 — add valuation-vs-history (current P/E vs 5y avg) — kills
  the "30 P/E is bad" arbitrary thresholding.
- 8.3 — Piotroski F-score / Altman Z-score as composite quality
  flags. Surface on Symbol Deep Dive.
- 8.4 — dividend sustainability (payout ratio trajectory + FCF
  coverage) for income-strategy fans.

---

## 4. Earnings & guidance

**Why it matters.** ~70% of single-name moves >5% happen on earnings.
Trading the wrong side of earnings = the most expensive mistake
retail makes.

**What we have today**
- 🟡 `historical_earnings` on row — reported dates, surprise%, EPS
  actual + estimate. From Finnhub.
- 🟡 `earnings_signal` family-4 strategy — beat-and-retreat verdict.
- 🟡 Sparse next-earnings date (Section 7 of Deep Dive) — best-effort.

**What's missing**
- 🔴 **Reliable forward earnings calendar** — next print date,
  ESTIMATED time of day, "before market open / after close".
  Critical for "don't enter 3 days before earnings" rule.
- 🔴 **Consensus EPS & revenue estimates** for the upcoming print.
- 🔴 **Estimate revision trend** (last 4 weeks): "consensus has
  been raised / cut 6 times in the last 30 days". Predictive.
- 🔴 **Whisper number** — informal estimates above/below consensus.
- 🔴 **Guidance changes** — when a company raises/cuts guidance
  outside the print itself. Material price-mover.
- 🔴 **Earnings-call sentiment** — LLM-summarised call tone +
  forward-looking statements. Differentiator vs free tools.
- 🔴 **Conference call schedule** — investor day, analyst day, etc.

**Provider shortlist**
| Provider | Cost | Notes |
|---|---|---|
| Finnhub Earnings Calendar | Free / $50 paid | Has dates + estimates; revisions in paid |
| Benzinga Earnings API | $50/mo | Better revision history |
| Estimize | $99/mo+ | Crowdsourced whisper numbers |
| Refinitiv I/B/E/S | Talk to vendor | Gold-standard consensus, $$$ |
| Seeking Alpha API | $49/mo | Call transcripts |

**Sequencing**
- 8.5 (Task #66) — forward earnings date on every Compare row.
- 8.6 — consensus EPS + estimate revisions trend.
- 8.7 — pre-earnings position-flatten rule wired into intraday
  engine (configurable: "flatten X days before earnings").
- 8.8 — call-transcript LLM summary on Symbol Deep Dive.

---

## 5. Analyst coverage

**Why it matters.** Aggregate "the Street" view as a single conviction
signal. Already partially wired — needs depth.

**What we have today**
- ✅ `external_consensus.target_mean` — analyst mean price target
  on Compare row.
- 🟡 Buy/Hold/Sell counts in `analyst_recommendations`.
- 🔴 Upgrades/downgrades feed — Task #71 audit shows Finnhub is
  dropping events (BABA upgrade missed).

**What's missing**
- 🔴 **Upgrade/downgrade feed reliability.** Audit task #71 open.
- 🔴 **Target-price revision trend** — "12 analysts raised, 2 cut
  in last 30 days". More signal than the current snapshot.
- 🔴 **Per-analyst track record** — weight upgrades from analysts
  with high hit rate. Differentiator.
- 🔴 **Initiation coverage events** — "JPM initiates with OW" is
  a discrete event with predictable post-event drift.
- 🔴 **Buyside vs sellside split** — when fund-of-funds disagree
  with the bulge-bracket consensus.

**Provider shortlist**
| Provider | Cost | Notes |
|---|---|---|
| Finnhub (current) | Free / paid | Reliability gap per task #71 |
| TipRanks API | $35/mo | Per-analyst track records, gold for differentiation |
| Benzinga Ratings | $30/mo | Cleanest upgrade/downgrade feed |
| Refinitiv | Talk to vendor | Most complete, $$$ |

**Sequencing**
- 6.11 (Task #71) — fix the Finnhub upgrade-feed dropouts FIRST.
- 8.9 — add TipRanks per-analyst track records.
- 8.10 — target-revision-trend layer (gradient over time).

---

## 6. Macro & sector context

**Why it matters.** A stock in a falling sector with a rising market
behaves differently from one rising with both. Strategies that ignore
context misfire systematically in regime changes.

**What we have today**
- 🔴 None. The comparator looks at the symbol in isolation.

**What's missing**
- 🔴 **Sector ETF performance** — XLF, XLK, XLE, etc. relative
  strength and trend.
- 🔴 **Industry classification** (GICS sector + industry) per
  symbol — for peer grouping that's not hardcoded.
- 🔴 **Market breadth** — % of S&P 500 above 200dma, advance/decline
  line. Identifies thin rallies.
- 🔴 **Yield curve** — 10y-2y spread, inversion duration. Macro
  regime input.
- 🔴 **VIX + term structure** — risk-on/off proxy.
- 🔴 **DXY (dollar index)** — affects EM, multinationals.
- 🔴 **Commodities**: oil, gold, copper — sector-relevant.
- 🔴 **Fed policy state** — current rate, last change, dot-plot
  forward path. From FRED.
- 🔴 **Inflation prints** — CPI, PCE, with surprise vs consensus.

**Provider shortlist**
| Provider | Cost | Notes |
|---|---|---|
| FRED API | Free | Fed rates, inflation, yields, money supply. Gold standard. |
| Yahoo (current) | Free | Has sector ETFs + VIX + DXY |
| EODHD Economic Calendar | €19.99/mo | Adds the "scheduled event" layer |
| Trading Economics | $50-200/mo | Calendar + history + forecasts |

**Sequencing**
- 9.1 — FRED ingest: 10y-2y, Fed funds, CPI. Surface on Symbol
  Deep Dive header as a "macro context" pill.
- 9.2 — sector ETF relative-strength per symbol's GICS sector.
- 9.3 — market-breadth indicators on Decide page.

---

## 7. Sentiment & news

**Why it matters.** Already shipped, but coverage + sources can deepen.

**What we have today**
- ✅ Finnhub news with LLM sentiment scoring.
- ✅ Sentiment-driven BUY→WAIT demotion (configurable thresholds).
- 🟡 Coverage gaps on UK / EU stocks (Finnhub US-biased).

**What's missing**
- 🔴 **Social-media sentiment**: Reddit (WSB, individual stock
  subreddits), Twitter/X, StockTwits. Retail crowd activity is
  predictive for meme-prone names.
- 🔴 **Search-trend data** (Google Trends API) — broader interest
  proxy, leads price moves on some categories.
- 🔴 **Press-release feed** — corporate announcements that don't
  hit the news wire (8-K filings, dividend announcements).
- 🔴 **EU/UK news depth** — Yahoo/Finnhub thin on FTSE-listed
  companies vs US.

**Provider shortlist**
| Provider | Cost | Notes |
|---|---|---|
| Finnhub (current) | Free / paid | US-strong, UK-weak |
| StockTwits Free | Free | Retail sentiment, US |
| RavenPack | Talk to vendor | Institutional-grade; $$$ |
| NewsAPI.org | $0-449/mo | Broader EU coverage |
| Pushshift Reddit | Free (rate-limited) | DIY social aggregation |

**Sequencing**
- 9.4 — StockTwits sentiment add-on.
- 9.5 — NewsAPI for EU/UK coverage gap fill.

---

## 8. Microstructure

**Why it matters.** Intraday execution quality. Needed once Task #69
auto-place is live and we're getting real fills.

**What we have today**
- 🔴 None.

**What's missing**
- 🔴 **Bid/ask spread** — pre-trade gate's `maxSpreadPct` reads it.
- 🔴 **Level-2 order book** — depth at each price level.
- 🔴 **Trade prints / time & sales** — block-trade detection.
- 🔴 **Dark pool prints** — TRF data, hints at institutional
  positioning.
- 🔴 **Short interest + days-to-cover** — squeeze risk.
- 🔴 **Borrow availability + rate** — for any short-side strategy.

**Provider shortlist**
| Provider | Cost | Notes |
|---|---|---|
| Polygon.io stocks | $30-300/mo | L1 in starter, L2 in higher tiers |
| Alpaca | Free w/ broker | L1, no L2 |
| IBKR | Free w/ funded acct | L2 with subscription |
| FINRA Short Volume | Free | Daily file; lags T+2 |
| S3 Partners | Talk to vendor | Real-time borrow rate; $$$ |

**Sequencing**
- 7.4 — Polygon L1 quotes for spread (gates auto-place).
- 10.1 — FINRA short volume daily ingest.
- 10.2 (later) — L2 + dark prints, after auto-place is mature.

---

## 9. Risk & portfolio metrics

**Why it matters.** Single-position recommendations are necessary but
not sufficient. The Phase 2 (portfolio-aware) phase needs this layer.

**What we have today**
- ✅ Per-symbol stats (Sharpe, max drawdown, recovery time, CAGR).
- 🔴 Cross-asset / portfolio-level risk metrics absent.

**What's missing**
- 🔴 **Beta** to a configurable benchmark (^SPX, ^FTSE).
- 🔴 **Correlation matrix** across the holdings + watchlist.
- 🔴 **Portfolio VaR / Expected Shortfall** at 95 / 99%.
- 🔴 **Sector concentration metrics** — "you're 47% tech".
- 🔴 **FX exposure** for non-base-currency holdings.
- 🔴 **Volatility surface** (for any options layer later).

**Provider shortlist**
- All of these can be **computed in-house** from existing price data
  — no new provider needed. Engineering work, not data spend.

**Sequencing**
- 11.1 (Phase 2) — beta + correlation matrix when the portfolio-
  aware engine lands.
- 11.2 — portfolio-VaR using historical simulation.

---

## 10. Catalyst / event awareness

**Why it matters.** "BUY on dip" reasoning ignoring an FDA approval
miss / SEC investigation / dividend cut is the institutional-tools
gap reviewer flagged.

**What we have today**
- 🟡 Earnings calendar (sparse).
- 🔴 Everything else.

**What's missing**
- 🔴 **FDA decision calendar** — biotechs.
- 🔴 **Ex-dividend dates** — affects total return modelling.
- 🔴 **M&A announcements** (SEC 8-K filings).
- 🔴 **Investor-day / analyst-day calendar.**
- 🔴 **Stock splits / reverse splits** — pricing-display issues.
- 🔴 **Product-launch calendar** (manual / curated).
- 🔴 **Regulatory events** — central bank meetings, OPEC meetings.

**Provider shortlist**
| Provider | Cost | Notes |
|---|---|---|
| Benzinga Catalyst API | $50-100/mo | FDA / M&A / splits in one feed |
| EODHD Economic Calendar | €19.99/mo | Macro + corporate events bundled |
| Estimize Calendar | Free / paid | Earnings + some corporate |
| SEC EDGAR | Free | DIY 8-K parser; we have ROADMAP item for this |

**Sequencing**
- 12.1 — SEC EDGAR 8-K parser (already in ROADMAP).
- 12.2 — Benzinga Catalyst for FDA + M&A.

---

## 11. Order, execution, and broker data

**Why it matters.** Task #69 needs this to do real execution well.

**What we have today**
- ✅ Trading 212 demo + live integration (positions, orders).
- ✅ Order pending-queue + Approve/Reject flow.
- ✅ Event-sourced orders + fills + audit trail.

**What's missing**
- 🔴 **Trade-history reconciliation** — Mac engine fills vs T212's
  actual fills. Today recorded at emit-close; T212's real fill
  could differ.
- 🔴 **Tax-lot accounting** — which lot is being sold (FIFO / LIFO /
  HIFO). Matters for UK CGT.
- 🔴 **Buying power / margin tracking** — multi-broker aggregation.
- 🔴 **Slippage measurement** — tracking realised vs expected fill
  to feed slippage models back into strategy sizing.
- 🔴 **IBKR support** — currently stubbed.
- 🔴 **Multi-currency portfolio view** — GBP + USD + EUR aggregated.

**Sequencing**
- 13.1 — T212 order-stream subscription for true fill prices.
- 13.2 — IBKR live integration (already in registry as stub).
- 13.3 — slippage telemetry per (strategy, symbol) pair.

---

## 12. Alternative / specialty data

**Why it matters.** Mostly "nice to have" layer. Each is high-leverage
in narrow situations.

**What's missing**
- 🔴 **ETF flows** (creations/redemptions) — strong leading indicator
  on sector rotation.
- 🔴 **13F filings** — institutional holdings, lagged.
- 🔴 **Insider transactions** — Form 4 filings, near-real-time.
- 🔴 **Web traffic / app downloads / sales-data feeds** (Sensor
  Tower, Yipit) — leading economic indicators per company.
- 🔴 **Credit-card transaction panels** (Earnest, Yipit) — retail
  revenue nowcasts.
- 🔴 **Satellite imagery** (parking lots, oil tanks) — institutional
  edge.

**Most of these are out of scope for retail.** Listed for completeness;
the "useful for us" set is:

- 14.1 — ETF flows for sector ETFs (FactSet has a $30-tier API).
- 14.2 — Insider transactions (Form 4) — free via SEC EDGAR.
- 14.3 — 13F lag-aggregation — free via SEC; quarterly.

---

## 13. LLM pipeline — two-phase financial language understanding

**Why it matters.** Today's news-sentiment scoring uses a general
LLM (Ollama-served), which is fine for "is this article bullish or
bearish" but not for the finance-specific reasoning a trading
platform should do (FOMC tone, earnings-call guidance language,
8-K material-event classification).

**Phase A — Ollama / general-purpose LLM (current).**
- ✅ Local Ollama for news sentiment + Compare explainer banner.
- 🟡 Quality: decent for blunt sentiment, weak on financial nuance
  (mistakes "raised guidance" for neutral, can miss buried
  language like "headwinds expected").
- ✅ Free + on-device; no per-token cost.

**Phase B — FinBERT (planned).**
- 🔴 FinBERT (Hugging Face: `ProsusAI/finbert`) — BERT fine-tuned
  on financial corpus. Classifier-style, not generative — returns
  {positive, neutral, negative} with calibrated confidence on
  financial language specifically. Better for sentiment classification.
- Other domain models worth considering: FinGPT (instruction-
  tuned for finance), DeBERTa-finance, Llama-finance variants.

**What FinBERT needs (data inputs).**
- 🟡 News headlines + bodies — have via Finnhub; UK/EU gap remains.
- 🔴 SEC EDGAR 10-K / 10-Q / 8-K filings — text of recent material
  events. Free; needs an ingest worker.
- 🔴 Earnings-call transcripts — Seeking Alpha API ($49/mo) or
  Refinitiv ($$$). Differentiator for forward-looking sentiment.
- 🔴 Press releases — PR Newswire feed (paid) or SEC 8-K parser
  (free, lagged).
- 🟡 Existing news sentiment scoring stays on Ollama as the
  baseline; FinBERT runs alongside as a separate score column so
  we can A/B before promoting.

**Sequencing**
- 15.1 — Wire FinBERT as a second sentiment-scorer over the same
  Finnhub news payload. Side-by-side scoring on the Compare row
  (`sentiment_ollama` vs `sentiment_finbert`) so we can compare
  on real data before deciding which to lead with. Local
  inference; no new $$$.
- 15.2 — SEC EDGAR 8-K ingest worker (free) → FinBERT classifies
  material events as bullish / bearish / neutral. Feeds the
  catalyst-flag layer (category 10).
- 15.3 — Earnings-call transcript ingest (Seeking Alpha $49/mo)
  + FinBERT per-segment classification. Surfaces "guidance
  raised on Q3 call" type signal on the Deep Dive.
- 15.4 — Ollama vs FinBERT divergence dashboard — when the two
  models disagree on the same article, surface for human review.
  Productionising one fine-tuned classifier without measuring
  divergence is how silent regressions slip in.

---

## 13.5 ★ Catalyst overlay — the single biggest TradePro gap

**Reviewer + user verdict 2026-05-21:** TradePro has solid technicals
(MACD/RSI/Ichimoku, regime history, backtests) but **zero catalyst
awareness**. Pure-technical signals systematically miss event-driven
moves — and ~70% of single-name moves >5% happen on news / earnings
/ regulatory events. A trading platform that can't see catalysts is
at best a passive-investing tool, not a real trading tool.

**The Ecopetrol (EC) example.** Technicals said WAIT (89th percentile
of 52w range — correct in isolation). The real trade:
- Colombia election in 10 days
- Oil at $105
- MACD just fired
- Stock near highs but **with catalyst**
- = BUY with defined risk, tight stop, dated catalyst expiry (June 21 runoff)

TradePro missed the entire reason the trade existed.

**The fix in one paragraph.** A "Catalyst" sub-row on every Compare
row + a dedicated section on Symbol Deep Dive. Don't replace the
technical bucket — annotate it. User wants BOTH views (technical
isolated + catalyst-augmented) so they can reason about why they
disagree.

**The shipping target — combined verdict shape.**
```
Technical signal:   WAIT  (89th percentile)
News catalyst:      STRONG BUY (election 10d, oil $105)
Analyst flow:       MIXED (1 buy, 4 sell)
Combined verdict:   BUY with tight stop
Confidence:         Medium-High
Catalyst expiry:    June 21 (runoff date)
```

**Data ingredients (some already on roadmap, here in priority order).**

1. **News headlines per ticker** — 🟡 partial via Finnhub. Add NewsAPI.org ($0-449/mo) for EU/UK coverage; GDELT (free) for geopolitical event coverage at index level. **Phase 17.1.**
2. **LLM sentiment scoring** — ✅ shipped (Ollama). Phase B will add FinBERT side-by-side (§13). Score range -1 to +1; surface per-headline AND per-ticker aggregate.
3. **Political/regulatory event calendar** — 🔴 missing. GDELT covers elections, central-bank meetings, OPEC, geopolitical events. Free; needs an ingest worker that filters to events relevant to held / watched tickers. **Phase 17.2.**
4. **Earnings calendar + surprises** — 🟡 sparse via Finnhub free tier. Upgrade to paid for reliable forward dates + estimate revisions. Already in §4. Critical: "flatten N days before earnings" rule. **Phase 17.3.**
5. **Analyst upgrades/downgrades** — 🔴 Finnhub dropping events (Task #71). Either audit + replace provider OR de-duplicate via second source (TipRanks). **Already Task #71 — bump priority.**
6. **Commodity context** — 🔴 missing. Yahoo has the symbols (`CL=F` oil, `GC=F` gold, `HG=F` copper) — needs ingest + sector-relevance map (oil price matters for energy stocks, gold for miners, etc.). **Phase 17.4.**
7. **Macro regime detection** — 🔴 missing. FRED for rates, yield curve, CPI; classify into regime buckets (risk-on / risk-off / rates-up / inflation-spike). Already in §6. **Phase 17.5.**

**Sequencing — the catalyst sprint.**

| Phase | What ships | Cost | Effort |
|---|---|---|---|
| 17.1 | NewsAPI + GDELT ingest, per-ticker headline+catalyst extraction, scored sentiment | $0-50/mo | 1 week |
| 17.2 | Dated-catalyst extractor (elections, FOMC, OPEC, earnings) + expiry-date storage | Free | 3 days |
| 17.3 | "Catalyst" sub-row on every Compare row: top-3 catalysts + sentiment + expiry. Behind a 🟡 trust dot until validated. | Reuses above | 3 days |
| 17.4 | Commodity-context lookup per symbol (oil for energy, gold for miners, FX for multinationals) | Free | 2 days |
| 17.5 | **Combined verdict line** — technical bucket + catalyst overlay → single combined recommendation on Symbol Deep Dive. The headline output shape from this section. | Reuses above | 3 days |
| 17.6 | FRED macro regime classifier | Free | 3 days |

**Total: ~3 weeks for the full overlay sprint at <$50/mo new data spend.**

**Trust grading at launch.** Every new Catalyst row / Combined verdict
ships at 🟡 Yellow (in-progress / validating) — never silently override
the technical bucket. User-facing copy will read "Catalyst overlay
suggests BUY despite technical WAIT — review reasoning before
acting" until we have enough completed-trade data to validate the
combined-verdict accuracy vs single-layer.

**Why this jumps the queue.** Two converging signals:
- User can't make trading decisions on TradePro today (2026-05-21
  feedback — see TRUST_STATUS.md).
- Concrete missed trade (EC) demonstrates the gap costs real money.
This is the trust gap closure that matters most.

---

## 14. Product UX — two distinct usability modes

**Why it matters.** TradePro today mixes intraday signals + medium/
long-term ETF picks on the same surfaces. Users in different modes
want different defaults, different metrics, and different risk
posture — but right now there's no top-level switch saying "I'm
trading intraday today" vs "I'm investing for 3-5 years". User
feedback 2026-05-21: "we need to ensure and make it visible 2 diff
usability of tradepro. one the intraday trading and one medium and
long term trading."

**The split.**
| Aspect | Intraday mode | Medium / Long-term mode |
|---|---|---|
| Default page | `/intraday/leaderboard` (just shipped) | `/compare` (Decide) |
| Strategy menu | ORB, VWAP, Bollinger, MA crossover (1m bars) | SMA crossover, RSI mean-rev, MACD, Donchian, Ichimoku, Buy & Hold (daily bars) |
| Timeframe | 1m / 5m | 1d / 1w |
| Verdict horizon pills | "Today / this session" | "Swing 1-8w / Long-term 6-18mo / Passive 3-5y" |
| Risk preset | Tight stop, per-trade $ cap, max 4hr hold | Position sizing, max drawdown tolerance |
| Backtest window default | last 30 days | last 5 years |
| Price-feed dependency | Polygon.io (intraday) | Yahoo / EODHD (EOD) |
| Auth & approval flow | Pre-trade gate + auto-place threshold | Always manual; weekly review cadence |

**Today.** Most surfaces serve the medium/long-term user. Intraday
work is segregated under Settings → Intraday + `/intraday/leaderboard`.
A user has to manually piece together what's relevant for each mode.

**Sequencing**
- 16.1 — Top-of-page mode switcher pill (Intraday / Long-term),
  persisted in localStorage. Compare / Portfolio / Symbol Deep
  Dive all read this and adjust their default tab / timeframe /
  metric column selection. ~2 days.
- 16.2 — Mode-aware defaults in Settings (per-mode strategy
  toggles, per-mode risk presets). ~1 day.
- 16.3 — Mode-aware decision-trace: long-term mode hides the
  intraday-specific rule fires and vice versa. ~1 day.
- 16.4 — Mode-aware backtest defaults (window length, tick
  granularity). ~1 day.
- 16.5 — Onboarding tour that asks "what kind of trading?" first
  and sets the mode. ~0.5 day.

---

## Massive roadmap — sequenced delivery plan

Reading the categories above, the **highest-leverage / lowest-cost** set
ranks like this:

### Q3 2026 (next 3 months) — close the showstopper gaps

★ = catalyst sprint, jumped to top of queue per the EC-trade lesson.

| Phase | Scope | Cost | Effort |
|---|---|---|---|
| **★ 17.1** | NewsAPI + GDELT ingest + sentiment scoring | $0–50/mo | 1 week |
| **★ 17.2** | Dated-catalyst extractor (elections, FOMC, OPEC, earnings) | $0 | 3 days |
| **★ 17.3** | "Catalyst" sub-row on every Compare row | included | 3 days |
| **★ 17.5** | Combined verdict on Symbol Deep Dive (technical + catalyst) | included | 3 days |
| 6.11 | Fix Finnhub upgrade-feed dropouts (Task #71) | $0 | 1d |
| 6.12 | Fundamentals core 8 + Piotroski + ratios (Phase 8.1-3) | $30/mo | 2 weeks |
| 7.2 | Polygon.io intraday wired into Task #69 engine | $30/mo | 1 week |
| 7.4 | L1 spread → pre-trade gate becomes real | included w/ 7.2 | 2 days |
| 8.5 | Forward-earnings + estimate revisions (Phase 8.5-7) | $50/mo upgrade | 1 week |
| 6.8 | Multi-source price consensus (Yahoo + EODHD + Stooq) | included w/ EODHD | 1 week |
| 8.9 | TipRanks per-analyst track records | $35/mo | 1 week |
| 9.1 | FRED macro context (rates, CPI, yields) | $0 | 3 days |
| 6.8b | Symbol-resolution layer (VGGS → VGGS.L) | $0 | 2 days |
| 15.1 | FinBERT side-by-side sentiment (§13) | $0 (local) | 1 week |
| 14.2-14.5 | Mode-aware UI defaults (Intraday vs Long-term) | $0 | 1 week |

**Q3 data-feed budget: ~$195/mo + €19.99/mo + €29.99/mo ≈ $250/mo.**
Pays for itself if it prevents one bad trade per year. The ★ catalyst
sprint alone (Phases 17.1-17.5) is **3 weeks of work at $0–50/mo**
and ships the single biggest user-trust improvement.

### Q4 2026 — defensive depth

- 8.4 — dividend sustainability + buyback tracking
- 9.4 — StockTwits sentiment
- 12.1 — SEC EDGAR 8-K ingest (free)
- 12.2 — Benzinga catalyst feed
- 10.1 — FINRA short volume
- 13.1 — T212 order-stream for true fills

### 2027 — institutional-grade depth

- L2 order book, dark pool prints
- Earnings-call transcript LLM digest
- ETF flows + 13F lag aggregation
- Buyside/sellside analyst split

---

## Out of scope (for now)

- Options chains + Greeks + IV surface — TradePro is equity-first.
  Defer until a strategy needs them.
- Cryptocurrency feeds — separate product surface.
- Fixed income — institutional product.
- FX — only as a portfolio aggregation problem, not a trading surface.

---

## Open data questions for the user

Each of these blocks the relevant phase landing:

1. **Q3 budget commit.** $200/mo is roughly the new spend to close
   the showstopper gaps. Confirm before we sign up to providers.
2. **Stocks vs ETFs priority.** Reviewer says fundamentals are the
   gap to Stock Rover. But you've been mostly ETF-trading. Should
   we prioritise stock fundamentals (Phase 8.1-3) or ETF-specific
   data (holdings overlap, expense-trend, factor exposure)?
3. **Real-time vs EOD.** Real-time intraday (Phase 7.2) is the
   prerequisite for trustworthy auto-place. But if you're happy
   with manual approval indefinitely, we can skip this entire
   branch and save $30/mo.
4. **UK-resident assumption.** Current data is US-heavy. Should
   Phase 8.1 (fundamentals) prioritise FTSE 100 / FTSE 250 first?

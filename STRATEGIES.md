# Strategies in TradePro

This is the canonical reference for every trading strategy the system
runs. Read it when:

- You're about to ship a new strategy and need to understand the
  three layers it might plug into.
- A consensus verdict on the Decide page surprises you and you want
  to know which strategy contributed what.
- You're deciding whether a strategy is right for a particular
  symbol (e.g. should RSI mean-reversion be voting on the iShares
  Momentum ETF? Spoiler: no).
- You want to evaluate which strategy is doing best on a given
  symbol — see the companion doc [EVALUATION.md](EVALUATION.md) for
  the metrics, this doc for the philosophy.

## The three layers (plus two analytical tracks)

TradePro has strategies in three distinct layers (Layer 1–3 below) and
two analytical tracks that consume them:

- **Track 1 — Trading signals** (Layers 1, 2, 3) — produces BUY / SELL
  / HOLD signals on bars. The historical heart of the platform.
- **Track 2 — Core Portfolio / Compounder** (Compounder-mode vocabulary,
  distinct from BUY/WAIT/AVOID) — fundamentals-driven view for the
  ~25% core sleeve. Seven modules under
  [`strategies/tradepro_strategies/core_portfolio/`](strategies/tradepro_strategies/core_portfolio/)
  — quality scorecard (★), valuation layer (ATTRACTIVE/FAIR/STRETCHED),
  dividend dashboard (STRONG/STEADY/UNDER_PRESSURE), allocation view
  (UNDERWEIGHT/ON_TARGET/OVERWEIGHT), entry timing (ACCUMULATE/WATCH/
  NEUTRAL), ETF X-Ray (overlap detector + DRIP), manual MF sleeve
  (UK/Indian/offshore NAV-entry tracker). All seven landed
  2026-05-25.
- **Lane A — Quant Engine** (parallel work,
  [`strategies/tradepro_strategies/quant_engine/`](strategies/tradepro_strategies/quant_engine/))
  — trader-provided systematic-trading framework: Ichimoku-based
  equity + FX strategies, vol targeting (Hurst-Ooi-Pedersen scalar),
  walk-forward validation, Monte Carlo stress, regime filter,
  ensemble combiner, portfolio metrics. **Complementary** to the
  Layer-1 signal strategies — these are quant-strategy signal
  generators with full out-of-sample validation, not portfolio
  management. Plugged into the paper engine via
  `paper/strategies/ichimoku_equity.py` + `ichimoku_fx_mr.py`.

The platform-level fusion lives in
[`core_portfolio/symbol_analysis_card.py`](strategies/tradepro_strategies/core_portfolio/symbol_analysis_card.py)
— `build_symbol_analysis_card(symbol, …)` returns one unified card
with the technical block, the fundamental block, the long-term A-F
grade, and a single `primary_horizon_recommendation` token
(LONG_TERM_HOLD / MEDIUM_TERM_ADD / SHORT_TERM_TRADE / AVOID / WATCH
/ INSUFFICIENT). Exposed as MCP tool `get_symbol_analysis`.

The rest of this doc covers Track 1's three signal layers in detail.

### Layer 1 — Daily signal strategies (`ISignalStrategy`, .NET)

These run on **end-of-day OHLC bars** and produce a `BUY`, `SELL`, or
`HOLD` for each historical bar. They're what the Compare/Decide pages
backtest, what the multi-strategy consensus on the Backtest page
votes from, and what the email digest rolls up. Every TradePro user
who hasn't installed the paper-engine sees only these.

Code lives in
[`backend/TradePro.Api/Simulation/StrategySignals.cs`](backend/TradePro.Api/Simulation/StrategySignals.cs).
They're registered as `ISignalStrategy` in DI; the runtime resolves
them by name via `IStrategyRegistry`.

There are seven of them today: buy-and-hold, SMA crossover, RSI mean
reversion, MACD signal cross, Donchian breakout, Ichimoku Cloud, and
Bollinger bounce.

### Layer 2 — Intraday paper-trading strategies (Python, `BasePaperStrategy`)

These run on **1-minute bars** in the paper-trading engine, place
real orders against Trading 212 (Practice or Live) or Interactive
Brokers, and live entirely on the Mac side. They never appear on
the Decide page — they're for the active paper-trader, not the
investor screening the ETF universe.

Code lives in
[`strategies/tradepro_strategies/paper/strategies/`](strategies/tradepro_strategies/paper/strategies/).
They register via the `@register_strategy` decorator and are
discovered by name when you run `tradepro-paper --strategy <name>`.

There are four of them today: opening-range breakout, VWAP mean
reversion, intraday Bollinger bounce, and EMA crossover.

### Layer 3 — Horizon scorers (Python composites)

These are not "strategies" in the buy/sell sense — they're 0-8
composites that drive the **horizon pills** on the Decide page
(Swing / Long-term / Passive). Each scorer reads a bag of facts
(Sharpe, valuation flag, range percentile, factor type…) and emits a
verdict + a key-factors string explaining how it got there.

Code lives in
[`strategies/tradepro_strategies/swing.py`](strategies/tradepro_strategies/swing.py)
(swing composite) and
[`strategies/tradepro_strategies/horizons.py`](strategies/tradepro_strategies/horizons.py)
(long-term + passive composites + the swing wrapper that adds the
range-percentile modifier).

The rest of this doc walks each layer in detail.

---

## Layer 1: Daily Signal Strategies

The seven strategies here are intentionally **diverse by family** so
that the consensus vote means something. If they were all variants of
"price above moving average" then a majority-long vote would be one
data point dressed up as seven. The current mix covers:

- **Trend-following**: SMA crossover, MACD, Donchian, Ichimoku
- **Mean-reversion**: RSI, Bollinger bounce
- **Benchmark**: buy-and-hold

What's missing today is anything from the **valuation**,
**factor-based**, or **event-driven** families — see Phase 3 in
[ROADMAP.md](ROADMAP.md) for that gap.

### Buy-and-Hold (`buy_and_hold`)

> **Rule**: Buy on the first bar of the historical window; never sell.
>
> **Use as**: the baseline every other strategy gets compared
> against. If a fancy strategy can't beat buy-and-hold's Sharpe on
> a given symbol, it's not earning its complexity.

This isn't really a "strategy" — it's the **null model**. Every
backtest reports the buy-and-hold equity curve alongside the
strategy's curve. If a strategy with a Sharpe of 0.8 underperforms
buy-and-hold's 1.1 on the same symbol, that strategy is *destroying*
value, not creating it. The Decide page already surfaces this: a
strategy that doesn't beat buy-and-hold gets a `bg`-style demotion
in the top-candidate ranking.

The implementation is two lines: signals\[0\] = BUY, everything else
HOLD. The framework does the rest.

### SMA Crossover (`sma_crossover`)

> **Rule**: BUY when the fast SMA (default 20-day) crosses above the
> slow SMA (default 50-day). SELL when it crosses below.
>
> **Suits**: persistent trends — large-cap equities and broad ETFs in
> a clear bull or bear regime.
>
> **Anti-fit**: range-bound or choppy markets, where you get
> repeatedly whipsawed on every micro-trend.

The most familiar trend-follower in the canon. It's slow, which is
both its strength (filters out noise) and its weakness (it enters
late and exits late). On a smooth trending symbol like SPY in 2017
or NVDA in 2023-2024 it captures most of the move; on a
range-bound name like XLU in 2019 it loses to a flat line.

Parameters: `fast` (default 20), `slow` (default 50). Common
variants: 50/200 ("the death/golden cross" beloved by financial
journalists), 10/30 (faster, more whipsaws).

In the multi-strategy consensus this is the **slow trend vote**. If
it says BUY, the symbol has been trending up for at least a few
weeks. If it goes from BUY to SELL, the trend is breaking down on a
longer timescale than MACD would catch.

### RSI Mean Reversion (`rsi_mean_reversion`)

> **Rule**: Track the 14-day Relative Strength Index. BUY when RSI
> re-enters the neutral zone after being below 30 (oversold). SELL
> when it re-enters neutral after being above 70 (overbought).
>
> **Suits**: range-bound symbols that oscillate around a fair value —
> mature large-caps, broad indices in low-vol regimes, mean-reverting
> ETFs (low-vol, dividend, equal-weight).
>
> **Anti-fit**: **momentum-factor ETFs** (MTUM is the canonical
> example), strongly-trending high-growth names, any symbol where
> "oversold" historically just means "early in a longer move down."

RSI MR is the canonical mean-reversion strategy and it works *when
the asset is supposed to mean-revert*. The signal fires on the bar
RSI re-enters the 30-70 zone, not on the bar it crosses 30 — that
filters out the "walking the band down" trap where RSI stays below
30 for weeks while price keeps falling.

**The instrument-fit caveat is load-bearing.** Applying RSI MR to a
momentum-factor ETF like MTUM produces SELL signals almost
continuously — RSI being elevated is what a momentum factor *does
by design*. The strategy and the instrument are philosophically
incompatible, and the SELL it produces is statistically valid for
the strategy's own rules but tactically useless for someone holding
MTUM. This is the worked example for why future versions of the
consensus engine should down-weight or exclude structurally
mismatched strategies.

Parameters: `period` (default 14), `low` (oversold threshold,
default 30), `high` (overbought threshold, default 70).

### MACD Signal Cross (`macd_signal_cross`)

> **Rule**: Compute the MACD line (12-EMA − 26-EMA) and its 9-EMA
> signal line. BUY when MACD crosses above signal. SELL when it
> crosses below.
>
> **Suits**: medium-timescale trends — faster than SMA crossover,
> still smoothed enough to filter daily noise.
>
> **Anti-fit**: violently choppy markets (whipsaws), or extremely
> low-volatility periods where the EMAs converge and every micro-
> move triggers a crossover.

MACD sits between SMA crossover (slow) and Donchian breakout (fast).
It's the **medium-trend vote** in the consensus. The 12/26/9
defaults are inherited from Gerald Appel's 1979 specification and
are not particularly sacred — 8/17/9 is faster and more reactive
without losing too much signal quality.

The fact that MACD is computed from *exponential* moving averages,
not simple ones, is what makes it react sooner than SMA crossover
to a real trend change. The 9-EMA signal line is a smoothing layer
on top of MACD itself — without it, MACD would cross its zero line
too often and produce too many signals.

Parameters: `fast` (default 12), `slow` (default 26), `signal`
(default 9).

### Donchian Breakout (`donchian_breakout`)

> **Rule**: BUY when today's close exceeds the highest close of the
> prior 20 bars. SELL when it drops below the prior 20-bar low.
>
> **Suits**: momentum-driven breakouts — when a symbol clears
> resistance after weeks of consolidation. Catches the start of
> strong moves before MACD or SMA crossover would notice.
>
> **Anti-fit**: range-bound markets where the price oscillates within
> the 20-bar envelope without ever breaking out. Donchian sits
> dormant for months in choppy regimes.

Made famous by the **Turtle Traders** experiment in the 1980s — a
20-day breakout was the entry rule that turned a group of
non-traders into a small army of profitable trend-followers. The
strategy has aged remarkably well because it's parameter-light (one
number) and indifferent to volatility regime (it doesn't smooth, so
it can't lag).

The trade-off is **drawdown discipline**. A Donchian breakout strategy
without a position-sizing rule will get massacred in a chop year
because every false breakout becomes a full position. In TradePro
the strategy itself emits the signal; risk management (position
sizing, stop placement) is handled separately by the paper engine's
`RiskLimits`. For the Decide page's backtest the assumption is
fixed-fraction sizing.

Parameters: `lookback` (default 20).

In the consensus this is the **breakout vote** — independent of the
moving-average family. A Donchian BUY paired with an SMA-crossover
HOLD says "we just cleared resistance but the longer trend hasn't
re-confirmed yet" — a fragile, early signal worth treating with
caution.

### Ichimoku Cloud (`ichimoku_cloud`)

> **Rule**: BUY when close crosses above the cloud (max of senkou A
> and senkou B) **and** Chikou confirms (close above the close 26
> bars ago). SELL when close drops below the Kijun line.
>
> **Suits**: clean, well-defined trends that hold for weeks or
> months. The cloud's *thickness* is itself information — thick
> cloud = strong trend, thin cloud = transitional regime.
>
> **Anti-fit**: short-timescale price action, very small-cap symbols
> with sparse data, or anything where 52 bars (~10 weeks) of history
> is meaningfully different from the current regime.

The most ornate strategy in the lineup. Five lines (Tenkan, Kijun,
Senkou A, Senkou B, Chikou) interact to produce the entry/exit
signals. The Python and .NET implementations were aligned bar-for-
bar so the website's "backtest math == comparator math" — see the
docstring on `IchimokuCloudStrategy`.

What Ichimoku adds that the simpler strategies don't:

- **A confirmation layer** (Chikou) that filters out fake breakouts
  where current price is above the cloud but momentum 26 bars ago
  wasn't supporting it.
- **Visual decision-trace data**. The Decide page surfaces
  `ichimoku_targets` — price target, stop level, R/R ratio — only
  for symbols where Ichimoku is the active strategy, because the
  cloud structure provides a natural target and stop.

Parameters: `tenkan` (9), `kijun` (26), `senkou_b` (52),
`displacement` (26). The numbers come from the original 1960s
Japanese formulation and are essentially defaults nobody changes.

### Bollinger Bounce (`bollinger_bounce`)

> **Rule**: BUY when close is below the lower Bollinger band **and**
> RSI < 35 (dual trigger). SELL when close returns to the middle band
> or above the upper band.
>
> **Suits**: stable-volatility, range-bound symbols. Pairs well with
> RSI mean reversion as the second vote in the mean-reversion family.
>
> **Anti-fit**: trending symbols (rides the lower band down forever
> without a true bounce), squeeze-then-expansion regimes (false
> entries right before the move accelerates the wrong way).

The dual trigger is the key design choice. Plain "buy at the lower
band" gets you murdered on trending days because price *walks down*
the band — you enter every bar and lose every bar. Adding the RSI
filter requires the move to *also* be statistically oversold, which
filters out the walking-down case (RSI stays in the 40s during a
band-walk, so the dual trigger never fires).

The exit is liberal — middle band or above upper band, whichever
comes first — so winning trades are typically 4-8 bars and produce
modest gains. The strategy is a "small profits, fewer losses"
profile, the opposite of Donchian's "big rare wins."

Parameters: `window` (20), `num_std` (2.0), `rsi_period` (14),
`rsi_oversold` (35).

---

## Layer 2: Intraday Paper-Trading Strategies

These run on 1-minute bars inside the `tradepro-paper` engine. They
produce real (paper) orders against Trading 212 Practice or Live, or
Interactive Brokers paper. They share zero code with the Layer 1
strategies because they operate on different timescales, different
session boundaries, and different lifecycle assumptions (intraday
strategies must **flatten by session close**; daily strategies hold
overnight).

All four are **deliberately complementary by regime**:

- ORB and EMA crossover win on trending days.
- VWAP-MR and Bollinger bounce win on choppy days.

Running all four in a multi-broker shadow mode gives you a portfolio
that's regime-agnostic in expectation. The actual P&L attribution
will swing depending on what kind of day it was — see the per-
strategy fill log on the Paper page Live tab.

### Opening Range Breakout (`orb`)

> **Rule**: Watch the first 15 minutes of the regular session, record
> the high/low. After the window: BUY when close breaks above the
> range high, SHORT when it breaks below (if `allow_short`). Stop at
> 1× range height; target at 2× range height. Flatten at session
> close.
>
> **Suits**: liquid US large-caps that have a real opening-auction
> price discovery process (SPY, QQQ, AAPL, NVDA). Gap-and-go days,
> news-driven runs.
>
> **Anti-fit**: low-volume opens, mean-reverting days, post-holiday
> sessions where the first 15 minutes have no information content.

ORB is the textbook intraday strategy. The thesis: the first 15
minutes of trading set the day's "range of disagreement" between
buyers and sellers; once one side breaks that range, the move tends
to continue for hours rather than minutes. The 2R reward / 1R risk
profile is mechanical — no judgment calls — which is why it's the
strategy most often used to teach systematic intraday trading.

Real-world caveats:
- Earnings days break the strategy (the gap *is* the move).
- Mid-week chop days produce a false breakout in the first hour, a
  false breakdown in the second, and you get stopped both ways.
- The 19:50 UTC flatten time matters: that's 5 minutes before the
  16:00 ET close, giving the order one full minute-bar to fill.

Parameters: `range_minutes` (15), `risk_per_trade_usd` (100),
`stop_multiple` (1.0), `target_multiple` (2.0), `direction` ("long"
default; "both" enables the short side), `session_close_local`
("19:50" UTC).

### VWAP Mean Reversion (`vwap_mean_reversion`)

> **Rule**: Track session VWAP (cumulative volume-weighted average
> price). LONG when close < VWAP × (1 − 0.5%). SHORT when close >
> VWAP × (1 + 0.5%). Exit at VWAP touch (target = revert to mean) or
> stop placed `stop_pct` further beyond entry.
>
> **Suits**: choppy intraday days in liquid large-caps where the bulk
> of volume is institutional (real liquidity providers happy to
> defend VWAP).
>
> **Anti-fit**: trending days (VWAP drifts steadily, "stretched 0.5%"
> never reverts), low-volume open (VWAP unstable for the first 30
> minutes), thin small-caps (VWAP has no defenders).

VWAP-MR is the mirror image of ORB. ORB pays you when the day
*runs*; VWAP-MR pays you when the day *chops*. Their equity curves
should be anti-correlated day-by-day — that's the whole reason to
run both in shadow mode.

The 0.5% default trigger is conservative. Aggressive intraday
traders use 0.25-0.3%; that gets you more trades but a worse hit
rate as you start firing on routine noise. The right number is
symbol-dependent: AAPL trades inside 0.3% of VWAP for most of the
day, while NVDA can swing 1% intraday without it being a real
divergence.

Parameters: `vwap_dev_pct` (0.005), `stop_pct` (0.01),
`session_close_local` ("19:50" UTC), `direction` ("long" default).

### Bollinger Bounce intraday (`bollinger_bounce`)

> **Rule**: Maintain a rolling window of `window` closes. LONG when
> close < (mean − 2σ) AND RSI < oversold. Exit at the mean.
>
> **Suits**: range days with stable volatility. Same family as
> VWAP-MR but the anchor is volatility-adaptive (bands widen when
> the day gets volatile) rather than a single weighted average.
>
> **Anti-fit**: squeeze-then-expansion days (bands compress, then
> the move starts and you're long at the lower band right before
> the slide).

This is the intraday cousin of the Layer 1 Bollinger bounce. The
math is the same, the timescale is different (20 bars on 1-min data
is 20 minutes, not 20 days). The intraday version is less prone to
the "walking the band" pitfall because the bands recompute every
bar and adapt to volatility regime changes within the session.

Why include this alongside VWAP-MR even though both are mean-
reverters: they use different anchors. On a day where price drifts
steadily away from VWAP (gap-and-fade), VWAP-MR shorts the rip
aggressively while Bollinger may not trigger because the drift fits
inside expanding bands. Different trigger → different P&L → real
diversification within the mean-reversion family.

Parameters: `window` (20), `num_std` (2.0), `rsi_period` (14),
`rsi_oversold` (35), `session_close_local` ("19:50" UTC).

### EMA Crossover intraday (`ma_crossover`)

> **Rule**: Maintain a fast EMA (default 5 bars) and slow EMA
> (default 20). LONG when fast crosses above slow. Exit on the
> opposite crossover. Both EMAs reset at session start.
>
> **Suits**: persistent intraday trends. The trend-following
> counterpart to VWAP-MR and Bollinger bounce.
>
> **Anti-fit**: choppy days — classic death-by-a-thousand-whipsaws.
> The first 5-10 bars of any session also produce noisy signals
> before either EMA stabilises.

The point of this strategy is **trend exposure** in the multi-
strategy stack. On a strong trending day VWAP-MR and Bollinger
bounce both bleed (false reversion trades) while ORB has one big
win (the breakout) and then sits flat. EMA crossover fills the gap
by trading the trend's *continuations* — every meaningful pullback-
and-recovery generates an entry, and the strategy stays long
through extended runs.

The fast/slow defaults (5 / 20 minute EMAs) are calibrated for
liquid US equities. Smaller numbers (3 / 10) give a more reactive
strategy at the cost of more whipsaws; larger (10 / 30) gives a
slower but cleaner profile.

Parameters: `fast_window` (5), `slow_window` (20),
`session_close_local` ("19:50" UTC), `direction` ("long" default).

### Intraday EOD-Flat with daily-Ichimoku basket (`intraday_flat`)

> **Rule**: At session start, score the candidate universe on the prior
> day's Ichimoku setup, lock the top-N (default 5) as today's basket.
> Through the day, each basket name gets at most one long entry, sized
> so the ATR-based stop costs a fixed dollar amount. Exit on stop,
> target, time-stop, or EOD — whichever fires first. **Must be flat by
> close.** No overnight risk.
>
> **Suits**: trader who wants a transparent, risk-bounded intraday
> book where every trade has an explainable thesis at the basket level
> ("today's top Ichimoku longs") and at the per-name level (sized from
> ATR, stop-anchored, news-vetoed by the LLM gate).
>
> **Anti-fit**: choppy single-day reversals (basket was set on a
> bullish daily view; if the day reverses, every position stops out
> together). Earnings-heavy weeks (LLM gate dampens but doesn't avoid).

`intraday_flat` is the **risk-aversion-first** intraday strategy in
TradePro. ORB and VWAP-MR are textbook intraday entries with a fixed
symbol; `intraday_flat` adds three things ORB does not have:

1. **A scanner.** ORB needs you to pick the symbol. `intraday_flat`
   picks 5 per day from a candidate list using `ichimoku_strength_score`
   (price-vs-kijun distance × cloud-thickness, both ATR-normalised).
2. **A regime gate.** If SPY closes below its 200-SMA the scanner
   refuses to build a basket at all. No "force a trade because today
   is a trading day."
3. **An audit trail every gate writes to.** Every skip — outside the
   entry window, halted, vetoed by the LLM, sized to zero, missing IG
   epic — produces a structured `_decisions` entry the cockpit can
   render so a trader knows exactly why nothing happened.

**Flow through one bar:**

```
on_bar(bar):
  1. paused?            → skip-paused
  2. off-basket?        → silent skip (no noise in trace)
  3. EOD window?        → flatten ALL positions (never LLM-gated)
  4. holding position?  → check stop / target / time-stop
  5. entry window?      → skip-outside-entry-window
  6. one-per-day?       → skip-one-per-day
  7. in-flight?         → skip-in-flight  (no emit-twice race)
  8. halted?            → skip-halted
  9. max positions?     → skip-max-positions
  10. epic lookup?      → skip-no-epic    (refuses to route unmapped)
  11. LLM gate          → skip-llm-vetoed  (with sentiment + reason)
  12. sizing            → skip-zero-qty if math rounds to 0
  13. fire-buy          → tagged with strength, regime, ATR, stop,
                          target, R:R, LLM verdict
```

**Three EOD safeguards:**

1. **Flatten window** — from `flatten_start_utc` onwards every bar
   tries to flatten every open position.
2. **`on_session_end` backstop** — logs an `alert-eod-leftovers`
   entry naming any positions still open. This is an audit alert,
   not a flatten that can fire (the bar bus is closing).
3. **Out-of-band reconciliation** — separate operator step: query
   `IGClient.GetPositionsAsync` after the session and flatten any
   leftovers manually. The strategy reports the alert; the operator
   acts on it.

**Risk envelope (defaults, all conservative):**

| Cap | Default | Why |
|---|---|---|
| risk_per_trade_usd | $100 | One trade losing the stop costs ~$100; with 5 names that's $500 max if everything stops at once |
| stop_atr_mult | 1.5× ATR | Stops outside typical intraday noise but tight enough to size meaningful share count |
| target_atr_mult | 2.5× ATR | ~1.67 R:R; achievable on liquid ETFs in trending sessions |
| max_hold_minutes | 240 | 4 hours; if the thesis hasn't worked by then it likely won't today |
| top_n | 5 | Diversifies single-name idiosyncrasy without exceeding RiskService default `max_open_positions` |
| LLM gate | enabled, fail-open | Veto on materially negative news only; fail-open so an LLM outage doesn't block trading. For LIVE, set `fail_open=False` |

**Reading the order tag (entry):**

```
intraday_flat ENTRY IWM strength=3.05 regime=BULL atr=1.234
              qty=53 stop~198.50 target~203.10 R:R~1.67 llm=APPROVED@+0.34
```

Every field is the gate's input: `strength` is the scanner score,
`regime` is the SPY/200-SMA verdict, `atr` is the sizing denominator,
`stop`/`target` are the exit anchors, `llm` is the gate verdict and
sentiment. A trader can read this line and know *exactly* why the
order was emitted at this size with this stop.

**Reading the order tag (exit):**

```
intraday_flat STOP IWM held=125min entry=200.05 stop=198.50 bar.low=198.30
intraday_flat TARGET IWM held=92min entry=200.05 target=203.10 bar.high=203.55
intraday_flat TIME IWM held=240.1min >= max_hold 240min entry=200.05 close=200.80
intraday_flat EOD IWM flatten window opened at 19:50 (triggered by SPY bar)
```

**Phase-0 dependency** — this strategy routes orders to IG demo by
stamping `Order.broker_label="IG_DEMO"` and `Order.instrument_id=<epic>`.
The epic comes from `ig_epic_map.json` next to `ig_epic_map.py`. To
enable the strategy for a new symbol:
1. Discover the epic via `GET /api/admin/ig/search?term=<symbol>`.
2. Edit `ig_epic_map.json`, populate `epic`.
3. Smoke-test via `POST /api/admin/ig/smoke-order { epic, side, size: 1 }`.
4. Add the symbol to the strategy's `candidates` param.

The scanner refuses any symbol whose epic is null or missing. There
is no "best-effort route by ticker" fallback — the cost of a wrong
listing dwarfs the cost of an obvious refusal.

**Caveats** (also surfaced in the UI banner):

- Basket is **locked at session_start** — no intraday re-ranking, so
  a name that becomes bullish at 11am is NOT added to today's book.
- One entry per name per day. No averaging in.
- No partial exits. Stop, target, time, or EOD — whole position out.
- No cross-strategy gross exposure cap (framework gap).
- EOD flatten depends on bars arriving in the flatten window; the
  on_session_end backstop is best-effort. Reconciliation is the real
  guarantee of "flat by close".
- LLM gate is fail-open by framework default. For live, switch the
  per-strategy LLMGateConfig `fail_open` to False.
- Daily signal + ATR come from the on-disk cache (yfinance default).
  Stale cache = stale basket.

Parameters: `candidates` (5 ETFs by default), `top_n` (5),
`use_regime_filter` (True), `regime_symbol` ("SPY"),
`regime_sma_period` (200), `risk_per_trade_usd` (100),
`stop_atr_mult` (1.5), `target_atr_mult` (2.5),
`max_hold_minutes` (240), `entry_window_start_utc` ("13:35"),
`entry_window_end_utc` ("18:00"), `flatten_start_utc` ("19:50"),
`session_close_utc` ("20:00"), `broker_label` ("IG_DEMO"),
`ig_epic_map_path` (None → default beside `ig_epic_map.py`).

---

## Strategy → broker mapping

Each strategy has a default broker it targets. The mapping lives in
the Postgres table `strategy_broker_map`, populated by SQL migrations
under `backend/TradePro.Api/db/migrations/`. The `.NET ApproveAsync`
path consults this table to pick which downstream broker client
(T212, IG, IBKR, paper) handles each approved order.

**Resolution priority** (highest wins) — see
`backend/.../Endpoints/TradePlanEndpoints.cs`:

1. **Per-call override** — an explicit `broker` field in the trade-plan
   request body (e.g. operator UI sends `broker: "T212_DEMO"` for a
   one-off).
2. **`strategy_broker_map.broker`** — the per-strategy default below.
3. **`app_settings_kv.default_broker`** — global fallback when a
   strategy isn't mapped (lets new strategies land without a migration).
4. **Hardcoded** — `T212_DEMO` if even the global fallback isn't set.

### Current mapping (seeded by migrations 021 and 024)

| Strategy | Broker | Note | Seeded by |
|---|---|---|---|
| `ichimoku_equity` | `IG_DEMO` | US equity sleeve via IG demo | 021 |
| `ichimoku_fx_mr` | `IG_DEMO` | G10 FX intraday via IG demo | 021 |
| `intraday_flat` | `IG_DEMO` | US ETF intraday EOD-flat via IG demo | 024 |
| any other registered strategy | `app_settings_kv.default_broker` | falls back to global default | — |

### How to read it

```sql
SELECT strategy_id, broker, account_id, note, updated_at_utc, updated_by
FROM strategy_broker_map
ORDER BY strategy_id;
```

### How to override per-strategy

Don't back-edit migration 021 — add a new migration `0NN_<change>.sql`
with the targeted `UPDATE`. Per-call overrides via the trade-plan
request body work for one-off operator actions and don't need a
migration.

Inside a Python strategy, `params["broker_label"]` is consulted at
order-emission time when the strategy itself stamps a target broker
(e.g. `intraday_flat` defaults to `"IG_DEMO"`). The DB-level mapping
still wins at OMS dispatch — the in-strategy field is just an explicit
declaration of intent in the order tag for audit purposes.

### Adding a new strategy

When you register a new `@register_strategy(...)`, also add a small
migration to `strategy_broker_map`:

```sql
-- 025_strategy_broker_map_my_new_strategy.sql
INSERT INTO strategy_broker_map (strategy_id, broker, note, updated_by)
VALUES ('my_new_strategy', 'T212_DEMO', 'description', 'migration')
ON CONFLICT (strategy_id) DO NOTHING;
```

If you skip this step, the strategy will still run but will route via
the global `default_broker` — fine for development, not what you want
in production.

---

## Layer 3: Horizon Scorers

These are 0-8 composites that drive the **horizon pills** on the
Decide page. Unlike the buy/sell strategies above, they don't fire
on bars — they read a snapshot of facts about the symbol and emit a
score + verdict + reason. There are three:

### Swing scorer (`evaluate_swing`, range 0-8)

Returns a swing verdict (STRONG_BUY / BUY / WATCH / AVOID) and a
score 0-8 derived from four sub-scores:

- **Q (quality, 0-2)**: Sharpe + recovery profile. High Sharpe with
  fast drawdown recovery = 2; modest Sharpe with slow recovery = 1;
  poor Sharpe = 0.
- **V (valuation, 0-2)**: PE-band flag. CHEAP = 2, FAIR = 1,
  EXPENSIVE = 0.
- **E (event, 0-2)**: earnings catalyst proximity. Within 5
  trading days and historically up = 2; within 15 days = 1;
  no catalyst = 0.
- **P (price, 0-2)**: consensus upside. Analyst price target > 25%
  upside = 2; 10-25% = 1; under 10% = 0.

Total Q + V + E + P gives the swing score. A score of 7-8 is
STRONG_BUY, 5-6 is BUY, 3-4 is WATCH, 0-2 is AVOID. The Decide
page displays this as e.g. "Swing 6/8 BUY [Q2·V2·E1·P1]" so you
can see exactly which factors contributed.

The full implementation is in
[`strategies/tradepro_strategies/swing.py`](strategies/tradepro_strategies/swing.py).

### Long-term scorer (`_score_long_term`, range 0-8)

Suited to a 6-18 month horizon. Reads:

- **Sharpe > 0.7** → 2 points
- **PE flag CHEAP** → 2, **FAIR** → 1
- **Analyst upside > 25%** → 2 points
- **CAGR > 10%** → 1 point
- **(historical drawdown well-recovered)** → up to 1 more point

Verdict mapping: 6-8 = BUY, 3-5 = WATCH, 0-2 = AVOID.

The long-term scorer is **more forgiving on entry timing** than the
swing scorer — it doesn't apply the range-percentile penalty because
the underlying thesis is "DCA in over months." A symbol at the 90th
percentile of its 52w range is still BUY-able for long-term if the
fundamentals support it.

### Passive scorer (`_score_passive`, range 0-8 or N/A)

Suited to a 3-5 year DCA horizon, specifically for diversified ETFs.
Reads:

- **Expense ratio < 0.1%** → 2 points
- **Holdings > 200** → 2 points
- **Sharpe > 0.6** → 2 points
- **CAGR > 7%** → 1 point
- **(structural fit)** → up to 1 more point

**Returns "N/A" for single-stock symbols.** A passive DCA score on
AAPL is meaningless — that's a single-name concentration bet, not
the kind of position the passive horizon was designed for. The
Decide page hides the passive pill when the score is N/A.

### The range-percentile modifier (swing only)

The swing scorer in `horizons.py:_score_swing` adds a final
adjustment based on **where the current price sits in the 52-week
range**:

- **0-30th percentile** (near 52w low) → +1 score modifier
- **30-65th percentile** (mid-range) → 0
- **65-80th percentile** (near highs) → -1 modifier
- **above 80th percentile** (very near 52w high) → capped at WATCH

This is the rule that turns AVGO's `swing 6/8 BUY` into `swing 4/8
WATCH` at the 93rd percentile of its 52w range — the rule fires
specifically because the asymmetric risk/reward of buying at the
highs is unfavourable for a 1-8 week swing trade. The long-term and
passive scorers do **not** apply this modifier because their
holding horizon makes the entry-price-percentile effectively
irrelevant.

---

## How Layer 1 strategies combine into a consensus

The Backtest page and the Decide page both surface a "multi-
strategy consensus" — the same data viewed two different ways.

### What "currently long" means

For each (symbol, strategy) pair, the comparator looks at the most
recent non-zero signal. If it was a BUY and no SELL has fired since,
the strategy is **currently long** — it has a position. If it was a
SELL and no BUY has fired since, the strategy is **currently flat**.

A strategy that just fired BUY today is currently long.
A strategy that just fired SELL today is currently flat.
A strategy with a HOLD today can be either, depending on its prior
state.

This is why the Backtest page splits HOLD into:

- **HOLD-IN**: strategy is currently long, no action today.
  (Equivalent to "stay in the trade.")
- **HOLD-OUT**: strategy is currently flat, no action today.
  (Equivalent to "stay flat, waiting for a setup.")

The math reconciles in plain view: `currently_long = BUY + HOLD-IN`.

### How the consensus bucket is decided

The consensus engine in
[`strategies/tradepro_strategies/compare.py`](strategies/tradepro_strategies/compare.py)
runs roughly this logic:

1. For each strategy, classify today's action (BUY / SELL / HOLD)
   and position state (in / out).
2. Compute the price-side verdict from market state alone (above
   200-SMA? near 52w high? RSI extremes?). This produces BUY,
   WAIT, or AVOID independent of the strategy votes.
3. Compute the long-count as `BUY + HOLD-IN` across all
   strategies.
4. Combine price-verdict + long-count:
   - Price says BUY + majority long → bucket BUY.
   - Price says BUY + minority long → bucket WAIT ("price-action
     gate passes but only N of 7 strategies are long — wait for
     broader confirmation").
   - Price says WAIT or AVOID → carry through regardless of how
     many strategies are still long. **The earlier rule that
     promoted HOLD to BUY on majority-long produced the
     MTUM/VLUE/QUAL contradictions** (bucket BUY while the same
     row's entry signal said "near 52w high, asymmetric risk").
5. Apply sentiment demotion (7-day mean sentiment ≤ -0.30 with 2+
   material-negative headlines demotes BUY to WAIT; ≤ -0.45 with
   3+ negatives demotes anything to AVOID).
6. Apply horizon and range-percentile demotion (swing AVOID
   veto, 85th-pctile cap).

The final bucket goes into the email digest and the Decide page
verdict.

### The instrument-strategy fit problem (Phase 6.5 — shipped)

The consensus engine now filters strategies by instrument type.
Each symbol carries a `factor_type` classification (momentum, value,
quality, low_vol, broad_equity, bond, commodity, crypto,
single_stock, ...) and the engine excludes structurally-incompatible
strategies from the consensus count.

Concrete rules in
[`strategies/tradepro_strategies/factor_types.py`](strategies/tradepro_strategies/factor_types.py):

- **momentum** (MTUM): RSI mean-reversion and Bollinger bounce are
  excluded — elevated RSI / above upper band is the asset doing
  exactly what a momentum factor is designed to do.
- **low_vol** (USMV, XLU): Donchian and Ichimoku are excluded — these
  breakouts need volatility to fire meaningfully and tend to
  false-start on min-vol constructions.
- **bond** (AGG, TLT, IGLT): Donchian and RSI-MR are excluded — bond
  prices are tightly bounded by duration/coupon math, so breakouts
  fire rarely and the RSI-MR signal operates on a different
  timescale than yield moves.
- **crypto** (BTC-USD etc.): RSI-MR and Bollinger bounce are
  excluded — extreme volatility makes "oversold" readings persist
  for weeks without the reversion the strategies require.
- **broad_equity, single_stock, growth, quality, size, value,
  commodity, country, broad_sector**: no exclusions (every strategy
  votes).

Excluded rows are still shown in the leaderboard with their Sharpe
intact (it's valid backtest history), but they're greyed out and
flagged "excluded — X mismatch". The consensus header reads
"N of M strategies currently long (X excluded for fit)" so the
denominator reflects only the strategies that should vote on this
instrument.

MCP exposes the classification via `get_instrument_fit(symbol)` —
call this before recommending or rejecting a strategy on a specific
symbol. The leaderboard tool `get_strategy_leaderboard` carries
`excluded_for_fit` on each row.

---

## How to evaluate strategies

Five lenses, listed roughly in order of how much they tell you:

1. **Backtest performance vs buy-and-hold** — the same window, the
   same symbol, Sharpe + CAGR + max drawdown. If the strategy
   doesn't beat the null model, it's not earning its complexity.

2. **Performance by regime** — every backtest report includes
   regime stats (bull / bear / chop / high-vol). A strategy that
   has Sharpe 1.2 overall but Sharpe -0.4 in chop is a fair-
   weather strategy and its consensus weight should reflect that.

3. **Hit rate on fresh signals** — the `IHitRateEngine` already
   computes "when this strategy says BUY, what % of next-20-bar
   windows close higher?" Real numbers on a real universe of
   symbols. A hit rate under 50% on BUY signals is a "this
   strategy is anti-edge here" red flag.

4. **Independence in the consensus** — when a strategy disagrees
   with the consensus, is it usually right or wrong? A strategy
   whose dissent is reliably profitable is a *valuable* member of
   the stack even if its overall edge is modest. A strategy whose
   dissent is noise should be down-weighted.

5. **Live-vs-backtest drift** — paper-trading P&L vs the backtest's
   predicted Sharpe. If a strategy's live edge collapses below
   half its backtest Sharpe, it's been overfit and should be
   demoted.

Each of these is partially instrumented today and fully fleshed out
in [EVALUATION.md](EVALUATION.md).

---

## Adding a new strategy

### Layer 1 (daily signal, .NET)

1. Add a new class implementing `ISignalStrategy` in
   `backend/TradePro.Api/Simulation/StrategySignals.cs`. The
   `Generate` method takes the candle series and parameters and
   returns a `Signal[]` aligned to the input.
2. Register it in `Program.cs` with
   `builder.Services.AddScoped<ISignalStrategy, YourStrategy>();`.
3. The registry picks it up automatically.
4. Add an integration test in
   `backend/TradePro.Api.Tests/SimulationTests.cs` that pins the
   signal output on a fixture candle series.
5. Update this doc with rule, suits, anti-fit, parameters.

### Layer 2 (paper-trading, Python)

1. Create a new module under
   `strategies/tradepro_strategies/paper/strategies/your_strategy.py`.
2. Subclass `BasePaperStrategy` and decorate the class with
   `@register_strategy("your_strategy")`.
3. Implement `on_bar(self, symbol, bar)` and any of the lifecycle
   hooks (`on_session_start`, `on_session_end`). Use
   `self.has_order_in_flight(symbol)` and `self.qty_from_risk` to
   stay safe against the bar-vs-fill race condition.
4. Add to `paper/strategies/__init__.py` so the registry sees it.
5. Add a behaviour test under `strategies/tests/paper/strategies/`.
6. Run `tradepro-paper-strategies-push` to seed the API's
   strategy catalog so the UI lists it.
7. Update this doc.

### Layer 3 (horizon scorer)

Don't add a new horizon. The three (swing / long / passive) are a
deliberately fixed set so the Decide page can render three pills
with confidence. If you want a new evaluation lens, add it as a
**sub-score** to one of the existing scorers — e.g. a "momentum"
contribution to the long-term score — rather than a fourth horizon.

---

## See also

- [PAPER_TRADING.md](strategies/PAPER_TRADING.md) — operational guide
  for the paper-trading engine
- [CONCEPTS.md](strategies/CONCEPTS.md) — glossary of all TradePro
  concepts (BUY/SELL/HOLD, horizons, sentiment demotion, regime
  stats, …)
- [EVALUATION.md](EVALUATION.md) — quantitative framework for
  evaluating strategies
- [ROADMAP.md](ROADMAP.md) — including Phase 3 (multi-family
  signals + factor-aware consensus)

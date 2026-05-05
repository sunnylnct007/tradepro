/**
 * Central dictionary of plain-English definitions used by Info tooltips.
 * The `href` points to an anchor inside /help (rendered from ARCHITECTURE.md)
 * so a user who wants more context can follow through.
 *
 * Keep these short — two or three sentences max. Longer explanations live
 * in the architecture doc.
 */

export interface HelpEntry {
  title: string;
  body: string;
  href?: string; // anchor on /help
}

export const HELP: Record<string, HelpEntry> = {
  watchlist: {
    title: "Watchlist",
    body:
      "A defined list of stocks to analyse. 'uk' = FTSE 100/250 plus a few UK large-caps. You'll be able to create your own lists in a later phase.",
    href: "#7-strategies-we-have-today",
  },
  strategy: {
    title: "Trading strategy",
    body:
      "The rule set that decides BUY / SELL. SMA crossover catches trend changes; buy & hold is the benchmark every other strategy must beat net of fees.",
    href: "#6-indicators-explained-plain-english",
  },
  provider: {
    title: "Data provider",
    body:
      "Today everything runs through Yahoo Finance — covers UK + US stocks, indices, and crypto, no API key required. Stooq and Binance are coded but disabled (Stooq now needs a paid key; Binance is crypto-only).",
  },
  rsi_strategy: {
    title: "RSI mean-reversion",
    body:
      "Buys when RSI(14) recovers from oversold (<30) and sells when it cools off from overbought (>70). Fires more often than SMA crossover and tends to do well in sideways markets.",
    href: "#rsi-relative-strength-index",
  },
  macd_fast: {
    title: "MACD fast EMA",
    body:
      "Span of the short-term exponential moving average. Classic value: 12 days. Smaller = more reactive, more whipsaw.",
  },
  macd_slow: {
    title: "MACD slow EMA",
    body:
      "Span of the long-term exponential moving average. Classic value: 26 days. The MACD line itself is fast EMA − slow EMA.",
  },
  macd_signal: {
    title: "MACD signal smoothing",
    body:
      "Span of the EMA applied to the MACD line to produce the 'signal' line. Buy fires when MACD crosses above signal; sell on the reverse. Classic value: 9 days.",
  },
  donchian_lookback: {
    title: "Donchian lookback window",
    body:
      "How many prior bars define the breakout level. With 20, a buy fires when today closes above the highest close of the previous 20 days. Bigger = fewer, stronger signals; smaller = more noise.",
  },
  fast_sma: {
    title: "Fast SMA window",
    body:
      "Number of recent days averaged to form the short-term moving average. Smaller = reacts faster but noisier. Classic value: 20.",
    href: "#simple-moving-average-sma",
  },
  slow_sma: {
    title: "Slow SMA window",
    body:
      "Number of days for the long-term average. Larger = smoother, lags more. Classic pairing: 50 or 200 with a faster line crossing it.",
    href: "#simple-moving-average-sma",
  },
  stamp_duty: {
    title: "UK stamp duty",
    body:
      "0.5% tax on BUYS of most LSE main-market shares. AIM shares and ETFs are exempt — set this to 0 for those. Applied on every entry to make backtest results honest.",
    href: "#8-uk-fee-model-the-default",
  },
  commission: {
    title: "Commission per trade",
    body:
      "Flat broker fee in your account currency. Trading212 and Freetrade are £0; Hargreaves Lansdown ≈ £11.95; Interactive Brokers depends on volume.",
  },
  initial_capital: {
    title: "Starting capital",
    body:
      "How much money the simulation begins with. Fees and position sizing scale off this. Small accounts are more hurt by flat commissions.",
  },
  confidence: {
    title: "Signal score (0–95) — not a probability",
    body:
      "A heuristic agreement score: 65 if the strategy fired in the last 3 bars (40 otherwise), ±10 when SMA-trend or RSI confirm/contradict the action. It is NOT a probability of profit and NOT a historical hit rate — for that, see the Hit-rate card on the Signal detail page.",
  },
  cagr: {
    title: "CAGR — compound annual growth rate",
    body:
      "Your total return expressed as an annual %. A 10-year backtest that turned £10k into £20k has a CAGR of ~7.2%.",
    href: "#14-glossary",
  },
  max_drawdown: {
    title: "Max drawdown",
    body:
      "The worst peak-to-trough fall in equity during the run. This is the emotional cost of the strategy — if you can't stomach a 30% drawdown, don't run a strategy that has one historically.",
  },
  sharpe: {
    title: "Sharpe ratio",
    body:
      "Return per unit of volatility, annualised. Above 1 is good for a long-only equity strategy, above 2 is rare and usually too good to be true.",
  },
  rsi14: {
    title: "RSI (14) — relative strength index",
    body:
      "0–100 score of recent gains vs losses. Above 70 = overbought (pullback more likely). Below 30 = oversold (bounce more likely). 30–70 is no signal.",
    href: "#rsi-relative-strength-index",
  },
  vs_52w: {
    title: "Distance from 52-week extremes",
    body:
      "Price relative to its 1-year high/low. Near the 52w high = strong uptrend (or overextended). Near 52w low = downtrend (or a bottom).",
  },
  win_rate: {
    title: "Historical win-rate",
    body:
      "Percentage of past round-trip trades this exact strategy made on this exact stock that finished profitable. Above 55% is good; above 65% is suspicious unless the sample is big.",
  },
  expectancy: {
    title: "Expectancy",
    body:
      "Average return per round-trip trade. = winRate × avgWinner + lossRate × avgLoser. Positive means the strategy made money on average across all trades.",
  },
  median_hold: {
    title: "Median holding period",
    body:
      "The typical number of calendar days a trade stayed open. Tells you how actively this strategy churns the stock.",
  },
  entry_signal: {
    title: "Now or wait? — entry-quality verdict",
    body:
      "BUY = uptrend (above 200-day SMA), RSI healthy, not extended. WAIT = at 52w highs with RSI > 70 (overbought) or in mid-drawdown. AVOID = below 200-day SMA with 12m return < -10% (confirmed downtrend). HOLD = no fresh entry edge — keep the position if held, no rush to add.",
  },
  off_52w: {
    title: "Distance below 52-week high",
    body:
      "How far today's price is below the highest price of the past year, in %. Near 0% means it's at or near all-time-ish highs — potentially extended. A larger number means the asset has corrected and may be a better entry, if the trend recovers.",
  },
  vix: {
    title: "VIX — fear gauge",
    body:
      "30-day expected volatility of the S&P 500, derived from option prices. <15 = calm. 15-25 = normal. ≥25 = stressed (markets are pricing fear, drawdowns more likely).",
  },
  treasury_yield: {
    title: "10-year Treasury yield",
    body:
      "Annual interest rate on US 10-year government debt. Rising = rate-shock risk for long-duration bonds and growth stocks; falling = recession or flight-to-safety. Direction over 30d is more informative than the absolute level.",
  },
  sp_drawdown: {
    title: "S&P 500 drawdown from peak",
    body:
      "How far the broad US market is below its all-time high, in %. >5% = pullback. >10% = correction. >20% = bear market. Use it to read context — buying ETFs in a -15% S&P drawdown is different from buying at all-time highs.",
  },
  active_stress_regime: {
    title: "Active stress regime",
    body:
      "Whether today falls inside any of the 13 named historical stress windows tracked by the system (GFC, COVID, 2022 rate shock, 2025 tariff shock, …). When active, treat BUYs with extra caution.",
  },
  strategy_vote: {
    title: "Strategy consensus (X / N)",
    body:
      "How many of the system's strategies are currently 'long' this asset (their last fired signal was BUY, not SELL). >50% = the BUY bucket requires it; otherwise the asset goes to WAIT. Buy-and-hold is always long after the first bar — it's the baseline.",
  },
  in_position: {
    title: "Strategy currently long",
    body:
      "True if the strategy's most recent fired signal on this asset was a BUY that hasn't been closed by a later SELL. 'flat' means it would currently not be holding the asset.",
  },
  wall_street_consensus: {
    title: "Wall Street analyst consensus (Yahoo)",
    body:
      "Aggregated rating from the analysts Yahoo tracks. Mean is on a 1-5 scale (1 = strong buy, 5 = strong sell). Most ETFs aren't rated because analysts cover companies, not baskets — that's normal.",
  },
  target_price: {
    title: "Analyst price target",
    body:
      "The mean of all analysts' 12-month price targets. The % vs current is more useful than the absolute level — +10% means analysts collectively expect 10% upside over the next year.",
  },
  freshness: {
    title: "Data freshness",
    body:
      "How long ago this comparison was computed on the Mac and pushed to the API. <24h = green (Live). 24-72h = amber (Stale — refresh recommended). >72h = red (Very stale — refresh before deciding). The scheduled launchd job runs daily at 22:30 UTC.",
  },

  // ---- Recent additions — keep these in sync with the helpers in
  // strategies/tradepro_strategies/{market_state,fees,cross_sectional}.py
  // and the `feedback_explainability_required` memory.
  pct_off_52w_high: {
    title: "% off 52-week high",
    body:
      "How far below the highest close of the last 252 trading days the price is. Anchored to today's UTC date — rolls daily. Always paired with the date the high was set, so 'down 22% off Jan high' is distinct from 'down 22% from yesterday'.",
    href: "#52-week-high-vs-all-time-peak",
  },
  drawdown_from_5y_peak: {
    title: "Drawdown from 5-year peak (long-term valuation)",
    body:
      "How far below the running cumulative max over the entire price window the price is. Different from the 52-week number — captures multi-year peaks. Use as a structural-cheap-vs-history signal, NOT as a short-term entry trigger (a 5-year-old peak doesn't tell you about today's mean-reversion edge).",
    href: "#5y-peak-vs-52w-high",
  },
  max_drawdown_recovery_days: {
    title: "Drawdown recovery time",
    body:
      "How many days the equity curve took to climb back to its prior peak after the deepest drawdown. 'Still recovering' means it hasn't gotten back yet — paired with a days-since-trough count. Half the picture max-DD alone misses; two -30% drawdowns are very different if one took 9 months to come back and the other took 7 years.",
    href: "#drawdown-recovery",
  },
  cross_sectional_momentum: {
    title: "Cross-sectional momentum rank",
    body:
      "This symbol's rank vs basket peers on 12-month return. 1 = highest momentum in the basket. Z-score is how many standard deviations above (positive) or below (negative) the basket mean. Distinguishes 'this symbol is strong' from 'the whole basket is strong' — Family-3 signal complementing the Family-1 (price-vs-MA) strategies.",
    href: "#multi-family-signal-stack",
  },
  stamp_duty_auto: {
    title: "UK stamp duty (auto)",
    body:
      "0.5% SDRT applies to LSE main-market share BUYS only. UCITS ETFs are exempt; non-UK securities pay no UK SDRT. The comparator now resolves the rate per-symbol (auto), so high-turnover strategies on ETFs aren't silently penalised. Pass --stamp-duty <number> to force a flat rate.",
    href: "#uk-stamp-duty",
  },
  bucket_consensus_elevated: {
    title: "BUY from strategy consensus",
    body:
      "When the price-side rule says HOLD but a majority of strategies are currently long, the bucket lifts to BUY. The reason text reads 'N of M strategies currently long; price: <hold reason>' so you can see both the consensus push and the price-side caveat. Disagree with the system? Trust the price-side text; it's the more conservative read.",
    href: "#bucket-vote",
  },
  market_closed_banner: {
    title: "Markets closed today",
    body:
      "When the latest available bar across the basket is older than today (UK bank holiday, weekend, or pre-close run), the digest shows a banner with the actual data date. No external calendar — the data tells us the market wasn't open.",
  },
  llm_preflight: {
    title: "LLM (Ollama) preflight check",
    body:
      "Runs before the slow backtest to verify Ollama is reachable AND the configured model is pulled. Three states: ok → sentiment scoring will run; daemon_down → start `ollama serve`; model_missing → run `ollama pull <model>`. Without it, missing models silently produced null sentiment columns.",
  },
  valuation_flag: {
    title: "Valuation flag (cheap / fair / expensive)",
    body:
      "Quartile-bucket of this symbol's dividend yield vs basket peers. Top quartile (highest yield) → 'cheap'; bottom quartile → 'expensive'; middle 50% → 'fair'. Family-2 starter signal — proxy for value until we have a fundamentals snapshot store with historical-P/E-vs-10y-median. Caveat: a high yield can also flag a structurally distressed asset whose dividend hasn't been cut yet; pair with the technical bucket vote.",
    href: "#valuation-flag",
  },
  swing_score: {
    title: "Swing composite score (0-8)",
    body:
      "Phase-X scorer combining all four signal families into one number: Quality (Sharpe + max-DD recovery time), Valuation (cheap/fair/expensive vs basket), Event (earnings beat-and-retreat), Price (strategy consensus + RSI/SMA). Each layer is 0-2 max. Verdict mapping: ≥6 STRONG_BUY · 4-5 BUY · 2-3 HOLD · 0-1 AVOID. The composite can disagree with the bucket vote — that's the design (different lenses).",
    href: "#swing-composite",
  },
};

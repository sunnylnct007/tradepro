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
};

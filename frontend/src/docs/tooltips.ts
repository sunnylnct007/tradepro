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
    title: "How the confidence score is built",
    body:
      "Starts at 65% if the strategy fired within the last 3 bars, 40% otherwise. Adds/subtracts 10% when the supporting signals (trend direction, RSI zone) agree or disagree. Clamped to 95% — we never claim certainty.",
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
};

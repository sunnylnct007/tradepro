/**
 * Help system content. One entry per topic, each with sections rendered
 * as markdown. The Help page lists topics; /help/:slug renders one.
 *
 * Editing rules:
 * - Keep each section 2-4 short paragraphs. Beginners stop reading at
 *   wall-of-text.
 * - Plain English first; the maths or jargon goes after the intuition.
 * - Use small concrete examples (£10k, S&P, FTSE) — abstract is harder
 *   to remember.
 * - Link cross-topic where it helps ("see Risk metrics →").
 */

/** Inline diagram shown above the markdown body. Lets a Help section
 * say "RSI looks like this" with an actual chart instead of asking
 * the reader to picture it. Each kind maps to a recharts demo in
 * components/StrategyDiagrams.tsx — synthetic data, designed to
 * teach the shape, not to claim any real market period. */
export type HelpDiagramKind =
  | "sma_crossover"
  | "rsi_bands"
  | "macd_histogram"
  | "donchian_breakout"
  | "range_position"
  | "return_histogram";

export interface HelpSection {
  heading: string;
  body: string; // markdown
  diagram?: HelpDiagramKind;
}

export interface HelpTopic {
  slug: string;
  title: string;
  summary: string;
  emoji: string;
  sections: HelpSection[];
}

export const HELP_TOPICS: HelpTopic[] = [
  {
    slug: "getting-started",
    title: "Getting started",
    summary: "What TradePro answers and how to read the Compare page.",
    emoji: "🧭",
    sections: [
      {
        heading: "The two questions this app answers",
        body: `
TradePro is built around two questions a normal investor actually has:

1. **Is today a good day to buy, or should I wait?**
2. **If yes, which ETF should I buy?**

Most trading apps show you a chart and a price and leave it at that.
This one looks at your shortlist of ETFs, runs five different rule
sets across each of them, checks how each one survived past stress
events (2008 crash, 2020 COVID, 2022 rate shock), looks at today's
broader market mood, and lands on a single verdict per ETF: **BUY**,
**WAIT**, or **AVOID** — with the reasoning visible.
        `,
      },
      {
        heading: "BUY / WAIT / AVOID buckets",
        body: `
Every ETF in your watchlist falls into exactly one of three buckets:

- **BUY today** — price action is friendly (uptrend, RSI healthy,
  not extended) AND more than half the strategies are currently
  long the asset. Both signals lining up gives confidence.
- **WAIT** — either the price is at the highs and overbought, or
  it's mid-correction and the trend hasn't stabilised. Better
  entries usually come along.
- **AVOID** — confirmed downtrend (below 200-day moving average
  AND 12-month return < -10%). Don't fight the tape.

The buckets are just a triage. Click any ETF row to see the full
reasoning trail.
        `,
      },
      {
        heading: "Why this isn't advice",
        body: `
Every verdict here is a decision aid. The system uses transparent
rules — you can read them, argue with them, and disagree. It does
**not** know your tax position, your existing portfolio, your
liquidity needs, or your risk tolerance.

If you're not sure whether something is right for you, talk to a
regulated adviser before acting.
        `,
      },
    ],
  },

  {
    slug: "trading-basics",
    title: "Trading basics",
    summary: "Plain-English intro to BUY/SELL/HOLD, ETFs, time horizons, and risk.",
    emoji: "📚",
    sections: [
      {
        heading: "Shares, ETFs, and what you're actually buying",
        body: `
A **share** (or stock, or equity) is a tiny slice of a single
company. Owning AAPL means owning a piece of Apple.

An **ETF** (Exchange-Traded Fund) is a basket of many shares
bundled together and traded as one. Buying VOO buys you a sliver
of all 500 companies in the S&P 500. Buying VWRP.L buys you a
slice of the global stock market.

ETFs are usually safer than single stocks for beginners because
the basket diversifies — if one company collapses, you barely
notice. The trade-off is you also miss out on big winners, since
they're averaged with the laggards.
        `,
      },
      {
        heading: "BUY / SELL / HOLD",
        body: `
- **BUY** — open a new position. You're putting money to work.
- **SELL** — close an existing position. You're taking it back
  to cash (and possibly realising a profit or a loss).
- **HOLD** — do nothing. Sometimes the best move is to keep what
  you have and not act.

A common beginner mistake: thinking "HOLD" means "the strategy is
broken because it's not telling me what to do". HOLD is a real
choice — most days, the right action is no action.
        `,
      },
      {
        heading: "Time horizons matter",
        body: `
The same ETF can be a great long-term hold and a terrible short-
term trade — and vice versa. Match the strategy to the horizon:

- **Day / weeks** — fast strategies like RSI mean-reversion or
  Donchian breakout fire often. Lots of small trades, fees and
  taxes matter a lot.
- **Months (3–12)** — trend strategies like SMA / MACD crossovers
  catch medium-term moves. Drawdown matters more than absolute
  return.
- **Years** — buy-and-hold of a broad ETF beats nearly all
  strategies once you net out fees + taxes. Sharpe ratio and
  regime survival are what to look at.

The Compare page is built for **months-to-years horizons**. For
day-trading individual stocks, see the Scanner page.
        `,
      },
      {
        heading: "Diversification — the only free lunch",
        body: `
"Don't put all your eggs in one basket" is the actual rule of
finance. Owning 30 different things is much safer than owning one
thing, even if the average return is the same — because the
downside of any single one is much less catastrophic.

ETFs do diversification automatically. A FTSE 100 tracker holds
100 companies; a global tracker holds 8000+. That's why "buy a
broad ETF and hold it" is a respectable answer for most people.
        `,
      },
      {
        heading: "Dollar-cost averaging (DCA)",
        body: `
Instead of putting £10,000 into a fund all at once, you put
£1,000 in each month for 10 months. You buy more shares when
the price is low, fewer when it's high — average cost ends up
slightly below the simple average price.

DCA doesn't beat lump-sum on average (lump-sum wins ~67% of the
time historically), but it does smooth out the emotional cost of
buying right before a crash. For most beginners, the
psychological benefit is worth the small expected-return cost.
        `,
      },
      {
        heading: "Risk vs return",
        body: `
Higher returns require taking on more risk. There is no investment
that pays 12% a year with no chance of loss — if there were,
everyone would do it and the rate would fall.

The real question isn't "can I get high returns?" — it's **"how
much downside can I emotionally tolerate?"**. If you'd panic-sell
during a 30% drawdown, don't run a strategy that has had 30%
drawdowns historically. The Compare page shows max-DD for every
ETF + strategy combination so you can see this in advance.
        `,
      },
    ],
  },

  {
    slug: "indicators",
    title: "Indicators (technical analysis)",
    summary: "SMA, EMA, RSI, MACD, Donchian — what they measure and what they're for.",
    emoji: "📈",
    sections: [
      {
        heading: "Why technical indicators?",
        body: `
A price chart is a stream of numbers. **Indicators** transform
those numbers into signals — "trending up", "overbought", "broke
out" — that humans can read at a glance.

None of them tell the future. They condense the past into shapes
the brain handles better than raw price ticks. The skill is
knowing which indicator is informative in which context.
        `,
      },
      {
        heading: "Simple Moving Average (SMA)",
        diagram: "sma_crossover",
        body: `
**SMA(N)** = average of the last N closing prices. Smooths out
day-to-day noise.

- SMA(20) ≈ "where has price been over the last month".
- SMA(50) ≈ "...the last 2-3 months".
- SMA(200) ≈ "...the last year".

When today's price sits **above the SMA(200)**, the asset is in
a long-term uptrend. Below it, downtrend. It's the single most
important sanity check in trend-following.

When a fast SMA (e.g. SMA(20)) crosses **above** a slow SMA
(SMA(50)), it's called a **golden cross** — a classic buy
signal. The reverse is a **death cross** — sell. The diagram above
shows both: fast (blue, SMA(8)) tracking faster than slow (purple,
SMA(20)), with the green dot marking a golden cross and the red
dot a death cross.
        `,
      },
      {
        heading: "Exponential Moving Average (EMA)",
        body: `
Same idea as SMA but recent prices count more than old ones.
Reacts faster to changes, but is also noisier. Used inside MACD
and a lot of momentum strategies.
        `,
      },
      {
        heading: "RSI — Relative Strength Index",
        diagram: "rsi_bands",
        body: `
A number between 0 and 100 measuring how much recent gains
outweigh recent losses.

- **RSI > 70** = overbought (red zone above). Price has run hard
  recently; a pullback is statistically more likely than another
  big jump.
- **RSI < 30** = oversold (amber zone below). Price has dropped
  hard; a bounce is statistically more likely.
- **RSI 30-70** = neutral. No edge in either direction.

A common pitfall: in a strong trend, RSI can stay above 70 (or
below 30) for weeks. Don't sell purely because RSI is high — use
it alongside a trend check.
        `,
      },
      {
        heading: "MACD — Moving Average Convergence Divergence",
        diagram: "macd_histogram",
        body: `
Two EMAs (fast 12-day and slow 26-day) subtracted from each other,
then a 9-day EMA of that line called the **signal line**.

When the MACD line (blue) crosses **above** the signal line
(purple), momentum is turning up — buy. When it crosses below,
sell. The **histogram** is just MACD − Signal — green bars when
momentum is bullish, red when bearish. The further the lines
drift apart, the taller the bar and the stronger the momentum.

MACD is good at catching the start of trends; it's bad in flat,
choppy markets where it whipsaws between buy and sell.
        `,
      },
      {
        heading: "Donchian channel — breakout detection",
        diagram: "donchian_breakout",
        body: `
Track the highest high and lowest low of the last N days
(commonly 20). When today's close exceeds the prior 20-day high,
**breakout — buy**. When it drops below the prior 20-day low,
**breakdown — sell**.

The diagram shows a sideways consolidation where price stays
inside the channel, then a clean breakout (green dot) once price
punches through the upper band — that's the classic Turtle
breakout entry.

Works brilliantly when assets are trending; kills you in sideways
markets where the breakout immediately fails.
        `,
      },
      {
        heading: "Range position (52w) — where in the year are we?",
        diagram: "range_position",
        body: `
A simple but powerful sanity check the engine added in May 2026
after a real-world false-positive (VUKE.L flagged BUY at 5% off
its 52w high after a +24% YoY run — not a dip, just a minor
cooling near the top).

\`range_position_pct = (current − 52w_low) / (52w_high − 52w_low) × 100\`

- **0–35th** percentile → near the lows. Genuine dip territory;
  positive modifier on the swing score.
- **35–65th** → mid-range. Neutral.
- **65–80th** → near highs. Limited swing upside; demote BUY → HOLD.
- **80–100th** → at highs. Hard cap on swing signal at WATCH
  regardless of other criteria.

The diagram above plots all four bands so you can see how a 5%
pullback can sit in completely different parts of the annual
range depending on how the year went.
        `,
      },
    ],
  },

  {
    slug: "risk-metrics",
    title: "Risk metrics",
    summary: "CAGR, Sharpe, max drawdown, volatility — the numbers that matter for long-term investing.",
    emoji: "📊",
    sections: [
      {
        heading: "CAGR — Compound Annual Growth Rate",
        body: `
The smoothed annual return of an investment over a multi-year
period. £10,000 → £20,000 over 10 years has a CAGR of ~7.2%.

Why use CAGR instead of total return? It lets you compare
investments held for different lengths of time on equal footing.
A strategy that doubled in 5 years (CAGR ~14.9%) is more
impressive than one that doubled in 10 (CAGR ~7.2%).

Long-run rule of thumb: a broad equity index returns ~7-10%
CAGR over multi-decade windows. Anything claiming 20%+ CAGR
sustained should be treated with extreme suspicion.
        `,
      },
      {
        heading: "Sharpe ratio — return per unit of risk",
        diagram: "return_histogram",
        body: `
Strategy A returns 12% with 30% volatility. Strategy B returns
8% with 10% volatility. Which is better?

Sharpe ratio answers that. Roughly: \`return / volatility\`
(annualised, with the risk-free rate subtracted out). Higher is
better.

The histogram above shows daily returns of a sample strategy.
Sharpe measures both how **far right of zero** the centre sits
(returns above the risk-free rate) AND how **tight** the
distribution is around that centre (low volatility). A peaky,
right-skewed shape gives a high Sharpe; a flat or left-skewed
shape gives a low one.

- **Sharpe > 1** = good for a long-only equity strategy.
- **Sharpe > 2** = exceptional, almost always overfit or lucky.
- **Sharpe < 0.5** = barely worth the risk.

Use Sharpe when comparing two strategies with different
volatility profiles. Don't use it when one strategy has a fat
left tail (rare but catastrophic losses) — Sharpe flatters those.
        `,
      },
      {
        heading: "Max drawdown — the emotional cost",
        body: `
The worst peak-to-trough decline an investment ever had. -35%
means at some point your portfolio dropped 35% from its high.

Max-DD is **the single most important risk number for retail
investors**, because it's what makes people sell at the bottom.
A strategy with 15% CAGR and 60% max-DD looks great on paper but
will be abandoned by 90% of humans during the drawdown.

Rule: pick strategies whose max-DD you can actually stomach
**without** acting. If you'd panic-sell during a 25% drop, don't
run a strategy that's had 50% drawdowns.
        `,
      },
      {
        heading: "Volatility (standard deviation)",
        body: `
How much returns swing around their average, day to day. A
volatility of 15% per year means about 2/3 of years end within
±15% of the mean.

Stocks: ~15-25% annual volatility. Bonds: ~5-10%. Crypto:
60-100%+. Higher vol means more uncertainty about any single
outcome — but doesn't necessarily mean lower long-run returns.
        `,
      },
      {
        heading: "Beta — relative to the market",
        body: `
How much an asset moves when the broad market moves 1%. Beta of
1.0 = moves with the market. Beta 1.5 = moves 1.5x as much (more
aggressive). Beta 0.5 = half as much (defensive).

For ETFs, beta tells you how concentrated your portfolio's
market exposure is. Two high-beta ETFs (e.g. tech-heavy QQQ +
small-cap IWM) double your sensitivity even though they look
"diversified" by name.
        `,
      },
    ],
  },

  {
    slug: "strategies",
    title: "Strategies in plain English",
    summary: "The five rule sets the system uses, when each works, and when it fails.",
    emoji: "🎯",
    sections: [
      {
        heading: "Buy and hold — the benchmark",
        body: `
Buy on day 1, never sell. Every other strategy must beat this
**after fees and taxes** to be worth the complexity.

**When it wins:** long bull markets, broad index ETFs over years.
Almost always wins for the average retail investor.
**When it loses:** during multi-year bear markets when active
strategies could've moved to cash.
        `,
      },
      {
        heading: "SMA crossover — trend following",
        body: `
Buy when the fast moving average (e.g. SMA(20)) crosses above
the slow one (SMA(50)). Sell when it crosses back below.

**When it wins:** sustained trends — months of going up or going
down without much choppiness.
**When it loses:** sideways markets — fast SMA whipsaws above and
below slow SMA, creating losing trades and accumulating fees.
        `,
      },
      {
        heading: "RSI mean-reversion — buy the dip",
        body: `
Buy when RSI(14) recovers from below 30 (oversold) back up.
Sell when it recovers from above 70 (overbought) back down.

**When it wins:** sideways markets where price oscillates around
a level. Fires often, captures small moves.
**When it loses:** strong trends — RSI can stay overbought or
oversold for weeks, and the strategy gets shaken out.
        `,
      },
      {
        heading: "MACD signal cross — momentum",
        body: `
Buy when the MACD line crosses above its signal line. Sell on
the reverse. Catches turning points in momentum.

**When it wins:** persistent trends with clear inflection points.
**When it loses:** flat markets — MACD wiggles around zero and
generates many false signals.
        `,
      },
      {
        heading: "Donchian breakout — turtle trading",
        body: `
Buy when today's close exceeds the highest close of the prior N
(usually 20) days — a clean breakout to a new high. Sell on the
reverse breakdown.

**When it wins:** strong trends, especially in commodities and
trending equity markets.
**When it loses:** range-bound markets — every "breakout" fails
back into the range.
        `,
      },
      {
        heading: "Why we run every strategy together",
        body: `
No single rule set works in every market. Trend strategies
underperform in choppy markets; mean-reversion underperforms in
trending markets. By running every registered strategy
(currently seven — buy & hold, SMA crossover, RSI mean-reversion,
MACD signal-cross, Donchian breakout, Ichimoku Cloud, and
Bollinger bounce) and looking at the **consensus** ("how many of
these are currently long?"), we get a more robust read than any
single one would give us.

When a clear majority of strategies — built on different
philosophies — all say long, that's a much stronger signal than
any one of them saying long in isolation.
        `,
      },
    ],
  },

  {
    slug: "market-context",
    title: "Market context (macro)",
    summary: "VIX, 10-year yield, S&P drawdown, stress regimes — why the market mood matters.",
    emoji: "🌍",
    sections: [
      {
        heading: "VIX — the fear gauge",
        body: `
The VIX measures expected volatility of the S&P 500 over the
next 30 days, derived from option prices. It's high when traders
are paying up to hedge against scary moves.

- **VIX < 15** — calm market. Complacency. Tail risk under-
  priced.
- **VIX 15-25** — normal. Most trading days live here.
- **VIX > 25** — stressed. Drawdowns more likely; BUYs probably
  early.
- **VIX > 40** — panic. Historically a great long-term entry,
  but only for steel-stomached investors.

The VIX itself doesn't predict crashes — but it tells you the
mood you're trading into.
        `,
      },
      {
        heading: "10-year Treasury yield",
        body: `
The interest rate on US 10-year government debt. The single most
important macro number on Earth, because it's the yardstick
against which everything else is priced.

- **Rising yields** — bonds fall in price; long-duration assets
  (growth stocks, REITs) get hit; rate-sensitive sectors
  underperform. 2022 was a textbook example.
- **Falling yields** — flight to safety usually, or a recession
  expectation. Bonds rally; defensive sectors do well.

Direction over 30 days is more informative than the absolute
level. A 10Y at 4% rising 0.5% over a month is a different mood
to one falling 0.5%.
        `,
      },
      {
        heading: "S&P drawdown from peak",
        body: `
How far the broad US market is below its all-time high.

- **0 to -5%** — at the highs. Buying ETFs here is buying near
  the top of the range; entries are extended.
- **-5 to -10%** — pullback. Normal correction territory.
- **-10 to -20%** — correction. Historically a fine
  multi-year-horizon entry zone.
- **-20%+** — bear market. Generationally good entries usually
  but you need cash and patience.

Use this as context for the bucket assignments — buying ETFs in
a -15% S&P drawdown is fundamentally different from buying at
all-time highs.
        `,
      },
      {
        heading: "Active stress regimes",
        body: `
TradePro tracks 13 named historical stress windows: the dot-com
bust, GFC, Eurozone debt crisis, Volmageddon, COVID crash, 2022
rate shock, Aug 2024 yen-carry unwind, the 2025 tariff shock,
and a few more.

For every ETF + strategy, the system shows how that combination
performed **inside each window** — return, max drawdown, days
covered. So when an ETF goes BUY today, you can see it lost 34%
in COVID and -55% in the GFC, and decide if you can stomach a
repeat.

If today happens to fall **inside** one of those windows, the
"Active stress regime" indicator on the page lights up — read
the BUYs with extra caution.
        `,
      },
      {
        heading: "Why news matters but isn't always actionable",
        body: `
Geopolitical events (wars, elections, central-bank surprises),
earnings, and macro releases move markets — but the move is
often priced in within minutes, before any retail investor can
act. Selling on yesterday's bad news usually means selling the
bottom.

The honest takeaway: news is **context** for understanding why
prices moved, not usually a trigger for action. TradePro already
runs a local LLM over fresh headlines and **demotes BUYs to WAIT**
when sentiment turns sharply negative — see "How the LLM helps"
below for the exact thresholds. The point still stands though:
sentiment is a tiebreaker / safety net, never the reason to enter
or exit on its own.
        `,
      },
    ],
  },

  {
    slug: "llm-pipeline",
    title: "How the LLM helps (and where it doesn't)",
    summary: "Sentiment scoring, the demotion rule, and the strict principle that the LLM never decides.",
    emoji: "🤖",
    sections: [
      {
        heading: "What the LLM does",
        body: `
TradePro runs a small **local language model** (default
\`llama3.1:8b\` via Ollama on your machine) over each news headline
attached to an ETF. For each headline it returns:

- **sentiment** — a number from -1 (very negative) to +1 (very
  positive)
- **themes** — short tags like \`["earnings", "guidance"]\` or
  \`["geopolitics", "regulation"]\`
- **material** — a boolean: would this plausibly move the price,
  or is it filler ("3 ETFs to consider in May")?

These are aggregated into a 7-day rolling **mean sentiment** and a
count of **material-negative headlines** per ETF.
        `,
      },
      {
        heading: "What the LLM does NOT do",
        body: `
**The LLM never produces the buy/sell decision.** That stays in the
rule-based engine — price vs 200-day SMA, RSI, drawdown, strategy
consensus. The LLM output only adds *one extra check* to the
decision trace ("Sentiment trend (7d)") and applies one demotion
rule.

This is deliberate: an LLM can hallucinate, change its mind across
runs, and quietly drift. We wrote the rule chain on purpose so a
human can argue with each step. The LLM just *contextualises* —
it doesn't override.
        `,
      },
      {
        heading: "The demotion rule",
        body: `
The only place sentiment changes a verdict is the **BUY → WAIT
demotion**:

> If a verdict would be BUY by price + strategy consensus,
> AND the 7-day rolling mean sentiment is ≤ **−0.30**,
> AND there are ≥ **2 material-negative headlines**,
> the verdict is downgraded to WAIT.

Both thresholds and the lookback window ride in the JSON payload
(\`payload.llm.demotion_rule\`), so the UI shows the *exact* rule
that fired — no hidden numbers. When a row gets demoted you see a
banner in its expand panel saying which condition triggered.

A BUY is **never promoted** by positive sentiment alone. The
rule-based engine has to also pass — sentiment is a brake, not an
accelerator.
        `,
      },
      {
        heading: "Failure handling",
        body: `
LLM calls fail (network down, model parses garbage, Ollama not
running). When that happens the comparator does **not** crash —
each row carries a \`sentiment_status\` flag:

- **scored** — every headline scored cleanly
- **partial** — some succeeded, some failed (visible per item)
- **all_failed** — none scored (the trace check goes to "warn")
- **no_news** — no recent headlines to score
- **provider_down** — LLM unavailable, sentiment didn't influence
  the verdict at all

The status pill at the top of /compare shows whether the LLM is
healthy. When it's not, verdicts run on rules-only — never silently.
        `,
      },
      {
        heading: "Picking a different model",
        body: `
The default is \`llama3.1:8b\` — fast and accurate enough for
sentiment scoring. To swap models, set an env var on the host
running the Strategy Engine:

\`\`\`bash
export TRADEPRO_OLLAMA_MODEL=qwen3.5:latest    # broader knowledge
export TRADEPRO_OLLAMA_MODEL=phi4              # stronger reasoning
\`\`\`

The model name is captured in the payload alongside every score so
you can A/B-test prompts and compare quality across models without
re-running the comparator (the cache is keyed by hash of headline +
model, so each model has its own cache slice).
        `,
      },
      {
        heading: "Caching",
        body: `
Headlines repeat across runs (Yahoo's news feed for QQQ today
mostly overlaps tomorrow's). Scored results live in
\`~/.tradepro/cache/llm-sentiment.json\`, keyed by
\`hash(model + headline)\` — each headline costs **one** LLM call
total, not one per refresh.

To force a re-score, delete the cache file. To pick a different
model, the cache lookup automatically misses (different key) so
you don't have to clear anything.
        `,
      },
    ],
  },

  {
    slug: "rationale",
    title: "Plain-English rationale (no hallucination)",
    summary: "How the per-verdict prose summary is generated, verified, and rejected when it's wrong.",
    emoji: "📝",
    sections: [
      {
        heading: "What the rationale is",
        body: `
On every ETF in the Compare expand panel you'll see a green-bar
**In plain English** block — a 2-3 sentence summary explaining the
verdict in everyday language, plus a short "Why" list and a "Caveats"
list.

It is **not** a new decision. The verdict comes from the rule engine
(price action + strategy consensus + sentiment thresholds, all
visible in the rules ladder above the rationale). The rationale just
*explains* what the rules already decided.
        `,
      },
      {
        heading: "Two sources, both honest",
        body: `
The badge in the top-right of the rationale block tells you which
path produced the prose:

- **LLM ✓ verified** — the LLM (Ollama / Claude depending on config)
  wrote the summary, AND every numerical claim in the summary was
  found verbatim in the input facts. Safe to read.
- **template (deterministic)** — built mechanically from the same
  facts using a fixed sentence template. No LLM creativity. Less
  elegant prose but factually 100% correct.
- **template (LLM hallucinated)** — the LLM produced a summary, but
  it referenced a number that wasn't in the inputs (e.g. invented a
  year, a percentage, a holding name). The verifier rejected it and
  the deterministic template ran instead.
- **template (LLM unavailable / failed / empty)** — the LLM wasn't
  reachable or returned nothing. Template kicked in.

In **none** of these cases does the user see fabricated content.
        `,
      },
      {
        heading: "Why we do this",
        body: `
Hallucinated numbers in a financial-decision tool are dangerous.
The LLM sometimes writes "QQQ lost 55% in 2008" — which is
historically true — but our facts only include the regime name
"GFC", not the year "2008". The LLM is using outside knowledge,
which we *cannot* verify against our pipeline.

Strict rule: **if a number doesn't trace to the input facts, we
don't show the LLM's version.** The template is built from the
same facts and produces a summary that's slightly clunkier but
provably correct — every word maps back.

This is the same accuracy contract we use in the MCP server (Ask
Claude topic). LLM produces context, never decides; verifier
catches every claim; doubt → fallback to safe path.
        `,
      },
      {
        heading: "Picking which LLM",
        body: `
Sentiment scoring uses the cheap local model (\`llama3.1:8b\` via
Ollama) — it's running 8 headlines per ETF, speed matters more than
nuance. Rationale generation is per-symbol (much fewer calls) and
prose quality matters more, so by default it can use a stronger
model.

Override per task via env vars:

\`\`\`bash
export TRADEPRO_LLM_RATIONALE=claude   # use Claude API for rationale
export ANTHROPIC_API_KEY=sk-...
export TRADEPRO_LLM_SENTIMENT=ollama   # keep sentiment local
\`\`\`

Either way the verifier still runs. Better model = more rationales
that pass verification (more LLM-source rather than template-source
badges) — but never the difference between accurate and inaccurate.
        `,
      },
      {
        heading: "Verification notes",
        body: `
If the rationale's badge says "LLM hallucinated" or "LLM failed",
expand the **Verification notes** disclosure to see exactly what
went wrong: which numbers couldn't be traced, what error the LLM
threw. This is pure transparency — every fallback decision is
auditable.
        `,
      },
    ],
  },

  {
    slug: "ask-claude",
    title: "Ask Claude about your portfolio",
    summary: "Use the MCP server to query your TradePro data from Claude Desktop, Cursor, or our /chat page — with strict citation tracking and fail-closed verification.",
    emoji: "💬",
    sections: [
      {
        heading: "What is the MCP server?",
        body: `
TradePro ships an **MCP (Model Context Protocol) server** —
\`tradepro-mcp\` — that exposes the platform as a set of tools and
resources an LLM can call. Any MCP-aware client (Claude Desktop,
Cursor, our future /chat page) can ask questions of your portfolio
and get answers grounded in your actual data, not hallucinated.

The server is a thin layer over what you already have: the comparator,
market_state, news + sentiment, regimes, health. Each tool returns
structured JSON with a \`_source\` URI for every fact, so the LLM
must cite when it claims a number.
        `,
      },
      {
        heading: "Three accuracy guarantees",
        body: `
This is a financial-decision tool. Hallucinated numbers about
returns or drawdowns are dangerous. The MCP server is built around
three non-negotiable guarantees:

1. **Citation tracking.** Every tool output includes \`_source\`
   paths like \`tradepro://compare/etf_us_core/rows[0]/stats/sharpe\`.
   The decomposition prompt requires the LLM to cite by source on
   every quantitative claim.

2. **Fail-closed verification.** Before delivering any answer, the
   LLM must call \`verify_answer\`, which extracts each claim and
   checks it against the tool outputs. If \`should_refuse=true\`,
   the LLM either rewrites and re-verifies once, or refuses with
   the specific failure reasons. **No unverified number is ever
   delivered.**

3. **Full traceability.** Every Q&A leaves a JSON trace at
   \`~/.tradepro/traces/<trace_id>.json\` capturing the
   decomposition, every tool call (with inputs + outputs +
   latency), every LLM call (prompt hash + raw response), the
   draft answer, the verification verdicts, and the outcome
   (\`delivered\` / \`refused\`). The same trace is exposed at
   \`tradepro://trace/<trace_id>\` for inspection.
        `,
      },
      {
        heading: "Setting up Claude Desktop",
        body: `
Drop this into \`~/Library/Application Support/Claude/claude_desktop_config.json\`:

\`\`\`json
{
  "mcpServers": {
    "tradepro": {
      "command": "uv",
      "args": ["run",
               "--project", "/path/to/tradepro/strategies",
               "tradepro-mcp"],
      "env": {
        "TRADEPRO_API_URL": "http://localhost:5080",
        "TRADEPRO_OLLAMA_MODEL": "llama3.1:8b"
      }
    }
  }
}
\`\`\`

Restart Claude Desktop. You'll see a 🔌 icon in the bottom-left of
the chat — click it and you should see "tradepro" with its tools
listed. Try one of the prompts:

- \`@tradepro analyse_etf("QQQ")\`
- \`@tradepro should_i_buy_today("etf_us_core")\`
- \`@tradepro compare_etfs("VOO,VWRP.L")\`

The decomposition prompt fires automatically — Claude will plan
sub-questions, call tools, draft, verify, and either answer
(with citations) or refuse with explicit reasons.
        `,
      },
      {
        heading: "What it can and can't do",
        body: `
**Can:**
- Pull current and historical compare data for any cached universe
- Show how a symbol survived past stress windows (GFC, COVID, 2022)
- Read recent news + LLM-scored sentiment per symbol
- Trigger a fresh comparator run (slow — only on explicit ask)
- Show system health + Strategy Engine liveness
- Verify any answer against tool outputs

**Cannot:**
- **Override the BUY/SELL/HOLD verdict.** That comes from the rule
  engine. The LLM may explain *why* the engine said BUY; it cannot
  disagree.
- Make up a number that's not in a tool response. The verifier
  catches this — \`should_refuse\` goes true and the answer is
  blocked.
- Place an order. There is no broker integration in MCP.
        `,
      },
      {
        heading: "Inspecting a refused answer",
        body: `
When the verifier blocks an answer, the trace contains everything
you need to debug:

\`\`\`bash
ls -t ~/.tradepro/traces/ | head
cat ~/.tradepro/traces/<trace_id>.json | jq '.verification.verdicts'
\`\`\`

Each verdict shows:
- the exact claim text
- status: supported / contradicted / unsupported
- the citation path (if it was supported)
- the evidence found (or null)
- model confidence

If a claim was \`unsupported\`, it means the LLM's output mentioned
a number not present in any tool response — exactly the kind of
hallucination this whole layer exists to prevent.
        `,
      },
    ],
  },

  {
    slug: "how-it-works",
    title: "How TradePro works under the hood",
    summary: "The push pipeline, the Strategy Engine → API → frontend flow, and the full architecture doc.",
    emoji: "🔧",
    sections: [
      {
        heading: "Where the compute happens",
        body: `
The **Strategy Engine** (a Python worker — runs in the
\`tradepro-worker\` docker container or as a launchd job on a Mac)
does the heavy work. Yahoo Finance prices are fetched, five
strategies are backtested over multi-year history, regime overlaps
are sliced, market-state verdicts are computed, analyst-consensus
snapshots are pulled — all in Python, using local CPU.

The result is a single JSON payload. The Engine POSTs it to the
API (auth: a static bearer token set in compose env or App Service
config).
The API stores the payload to disk (\`/data/compare/<universe>.json\`).
The frontend GETs the latest payload and renders this page.

This means: even if Yahoo goes down or the network's slow, the
frontend keeps showing the last known good answer. The
provenance bar at the top of /compare tells you when it was
last refreshed.
        `,
      },
      {
        heading: "Scheduled refresh",
        body: `
In the docker stack the \`worker\` service runs a loop:
re-runs the comparator every \`WORKER_INTERVAL_SECONDS\` (default
30 min) and heartbeats the API every 5 min. Results push
automatically — you should never have to think about it.

For a Mac launchd install (no Docker), a \`launchd\` job fires every
day at 22:30 UTC after the US close:

\`\`\`
bash strategies/scripts/install-launchd.sh
\`\`\`

Either way, logs land at \`~/.tradepro/logs/refresh-<date>.log\`.
        `,
      },
      {
        heading: "Full architecture",
        body: `
For the full deep-dive — components, data flows, indicator maths,
security posture, observability model — see the architecture
document in the repo:

→ [docs/ARCHITECTURE.md](https://github.com/sunnylnct007/tradepro/blob/main/docs/ARCHITECTURE.md)

It's a single source of truth that the API, frontend, and Python
package all stay aligned with.
        `,
      },
    ],
  },

  {
    slug: "data-sources",
    title: "Data sources",
    summary: "Every external feed TradePro uses, what it provides, what's free, and how to spot when one is degraded.",
    emoji: "📡",
    sections: [
      {
        heading: "Why this matters",
        body: `
Every BUY / WAIT / AVOID signal sits on top of data from external
providers. If a provider quietly fails (rate-limit, schema change,
auth expired), the verdict still renders — but it's based on stale
or missing inputs. **Always glance at the Data sources card on the
Health page before trusting today's recommendation.**

This topic lists every feed we use, what's free vs paid, and what
breaks when each one degrades.
        `,
      },
      {
        heading: "Yahoo Finance — primary price + fundamentals",
        body: `
**What we use:** OHLCV daily bars, 200-day SMA, RSI, distance from
52-week high, 52-week range position, 5-year drawdown from peak,
12-month momentum, fundamentals (P/E ratio, dividend yield, expense
ratio, n_holdings, sector weights, top holdings, AUM, summary text),
historic earnings dates with EPS surprise, analyst consensus
(target price, buy/hold/sell counts, upside %).

**Auth:** none. Free, undocumented, rate-limited.

**Cost:** £0.

**Where it shows up:** every single decision. The price chain
(SMA200, RSI, drawdown) drives the per-symbol BUY/WAIT/AVOID. The
fundamentals drive the swing-composite valuation layer + the
horizon classification engine's passive score.

**What breaks if it degrades:** today's prices fall back to the
last cached close. You'll see the freshness banner go amber, and
the "Range position (52w)" / "RSI" rows in the decision trace
will show the stale numbers. Yahoo is the load-bearing dependency
of the whole platform — if it goes down for >24h the comparator
emits empty payloads.

**How to spot a degradation:** the Compare page's freshness pill
(top of /compare) goes from green (<24h) to amber (24-72h) to red
(>72h). The Health page Data sources card flags Yahoo as
"degraded" or "down" once the cache is older than 24h / 72h.
        `,
      },
      {
        heading: "Finnhub — forward earnings calendar",
        body: `
**What we use:** upcoming earnings announcements per symbol over
the next ~60 days. Date + estimate EPS + estimate revenue + when
it reports (before market open / after close).

**Auth:** API key, passed as \`token\` query parameter.

**Cost:** £0 (free tier 60 req/min, no card needed). Sign up at
[finnhub.io](https://finnhub.io).

**Where it shows up:** the email digest's "⚠ EPS reports in Xd"
warning on holdings + BUY candidates. Without it, you get the
verdict but no advance warning that the position is about to face
earnings volatility.

**What breaks if it degrades:** EPS warnings disappear from the
digest + dashboard. The horizon-engine's swing-layer "active
catalyst" check still fires from yfinance historic earnings, so
classify_horizons still scores correctly — but you lose the
forward-looking heads-up.

**Setup:** add \`TRADEPRO_FINNHUB_API_KEY=<key>\` to \`.env\`,
recreate the api: \`docker compose up -d --force-recreate api\`.
Confirm with \`curl http://localhost:5080/api/integrations/finnhub/earnings-calendar?symbol=NVDA\` — should return a non-empty events list.
        `,
      },
      {
        heading: "Trading 212 — your portfolio",
        body: `
**What we use:** open positions (qty, average price, current
price), instruments registry (every symbol your account can
trade), account summary (cash + equity).

**Auth:** API key. Modern T212 accounts issue a single key —
NO secret. Older accounts had a key+secret pair (HTTP Basic).
The client auto-detects which scheme to use.

**Cost:** £0 with any T212 brokerage account.

**Mode:** \`demo\` (paper trading) or \`live\` (real money). Always
shown as a chip on every page so you can't confuse them.

**Where it shows up:** the "Your portfolio" card on the Decide
dashboard (BUY MORE / HOLD / TRIM advice per holding), the
Portfolio page (full table), the email digest's "What you hold"
section. Three MCP tools expose it to Claude:
\`get_portfolio\`, \`get_portfolio_signals\`, \`search_t212_instruments\`.

**What breaks if it degrades:** the holdings panel goes empty.
The error gets surfaced explicitly with the underlying T212
HTTP status, not silently masquerading as "no positions"
(that bug shipped briefly and was caught by the user — fixed in
the May 2026 release).

**Rate limit:** T212 caps \`/equity/portfolio\` at 1 req/1s. We
cache positions for 30s on the api side so multiple consumers
(dashboard + portfolio page + MCP) don't trip the limit.
        `,
      },
      {
        heading: "Ollama — local LLM (sentiment + rationale)",
        body: `
**What we use:** \`llama3.1:8b\` by default, runs natively on your
machine via Ollama. Two jobs: sentiment scoring on every news
headline (-1 to +1 with theme tags) and rationale generation
(plain-English "why the engine said BUY" per symbol).

**Auth:** none — runs locally on \`localhost:11434\` (host) or
\`host.docker.internal:11434\` from inside the worker container.

**Cost:** £0 — your CPU/GPU.

**Where it shows up:** the LLM banner on the Compare page (model
name + health status), per-row rationale text with verifier
guard rails, the email digest's per-symbol prose. The sentiment
demotion rule (BUY → WAIT at -0.30, → AVOID at -0.45) all flows
from these scores.

**What breaks if it degrades:** sentiment column becomes null.
The bucket vote still works (it's price-driven) but the demotion
rule can't fire. Rationale falls back to a templated string. The
Compare page's LLM banner goes red so you know.

**Switching models:** \`export TRADEPRO_OLLAMA_MODEL=qwen3.5:latest\`
(or any model you've \`ollama pull\`'d). Sentiment is pinned to
\`llama3.1:8b\` because qwen returns empty without specific request
flags — the sentiment-pipeline pins the model per purpose.
        `,
      },
      {
        heading: "Quick reference — provider × signal layer",
        body: `
| Provider | Where in the engine |
|---|---|
| **Yahoo Finance** | OHLCV → SMA/RSI/52w/drawdown → market_state ∙ fundamentals → swing-valuation + passive-horizon ∙ historic earnings → swing-event layer ∙ analyst consensus → long-term horizon |
| **Finnhub** | Forward earnings calendar → digest warnings + position-into-earnings flag |
| **Trading 212** | Live positions → holdings panel + MCP tools + portfolio email section |
| **Ollama** | Headline sentiment → demotion rule ∙ rationale prose |

If any row's source goes red on the Health page, that whole
column of the engine is degraded. The bucket vote remains
functional from the others — but the user should see the
warning before acting on a verdict.
        `,
      },
      {
        heading: "What we'd add next (and why we haven't yet)",
        body: `
**SEC EDGAR** — completely free. 10-K, 10-Q, 8-K filings + raw
earnings transcripts. Would unlock historical-P/E vs own 5-year
average (the spec wants this; we currently use basket-relative as
a stand-in). Heavier work — needs a snapshot store. Parked.

**Insider trades** (yfinance \`Ticker.insider_transactions\`) —
free, 1 line of code to fetch, would feed a "smart money signal"
layer. Not yet integrated into any scorer. Tracked.

**Recommendation trends** (Finnhub \`/stock/recommendation\`) —
analyst opinion momentum over months, not just current target.
Free. Would supplement the long-term-horizon analyst-upside
score. Tracked.

**FRED** (St Louis Fed) — VIX, 10Y treasury, CPI, unemployment.
We already inject VIX/TNX into market context but pull from
yfinance instead of FRED's canonical source. Cleaner if it
becomes load-bearing. Not blocking.

**Polygon / Alpha Vantage** — paid, real-time / institutional
grade. Would only matter if the platform moves to intraday
verdicts. Phase 7+.
        `,
      },
    ],
  },
];

// ---------------------------------------------------------------------------
// TRADE SUPPORT topics
// ---------------------------------------------------------------------------

HELP_TOPICS.push(
  {
    slug: "compass-score",
    title: "COMPASS alpha score",
    summary: "What the 0–100 COMPASS score means, how each factor is weighted, and how to act on BUY / WATCH / HOLD / TRIM signals.",
    emoji: "🧭",
    sections: [
      {
        heading: "What is COMPASS?",
        body: `
**COMPASS** stands for *Continuous Multi-factor Alpha Scoring*. It
gives every stock or ETF a single number from 0 to 100 that
summarises how attractive the investment looks *right now* across
six independent evidence streams.

Think of it as a second opinion that runs alongside the existing
technical analysis. The technical rules on the Decide page tell
you *when* to enter (trend, RSI, range position). COMPASS tells
you *which names are worth entering* — the ones where multiple
different types of evidence are all pointing the same way at once.
        `,
      },
      {
        heading: "The six factors (and their weights)",
        body: `
| Factor | Weight | What it measures |
|---|---|---|
| **Momentum** | 20% | 12-week price trend + RSI positioning |
| **Earnings revision** | 20% | Are analysts raising or cutting their EPS estimates? |
| **Sector relative strength** | 15% | Is this stock beating or lagging its own sector ETF? |
| **Quality** | 15% | Return-on-equity, debt load, free-cash-flow |
| **Analyst consensus** | 15% | Direction of upgrades + price-target upside |
| **Sentiment** | 10% | Recent news tone |
| **Valuation** | 5% | Forward P/E vs sector |

Each factor produces a sub-score from 0 (very negative) to 10
(very positive). The weighted average of the six sub-scores
maps onto the 0–100 COMPASS number.
        `,
      },
      {
        heading: "BUY / WATCH / HOLD / TRIM signals",
        body: `
The score maps to a recommended action:

| Score | Signal | What it means |
|---|---|---|
| **72–100** | BUY | Strong multi-factor confluence. Most factors agree this is a good entry. |
| **55–71** | WATCH | More positive than negative but not yet a compelling entry. Add to your watchlist; wait for RSI pullback or confirmation. |
| **40–54** | HOLD | Mixed picture. If you already own it, no urgent reason to sell. If flat, no strong reason to buy. |
| **0–39** | TRIM | Multiple factors deteriorating. Existing positions deserve scrutiny; new money should look elsewhere. |

**Important:** COMPASS is not a trigger — it is a filter. A BUY
signal from COMPASS combined with a technical BUY on the Decide
page is the highest-conviction setup. COMPASS BUY with technical
WAIT means "good stock, bad moment — be patient."
        `,
      },
      {
        heading: "Conviction grades",
        body: `
Alongside the signal, COMPASS shows a conviction grade:

- **HIGH** (score ≥ 78) — the evidence is strong and consistent.
  Multiple factors in the top quartile. Size positions normally.
- **MEDIUM** (score 60–77) — a clear directional lean but some
  factors are mixed. Size positions at 70–80% of normal.
- **LOW** (score < 60) — marginal. If in doubt, reduce size or skip.

The conviction grade on the Compare row reflects the COMPASS
conviction, *not* the technical conviction from the strategy vote.
They can disagree — that disagreement is useful information.
        `,
      },
      {
        heading: "The macro gate",
        body: `
COMPASS respects the macro regime (see "Macro regime gate →").
When the macro gate is AMBER, COMPASS can still produce a BUY
signal but it is automatically downgraded to WATCH on the row.
When the gate is RED, the score is shown but flagged as
**macro-gated** — meaning "the alpha is there, but the market
environment says reduce risk right now."

This means you will never see a COMPASS BUY on the Decide page
during a RED-regime period, even if every individual factor is
maxed out.
        `,
      },
      {
        heading: "Where to find it on the app",
        body: `
The COMPASS score, signal, and conviction appear on every expanded
row on the Decide page — look for the **COMPASS** section below
the technical bucket. You can also read the full factor breakdown
there: each of the six factors shows its sub-score and a one-line
evidence note.

The score is also available via the MCP tools if you're using
Claude Desktop: \`get_compare()\` returns \`compass_score\` and
\`compass_breakdown\` on every row.
        `,
      },
    ],
  },

  {
    slug: "macro-regime",
    title: "Macro regime gate",
    summary: "How the GREEN / AMBER / RED traffic light works, what drives each level, and how it changes your position sizing.",
    emoji: "🚦",
    sections: [
      {
        heading: "Why a regime gate?",
        body: `
Individual stock analysis tells you whether *this name* is good.
Macro regime tells you whether *right now* is a safe time to put
money to work at all.

A stock can have a perfect COMPASS score of 95/100 and still lose
money if a VIX spike or credit market seizure is under way. The
macro regime gate is a top-down circuit breaker: if the broad
market environment says "reduce risk", the gate shrinks or stops
all new entries regardless of how good the individual alpha looks.
        `,
      },
      {
        heading: "The three levels",
        body: `
| Level | Colour | What it means | Position sizing |
|---|---|---|---|
| 1 | 🟢 GREEN | Normal environment. VIX ≤ 22, credit spread healthy, rates not spiking. | Full size (1.0×) |
| 2 | 🟡 AMBER | Elevated stress. One of the three signals is flashing. Reduce but don't stop. | Reduced (0.6×) |
| 3 | 🔴 RED | Severe stress. Multiple signals in danger zone simultaneously. | Stop new entries (0.0×) |

"Position sizing" means the multiplier applied to whatever your
normal risk-per-trade is. If you normally risk £100 per trade and
the regime is AMBER, you risk £60. RED means you don't open new
positions until the gate clears.
        `,
      },
      {
        heading: "What drives the gate",
        body: `
Three signals are checked each trading day:

**VIX (the "fear gauge"):** the market's implied expectation of
future volatility in the S&P 500. VIX ≥ 22 triggers AMBER.
VIX ≥ 32 triggers RED. In quiet markets VIX is 12–18; during
2020 COVID it hit 82; 2022 rate shock peaked around 36.

**HYG credit spread:** high-yield bond ETF drawdown from its
52-week peak. When junk bonds sell off it means credit markets
are stressed — companies with debt are seen as riskier. Drawdown
≥ 4% triggers AMBER; ≥ 8% triggers RED.

**10-year Treasury yield trend:** if the 10Y has risen more than
0.40 percentage points over the last 60 days, AMBER. Rising rates
compress equity valuations, especially growth stocks. This check
alone can never trigger RED — it needs VIX or HYG to confirm.
        `,
      },
      {
        heading: "Where to see it on the app",
        body: `
The macro context bar at the top of the Decide page shows the
current regime in text: "VIX 16.7 (normal) · 10Y 4.56% (rising)
· risk_mode=GREEN". The same info appears in the daily email
digest's macro section.

When the gate is AMBER or RED, a banner appears on the Decide
page explaining which signals triggered it and what it means for
your entries. COMPASS signals are silently downgraded; the
banner explains why a COMPASS BUY may show as WATCH.
        `,
      },
    ],
  },

  {
    slug: "sector-rs",
    title: "Sector relative strength",
    summary: "What 12-week relative strength vs the sector ETF measures, and why a stock leading its peers is a stronger buy candidate.",
    emoji: "📡",
    sections: [
      {
        heading: "Why compare to the sector, not the market?",
        body: `
If NVDA is up 25% over 12 weeks, is that good? It depends. If the
whole semiconductor sector (SOXX) is up 30% over the same period,
NVDA is actually *underperforming its own peer group* by 5%. The
market is choosing other semis over NVDA. Something is wrong
specifically with this name — not with semis in general.

Conversely, if NVDA is up 25% and SOXX is up only 10%, NVDA is
outperforming its sector by +15 percentage points. The market is
*specifically choosing NVDA* over the rest of the group. That's a
signal that institutional money is flowing into this name.
        `,
      },
      {
        heading: "How the 12-week RS number is calculated",
        body: `
1. Fetch the closing price from 12 weeks ago (60 trading days).
2. Calculate: \`symbol_return = (price_now / price_12w_ago - 1) × 100\`.
3. Do the same for the sector ETF.
4. \`RS = symbol_return - etf_return\` (percentage points).

A positive RS means the stock is outperforming its sector.
Negative means it's lagging. Zero means it's moving exactly with
the sector (no alpha).
        `,
      },
      {
        heading: "Score mapping",
        body: `
The raw percentage-point RS is mapped to a 0–10 sub-score for
COMPASS:

| RS (pp) | Score | Interpretation |
|---|---|---|
| +15 or more | 10 | Market leader — strong buy candidate |
| +8 to +14 | 9 | Clear outperformer |
| +4 to +7 | 7 | Mild outperformer |
| +1 to +3 | 6 | Slight edge |
| -1 to +1 | 5 | Inline — neutral |
| -1 to -4 | 4 | Mild laggard |
| -4 to -8 | 3 | Underperformer |
| -8 to -15 | 2 | Weak — investigate why |
| -15 or less | 1 | Sector is leaving this name behind |
        `,
      },
      {
        heading: "Sector ETF mapping",
        body: `
For 40+ common names, TradePro uses a curated sector ETF:

| Symbol(s) | Sector ETF | Why |
|---|---|---|
| NVDA, MU, ASML, AMD, INTC | SOXX | Philadelphia Semiconductor Index |
| AAPL, MSFT, PLTR, CRM | XLK | Technology Select Sector |
| META, NFLX, GOOGL | XLC | Communication Services |
| JPM, BAC, GS, V | XLF | Financials |
| HSBA.L, AZN.L, SHEL.L | EWU | iShares MSCI United Kingdom |
| VUKE.L, VUSA.L, VWRL.L | SPY | Used as a global proxy for broad ETFs |

For symbols not in the curated list, TradePro looks up the sector
from Yahoo Finance and maps to the appropriate ETF. If that also
fails, it falls back to SPY (the S&P 500) as a broad market proxy.
The Decide row shows which ETF was used so you can spot a fallback.
        `,
      },
    ],
  },

  {
    slug: "eps-revision",
    title: "EPS revision tracker",
    summary: "Why analyst earnings estimate changes are a leading alpha factor, and how TradePro tracks the 90-day revision for every stock.",
    emoji: "📈",
    sections: [
      {
        heading: "Why earnings estimates matter more than earnings",
        body: `
A company beating earnings expectations is big news — but only if
the expectations haven't already been revised up to capture that
beat. What actually moves stock prices is **whether estimates are
rising or falling over time**.

If analysts were expecting Micron to earn $15/share next year and
over 90 days they've collectively revised that up to $19/share,
something has fundamentally improved in the business: maybe memory
demand is stronger, maybe margins are better, maybe guidance was
raised. Price follows earnings estimates with a lag.

This is why EPS revision is one of the strongest and most
consistent alpha factors in quant finance — not whether earnings
are high, but whether they're getting *higher*.
        `,
      },
      {
        heading: "How the tracker works",
        body: `
Every Sunday evening (before Monday's market opens), a scheduled
job records the current **forward EPS** (analysts' consensus
next-12-months earnings estimate) for every stock in TradePro's
watchlists. The data comes from Yahoo Finance's \`forwardEps\`
field — the same consensus estimate you'd see on a broker's
research page, updated within ~24 hours of any analyst change.

After about 90 days of snapshots you can see the direction of
travel:

- **\`direction: up\`** — estimates have risen. Analysts are raising
  their forecasts. Strong positive signal.
- **\`direction: down\`** — estimates are being cut. Weak signal;
  investigate before buying.
- **\`direction: flat\`** — no meaningful change (< £0.01 delta).
  Neutral.
- **\`direction: insufficient_data\`** — fewer than 30 days of
  snapshots, or ETF with no analyst coverage.
        `,
      },
      {
        heading: "The 90-day revision percentage",
        body: `
The tracker computes \`revision_pct = (current_estimate / estimate_90d_ago - 1) × 100\`.

Example for Micron (MU):
- 90 days ago: forward EPS = £15.10
- Today: forward EPS = £19.88
- Revision = +31.6% ← strong positive revision

This feeds COMPASS's earnings revision factor (20% weight). The
factor score maps revision_pct to 0–10 — large positive revisions
score near 10, large cuts score near 0.
        `,
      },
      {
        heading: "ETFs and no-coverage symbols",
        body: `
ETFs don't have analyst EPS coverage (they track an index, not
a company). The tracker simply skips them — no error, no warning.
The COMPASS earnings revision factor for ETFs defaults to 5
(neutral) so the score isn't penalised.

For individual stocks with thin analyst coverage (micro-caps,
foreign-listed names), Yahoo Finance may not have a \`forwardEps\`
value. These are also skipped silently. Build up a few months of
weekly snapshots before relying on the revision factor for thinly
covered names.
        `,
      },
      {
        heading: "How to trigger the weekly snapshot manually",
        body: `
The snapshot runs automatically every Sunday via launchd. To run
it manually (e.g. after adding new symbols to a watchlist):

\`\`\`bash
# Run for a specific watchlist
uv run tradepro-refresh --watchlist us_semis --eps-snapshot

# Run for multiple watchlists
uv run tradepro-refresh --watchlist us_megacap_sample --eps-snapshot
uv run tradepro-refresh --watchlist us_sp100_sample --eps-snapshot

# Trigger via launchd (no terminal window needed)
launchctl start com.tradepro.eps-snapshot
\`\`\`

Snapshots are stored at \`~/.tradepro/eps_snapshots/<SYMBOL>.json\`.
Each file is a JSON array of \`{date, forward_eps}\` entries, capped
at 104 entries (~2 years of weekly history).
        `,
      },
    ],
  },

  {
    slug: "signal-ledger",
    title: "Signal ledger & model performance",
    summary: "How TradePro logs every signal permanently, and how to use hit rate and expectancy to judge whether the models are actually working.",
    emoji: "📋",
    sections: [
      {
        heading: "Why log every signal?",
        body: `
Every model that produces signals can be made to look good by only
talking about its wins. TradePro's signal ledger prevents this by
logging **every single signal the moment it fires** — win or lose,
before the outcome is known.

The ledger is append-only: nothing is ever deleted. Once a signal
is written, it is part of the permanent evidence record.

This means: after 3 months of signals, you can run
\`ledger.compute_stats()\` and get the **actual, unbiased hit rate**
of COMPASS vs reality. No cherry-picking. If the hit rate is 35%,
you know the model needs work. If it's 65%+, you have evidence that
the alpha is real.
        `,
      },
      {
        heading: "Signal lifecycle",
        body: `
Every signal goes through three states:

1. **OPEN** — signal fired, waiting for the entry zone to be
   reached or for the expiry date to pass.
2. **ACTIVE** — price touched the entry level; position is live.
3. **CLOSED** — one of four outcomes recorded:
   - \`HIT_TARGET\` — price reached the target ✅
   - \`STOPPED_OUT\` — price hit the stop loss ❌
   - \`EXPIRED\` — position didn't trigger before the expiry date
   - \`MANUAL_CLOSE\` — closed by the operator for any other reason

For each closed signal the ledger also records: exit price,
return_pct, and holding_days.
        `,
      },
      {
        heading: "Hit rate and expectancy",
        body: `
**Hit rate** is simply the percentage of closed signals that hit
their target. A hit rate of 60% means 6 out of 10 signals worked.

But hit rate alone is misleading. A model with 80% hit rate but
tiny wins and huge losses can still lose money overall. That's why
expectancy matters:

\`expectancy = (hit_rate × avg_winner%) + ((1 - hit_rate) × avg_loser%)\`

A positive expectancy means the model makes money in the long run.
A negative expectancy means it loses, regardless of hit rate.

Example:
- Hit rate 60%, avg win +3.5%, avg loss -2.0%
- Expectancy = 0.60 × 3.5 + 0.40 × (-2.0) = **+1.3%** per signal

That's a good model. Run it 50 times a year and you're up ~65% on
your risk capital purely from model edge — before any compounding.
        `,
      },
      {
        heading: "Where the ledger lives",
        body: `
The ledger is stored at \`~/.tradepro/signal_ledger.jsonl\` — one
JSON object per line, one line per signal event. It survives Mac
reboots; it never gets wiped by a redeploy. It's a plain text file
you can open in any editor.

The ledger is **not yet surfaced on a dedicated page in the UI** —
that's on the roadmap (Phase B: portfolio simulation). For now,
you can query it with a Python one-liner:

\`\`\`python
from tradepro_strategies.signal_ledger import SignalLedger
ledger = SignalLedger()
stats = ledger.compute_stats(source="COMPASS")
print(stats)
# → {"hit_rate_pct": 63.2, "expectancy_pct": 1.45, "total_closed": 44, ...}
\`\`\`

Or filter by symbol:
\`\`\`python
stats = ledger.compute_stats(source="COMPASS", symbol="NVDA")
\`\`\`

Or filter to recent 30 days:
\`\`\`python
stats = ledger.compute_stats(source="COMPASS", lookback_days=30)
\`\`\`
        `,
      },
      {
        heading: "Building the evidence base",
        body: `
The ledger is most useful after **at least 30 closed signals**.
With fewer than that, hit rate estimates are too noisy to act on
(a coin flip can score 70% hit rate over 10 flips).

Build the ledger by running signals consistently and resisting the
temptation to intervene manually in winning trades before the
target is reached (that inflates hit rate artificially). Let the
model run for a full market cycle: a bull phase, a choppy phase,
and ideally a mini-drawdown.

After ~3 months of COMPASS signals you'll have enough data to:
- Tune the entry score threshold (currently 72 — raise if too many WATCH signals flop)
- Tune the expiry window (currently 5 days for CATALYST, open for COMPASS)
- Compare COMPASS performance sector by sector (does it work better in semis than energy?)
        `,
      },
    ],
  },

// ---------------------------------------------------------------------------
// IT / OPS topics
// ---------------------------------------------------------------------------

  {
    slug: "scheduling",
    title: "Scheduling & automation",
    summary: "How the Mac launchd jobs are set up, the three key schedules, how to pause/resume, and how to add the weekly EPS snapshot.",
    emoji: "⏱️",
    sections: [
      {
        heading: "Overview — what runs automatically",
        body: `
TradePro uses macOS **launchd** (the Mac equivalent of cron) to
run background jobs. All jobs live in \`~/Library/LaunchAgents/\` as
\`.plist\` files. They survive Mac reboots and missed fires are
replayed when the Mac wakes up.

Five jobs make up the full automation stack:

| Job | Cadence | What it does |
|---|---|---|
| \`com.tradepro.worker\` | Every 30 min (persistent) | Runs compare + heartbeat |
| \`com.tradepro.refresh\` | 4× daily at fixed UTC times | Alternative to worker for cron fans |
| \`com.tradepro.email-digest\` | Daily 23:00 UTC | Sends the daily signal email |
| \`com.tradepro.paper\` | Daily 14:30 UTC | Paper trading session |
| \`com.tradepro.eps-snapshot\` | Sunday 20:00 UTC | Records weekly EPS estimates |
| \`com.tradepro.intraday-engine\` | Continuous (KeepAlive) | Intraday paper strategy engine |
        `,
      },
      {
        heading: "Worker mode vs cron mode",
        body: `
**Worker mode** (default, recommended):
- One persistent job (\`com.tradepro.worker\`) runs compare every
  30 minutes and sends heartbeats every 5 minutes.
- The Mac UI always shows the worker as "alive".
- Better for active traders who check the app during the day.

**Cron (refresh+heartbeat) mode:**
- \`com.tradepro.refresh\` fires at 07:00, 12:00, 17:00, 22:30 UTC.
- \`com.tradepro.heartbeat\` pings every 15 minutes.
- Better if your Mac sleeps a lot or you don't need fresh data
  between the four fixed fire times.

Switch modes:
\`\`\`bash
# Switch to worker mode
bash strategies/scripts/install-launchd.sh --worker

# Switch to cron mode
bash strategies/scripts/install-launchd.sh --refresh
\`\`\`
        `,
      },
      {
        heading: "Adding the weekly EPS snapshot",
        body: `
The EPS snapshot job is NOT installed by default (it's a new
feature). Add it alongside whichever main mode you use:

\`\`\`bash
# Worker mode + EPS snapshot
bash strategies/scripts/install-launchd.sh --eps-snapshot

# Cron mode + EPS snapshot
bash strategies/scripts/install-launchd.sh --refresh --eps-snapshot

# Full stack: worker + intraday + EPS snapshot
bash strategies/scripts/install-launchd.sh --intraday --eps-snapshot
\`\`\`

The job fires every Sunday at 20:00 UTC. It runs
\`tradepro-refresh --eps-snapshot\` on all stock watchlists
(\`us_semis\`, \`us_megacap_sample\`, \`us_sp100_sample\`, etc.).

To trigger it manually any time:
\`\`\`bash
launchctl start com.tradepro.eps-snapshot
\`\`\`
        `,
      },
      {
        heading: "Pausing and resuming without unloading",
        body: `
You can pause the worker or intraday engine without unloading the
launchd job (which would require re-running the installer):

\`\`\`bash
# Pause the worker (next cycle sees the file and skips)
touch ~/.tradepro/worker.pause

# Resume
rm ~/.tradepro/worker.pause

# Pause the intraday engine
touch ~/.tradepro/intraday-engine.pause

# Resume
rm ~/.tradepro/intraday-engine.pause
\`\`\`

The email digest and EPS snapshot don't have a pause file — they
fire once per day/week and finish in under 5 minutes, so pausing
via \`launchctl bootout\` is fine for those.
        `,
      },
      {
        heading: "Checking what ran and when",
        body: `
All jobs log to \`~/.tradepro/logs/\`. Key files:

\`\`\`bash
# Today's compare run log
tail -100 ~/.tradepro/logs/worker-$(date +%Y-%m-%d).log

# Last Sunday's EPS snapshot
tail -100 ~/.tradepro/logs/eps-snapshot-$(date -u +%Y-%m-%d).log

# Email digest
tail -50 ~/.tradepro/logs/email-$(date +%Y-%m-%d).log

# Paper session
tail -50 ~/.tradepro/logs/paper-$(date +%Y-%m-%d).log
\`\`\`

The Health page in the app also shows the Mac worker's last
heartbeat time so you can confirm the worker is alive without
opening a terminal.
        `,
      },
    ],
  },

  {
    slug: "ops-runbook",
    title: "IT operations runbook",
    summary: "Quick-reference checklist for IT operators: health checks, log locations, manual refresh, restarting jobs, and common fixes.",
    emoji: "🔧",
    sections: [
      {
        heading: "First 60-second health check",
        body: `
Open the **Health page** (\`/health\`) in the app. It shows:

1. **API status** — is the .NET API reachable?
2. **Worker liveness** — when did the Mac last ping? Green = within
   30 min, amber = 30 min–2 h, red = stale > 2 h.
3. **Compare cache freshness** — is there data less than 24 h old?
4. **External integrations** — Yahoo Finance, Finnhub, T212, Ollama.

If all four are green you're done. If anything is amber or red,
read on.
        `,
      },
      {
        heading: "Worker is showing as stale",
        body: `
**Check if the launchd job is loaded:**
\`\`\`bash
launchctl list | grep tradepro
\`\`\`
You should see \`com.tradepro.worker\` (or \`com.tradepro.refresh\`
and \`com.tradepro.heartbeat\` in cron mode).

**Check the log for errors:**
\`\`\`bash
tail -50 ~/.tradepro/logs/worker-$(date +%Y-%m-%d).log
# or for the heartbeat specifically:
tail -20 ~/.tradepro/logs/worker-heartbeat-$(date +%Y-%m-%d).log
\`\`\`

**Restart the worker:**
\`\`\`bash
launchctl bootout "gui/$UID" \
  ~/Library/LaunchAgents/com.tradepro.worker.plist
launchctl bootstrap "gui/$UID" \
  ~/Library/LaunchAgents/com.tradepro.worker.plist
\`\`\`

**If the job is not loaded at all**, re-run the installer:
\`\`\`bash
bash strategies/scripts/install-launchd.sh
\`\`\`
        `,
      },
      {
        heading: "Manual price refresh",
        body: `
Trigger a fresh compare run right now without waiting for the
next scheduled fire:

\`\`\`bash
# Trigger via launchd (no terminal window, no logs to watch)
launchctl start com.tradepro.worker   # one extra cycle

# OR run directly in terminal (you see output in real time)
cd ~/sourcecode/tradepro/tradepro/strategies
uv run tradepro-compare --universe etf_uk_core --push
uv run tradepro-compare --universe us_semis --push
\`\`\`

For all universes at once:
\`\`\`bash
bash strategies/scripts/refresh.sh
\`\`\`
        `,
      },
      {
        heading: "Checking the EPS snapshot history",
        body: `
Check which symbols have snapshot data and how many weeks back:

\`\`\`bash
ls ~/.tradepro/eps_snapshots/ | wc -l   # total symbols

# Check a specific symbol
cat ~/.tradepro/eps_snapshots/NVDA.json | python3 -c \
  "import json,sys; d=json.load(sys.stdin); print(f'{len(d)} snapshots, latest {d[-1][\"date\"]}')"
\`\`\`

Run a manual Python check:
\`\`\`python
from tradepro_strategies.eps_tracker import get_eps_revision
print(get_eps_revision("NVDA"))
# → {"direction": "up", "revision_pct": 12.4, "delta_90d": 2.1, ...}
\`\`\`
        `,
      },
      {
        heading: "Common errors and fixes",
        body: `
**"Yahoo Finance quota exceeded" errors in the log:**
Rate limit hit. Wait 10–15 minutes and run again. The refresh script
is idempotent — already-cached symbols won't be re-fetched.

**"uv not found" error:**
Install uv: \`curl -LsSf https://astral.sh/uv/install.sh | sh\`
Then restart the terminal (or run \`source ~/.bashrc\`).

**API returns empty / stale data on the Decide page:**
1. Check the worker is running (see above).
2. Check the compare cache: \`ls -lh ~/.tradepro/cache/compare/\`
3. Push the latest cache manually: \`uv run tradepro-push\`

**Ollama / LLM rationale missing:**
Ollama may not be running. Start it: \`ollama serve\`
Check it's accessible: \`curl http://localhost:11434/api/tags\`
The engine degrades gracefully — rows will show a template
rationale rather than an LLM one; buckets are unaffected.

**Trading 212 badge shows "demo" but you want live:**
Update the T212 config in Settings → put the live API key in AWS
Secrets Manager at \`tradepro/all\` (key: \`Trading212__ApiKey\`) and
redeploy the API on EC2.
        `,
      },
      {
        heading: "Full reinstall (nuclear option)",
        body: `
If jobs are behaving strangely, a clean reinstall takes under
2 minutes:

\`\`\`bash
# 1. Unload all jobs
for plist in ~/Library/LaunchAgents/com.tradepro.*.plist; do
  launchctl bootout "gui/$UID" "$plist" 2>/dev/null || true
  rm -f "$plist"
done

# 2. Reinstall with your preferred flags
cd ~/sourcecode/tradepro/tradepro/strategies
bash scripts/install-launchd.sh --intraday --eps-snapshot

# 3. Verify
launchctl list | grep tradepro
\`\`\`

This is safe — it only touches launchd job registrations, not
your cached data or credentials.
        `,
      },
    ],
  }
);

// ---------------------------------------------------------------------------

export function topicBySlug(slug: string): HelpTopic | undefined {
  return HELP_TOPICS.find((t) => t.slug === slug);
}

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

export interface HelpSection {
  heading: string;
  body: string; // markdown
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
signal. The reverse is a **death cross** — sell.
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
        body: `
A number between 0 and 100 measuring how much recent gains
outweigh recent losses.

- **RSI > 70** = overbought. Price has run hard recently; a
  pullback is statistically more likely than another big jump.
- **RSI < 30** = oversold. Price has dropped hard; a bounce is
  statistically more likely.
- **RSI 30-70** = neutral. No edge in either direction.

A common pitfall: in a strong trend, RSI can stay above 70 (or
below 30) for weeks. Don't sell purely because RSI is high — use
it alongside a trend check.
        `,
      },
      {
        heading: "MACD — Moving Average Convergence Divergence",
        body: `
Two EMAs (fast 12-day and slow 26-day) subtracted from each other,
then a 9-day EMA of that line called the **signal line**.

When the MACD line crosses **above** the signal line, momentum is
turning up — buy. When it crosses below, sell. The further the
two lines drift apart, the stronger the momentum.

MACD is good at catching the start of trends; it's bad in flat,
choppy markets where it whipsaws between buy and sell.
        `,
      },
      {
        heading: "Donchian channel — breakout detection",
        body: `
Track the highest high and lowest low of the last N days
(commonly 20). When today's close exceeds the prior 20-day high,
**breakout — buy**. When it drops below the prior 20-day low,
**breakdown — sell**.

This is the rule turtles famously used to make millions. Works
brilliantly when assets are trending; kills you in sideways
markets.
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
        body: `
Strategy A returns 12% with 30% volatility. Strategy B returns
8% with 10% volatility. Which is better?

Sharpe ratio answers that. Roughly: \`return / volatility\`
(annualised, with the risk-free rate subtracted out). Higher is
better.

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
        heading: "Why we run all five",
        body: `
No single rule set works in every market. Trend strategies
underperform in choppy markets; mean-reversion underperforms in
trending markets. By running all five and looking at the
**consensus** ("how many of these strategies are currently
long?"), we get a more robust read than any single one would
give us.

When 4 out of 5 strategies — built on different philosophies —
all say long, that's a much stronger signal than any one of them
saying long in isolation.
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
prices moved, not usually a trigger for action. A future Phase
of the system will run an LLM over news headlines to score
sentiment and demote BUYs when the mood is sharply negative —
that's coming, but is informational, not decisive.
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
\`llama3.1:8b\` via Ollama on your Mac) over each news headline
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
sentiment scoring. To swap models, set an env var on the Mac:

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
- Show system health + Mac liveness
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
    summary: "The push pipeline, the Mac → API → frontend flow, and the full architecture doc.",
    emoji: "🔧",
    sections: [
      {
        heading: "Where the compute happens",
        body: `
The Mac runs the heavy work. Yahoo Finance prices are fetched,
five strategies are backtested over multi-year history, regime
overlaps are sliced, market-state verdicts are computed,
analyst-consensus snapshots are pulled — all locally, in
Python, using your machine's CPU.

The result is a single JSON payload. The Mac POSTs it to the
API (auth: a static bearer token set in App Service config).
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
A \`launchd\` job on the Mac fires every day at 22:30 UTC (after
the US close) and re-runs the comparator across every ETF
universe. Results push to the API automatically. You should
never have to think about it — the page is always fresh.

Install on a new Mac:

\`\`\`
bash strategies/scripts/install-launchd.sh
\`\`\`

Logs land at \`~/.tradepro/logs/refresh-<date>.log\`.
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
];

export function topicBySlug(slug: string): HelpTopic | undefined {
  return HELP_TOPICS.find((t) => t.slug === slug);
}

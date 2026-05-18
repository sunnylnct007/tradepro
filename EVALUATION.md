# Evaluating strategies

This is the companion to [STRATEGIES.md](STRATEGIES.md). That doc
explains *what* each strategy does and *when* it suits a given
instrument. This doc explains *how to tell which strategy is
actually doing the job well* on any given symbol — the quantitative
framework, what's already instrumented, and what's still manual.

Read this when:

- You're looking at a Decide page verdict and want to know *which
  strategy* is driving the top-candidate recommendation, and
  whether that strategy is the right voice to be listening to.
- A consensus disagrees with your intuition and you want to debug
  which contributing strategy is the source of the dissent.
- You're considering excluding a strategy from the consensus for a
  particular instrument class (e.g. dropping RSI mean-reversion
  from momentum-factor ETFs).
- You're evaluating whether a newly-added strategy is earning its
  place in the stack or just adding noise.

## The five lenses

Every evaluation question can be framed through one of these five
lenses. Each tells you something different; together they paint a
complete picture.

### Lens 1 — Backtest performance vs buy-and-hold

> **The question**: does this strategy beat the null model?
>
> **What it tells you**: whether the strategy is creating value at
> all on this symbol.

For each (symbol, strategy) pair, the comparator runs a backtest
over the historical bar window and reports:

- **Sharpe ratio**: risk-adjusted return. Above 1.0 is good; above
  1.5 is rare; above 2.0 should make you suspicious of overfit.
- **CAGR**: compound annual growth rate of the equity curve.
  Useful for comparing across symbols with different absolute
  return profiles.
- **Max drawdown**: worst peak-to-trough loss. A strategy with
  Sharpe 1.2 and 8% max DD is genuinely tradeable; a strategy
  with Sharpe 1.2 and 45% max DD is theoretically optimal but
  psychologically unholdable.
- **Win rate**: % of closed trades that were profitable. Less
  important than total return — a 35% win rate with 4:1 R/R is
  better than 60% win rate with 1:1 — but useful for sanity-checking.

**The buy-and-hold baseline** is on every backtest report. If the
strategy's Sharpe doesn't materially exceed buy-and-hold's Sharpe,
the strategy is destroying value (you took risk and got the same
return as just owning the asset). The Decide page already demotes
top-candidate ranking when the chosen strategy can't beat B&H.

#### What's instrumented today

The Compare page's per-row stats. Each row in the universe
comparator already has Sharpe / CAGR / max DD / win rate, both for
the strategy and for buy-and-hold. The Backtest (Signals) page also
shows the same per (symbol, strategy) pair when you drill in.

#### What's not yet instrumented

A **leaderboard view** — "for symbol X, here are all 7 strategies
ranked by Sharpe." Today you have to pivot mentally across rows in
the comparator output. A small Decide-page widget that says
**"Best strategy on AVGO: SMA crossover (Sharpe 1.42, beats B&H by
0.31)"** would close this gap. The data exists; it's purely a
surface-area task.

### Lens 2 — Performance by regime

> **The question**: does the strategy work in *all* market conditions
> or only some?
>
> **What it tells you**: when to trust the strategy's vote vs. when
> to discount it given today's regime.

A strategy with overall Sharpe 1.2 might have:

- Sharpe 1.8 in **bull** regimes
- Sharpe 0.6 in **chop** regimes
- Sharpe -0.4 in **bear** regimes

That overall 1.2 is a **lie of averages** — the strategy is
genuinely good in bulls, useless in chops, and a money-loser in
bears. Its consensus vote during a chop regime should count for
something like a third of its vote during a bull regime.

#### What's instrumented today

`backend/TradePro.Api/Simulation/RegimeStats.cs` computes regime-
split performance for every backtest. The Compare row's `regimes`
field already carries the per-regime stats — they're surfaced in
the Decide page's `decision_trace` and the email digest's per-
strategy detail.

#### What's not yet instrumented

**Regime-weighted consensus**. The consensus engine in
`compare.py:_attach_bucket_and_rationale` treats every strategy
vote as equally weighted regardless of whether today's regime is
the strategy's best or worst. A strategy that's bull-regime-only
should not get an equal vote during a bear; today it does.

The fix lives in two places:
1. Detect today's regime (already done in `market_state.py`).
2. Weight each strategy's vote by its Sharpe in that regime,
   normalised across strategies. Strategies with negative regime
   Sharpe get a vote weight of 0.

### Lens 3 — Hit rate on fresh signals

> **The question**: when this strategy says BUY today, what
> historically happens next?
>
> **What it tells you**: forward-looking edge specific to *this
> strategy + this symbol*.

Sharpe is a backwards-looking equity-curve statistic. Hit rate is a
forwards-looking accuracy statistic. They measure different things:

- A strategy with Sharpe 1.2 made money over the historical
  window. That's good but doesn't guarantee tomorrow's signal is
  useful.
- A strategy with 67% BUY-signal hit rate over the past 5 years
  says "two out of three times this strategy fired BUY, the
  symbol was higher 20 bars later." That's a forward-edge
  statement.

`IHitRateEngine` in the backend computes this on demand:

```
POST /api/signals/hitrate
{
  "symbol": "AAPL",
  "strategy": "donchian_breakout",
  "lookforward_bars": 20
}
→
{
  "signal_count": 47,
  "buy_hit_rate": 0.681,
  "sell_hit_rate": 0.594,
  "buy_avg_forward_return_pct": 4.2,
  "sell_avg_forward_return_pct": -2.1
}
```

#### What's instrumented today

The endpoint exists and is called from the Backtest page's "Hit
rate" panel. It's accurate but **not surfaced prominently** — you
have to click into a specific (symbol, strategy) pair to see it.

#### What's not yet instrumented

**Per-strategy hit-rate column in the comparator**. Adding it to
every row in the compare output would mean every Decide-page
recommendation comes with "this strategy has a 68% BUY hit rate on
this symbol" alongside the Sharpe number. The hit rate is more
honest than the Sharpe for "should I trust this signal" — Sharpe
tells you the strategy *did well in the past*, hit rate tells you
*the next signal is statistically likely to work*.

### Lens 4 — Independence in the consensus

> **The question**: when this strategy disagrees with the rest of
> the stack, is it right or wrong on average?
>
> **What it tells you**: whether dissent from this strategy carries
> information or noise.

A multi-strategy consensus is only useful if the strategies are
genuinely independent voters. If all 7 say BUY together every time,
they're one strategy with 7 names. If they disagree often *and the
dissenter is often right*, the consensus is doing real work.

The metric to track: for each strategy S and each (symbol, date)
where S's vote differed from the majority, did S's vote turn out
to be correct (defined as: directionally right over the next
20 bars)?

A strategy with a **dissent-accuracy of 60%+** is a load-bearing
member of the stack — its dissent is information. A strategy whose
dissent accuracy is at coin-flip (45-55%) is adding noise rather
than signal; its dissent should be ignored or it should be
discounted in the vote-counting step.

#### What's instrumented today

Nothing direct. The raw data exists (per-strategy vote on every
(symbol, date), forward returns) but no metric rolls these up.

#### What's not yet instrumented

A **dissent-accuracy report** per strategy, rebuilt monthly. Would
live as a new endpoint `/api/signals/dissent-accuracy` and surface
on a "Strategy health" page. Would inform the regime-weighted
consensus from Lens 2 — dissent accuracy is a more granular weight
than regime Sharpe.

### Lens 5 — Live-vs-backtest drift

> **The question**: is the paper-trading P&L tracking what the
> backtest predicted?
>
> **What it tells you**: whether the strategy is overfit or has
> structurally changed since the backtest window.

A backtest gives you a hypothetical Sharpe. Paper-trading gives
you a real Sharpe under real slippage, real commissions, real fill
timing. The gap between them is **drift** — and it's the single
most predictive indicator of whether a strategy will keep working.

A 40-50% drift (live Sharpe is 0.6 while backtest said 1.2) is
acceptable; the gap is typical of slippage + execution friction.

A 70%+ drift (live Sharpe 0.3, backtest 1.2) is a red flag. Either
the strategy was overfit to its training window, or the regime has
changed in a way the backtest doesn't capture. Either way, demote
its consensus weight or pull it from production.

#### What's instrumented today

The paper-trading engine produces live P&L per (strategy_id,
symbol). The Paper page Live tab surfaces the per-strategy fill log
and running equity curve. The backtest reports are pushed to the
API separately.

#### What's not yet instrumented

The **comparison**. There's no view that says "Backtest predicted
Sharpe 1.2; live Sharpe (last 30 days) is 0.4; drift is severe."
This is a new Paper-page or Settings-page panel that joins the two
datasets per (strategy_id, symbol). Once the DB migration happens
(see [ROADMAP.md](ROADMAP.md)) this becomes a single SQL query.

---

## A worked example: AVGO

Let's run through the AVGO MIXED verdict the user flagged:

```
AVGO · 1 BUY · 0 SELL · 6 HOLD
4 of 7 strategies currently long (position state — not today's trade)
```

Or, in the new split-HOLD vocabulary:

```
AVGO · 1 BUY · 0 SELL · 3 HOLD-IN · 3 HOLD-OUT
4 of 7 currently long (= 1 BUY + 3 HOLD-IN)
```

Decide-page top candidate: `buy_and_hold (sharpe 1.12)` with rationale
"above 200-day SMA but at 93rd percentile of 52w range — near the
highs, asymmetric risk/reward for a fresh entry. Wait for a
pullback."

### Lens 1: backtest performance

Buy-and-hold's Sharpe is 1.12. That's the headline number on the
top-candidate. For the verdict to be trustworthy, we'd want to see
*which strategies in the stack also have Sharpe ≥ 1.0 on AVGO*,
because those are the ones whose vote we should weight most.

Today this requires manually clicking into the Compare page rows
for each of the 7 strategies on AVGO and comparing. The
**leaderboard view** described in Lens 1 would surface this in one
line.

### Lens 2: regime fit

AVGO's current regime (let's say "bull, late-cycle") shows up in
the decision-trace. The strategies whose backtest Sharpe in
late-cycle bull regimes is highest (Donchian breakout, SMA
crossover historically) should get the most weight here. RSI mean
reversion's regime profile is *bad* in late-cycle bull (it fires
SELL repeatedly into a continuing uptrend) — its HOLD vote is
probably load-bearing here ("not screaming sell yet").

### Lens 3: hit rate

If we ran a hit-rate query for each of the 7 strategies on AVGO
over the past 5 years and 20-bar lookforward, the results would
look something like:

| Strategy | BUY hit rate | SELL hit rate |
|---|---|---|
| buy_and_hold | 71% | n/a |
| donchian_breakout | 64% | 52% |
| sma_crossover | 61% | 55% |
| macd_signal_cross | 58% | 48% |
| ichimoku_cloud | 56% | 51% |
| bollinger_bounce | 49% | 53% |
| rsi_mean_reversion | 41% | 60% |

(Illustrative — real numbers depend on the data window.)

The RSI mean-reversion row jumps out: 41% BUY hit rate on AVGO
means its BUY signals are *worse than coin-flip*. This is the
quantitative version of the MTUM/RSI-MR fit problem — AVGO is a
trending semiconductor name, mean-reversion logic doesn't suit it,
and the hit-rate data says so even without knowing the philosophy.
Its consensus vote should be down-weighted, or it should be
excluded altogether on this symbol class.

### Lens 4: independence

When RSI MR dissents on AVGO (i.e. votes SELL when others say
HOLD/BUY), what happens next 20 bars? If its dissent-accuracy is
under 50%, the dissent is noise and we should ignore it. If it's
over 60%, RSI MR is catching something the trend strategies miss
and we should listen.

This is the lens that **distinguishes "strategy is wrong for
this instrument" from "strategy is a useful contrarian voice."**
Today, untested.

### Lens 5: live drift

If AVGO is in our paper-trading universe and one of the 7
strategies has been running live, we'd compare the live Sharpe to
the backtest Sharpe. Severe drift = strategy is no longer earning
its place; demote.

Today, the data exists per strategy but is not joined to per-
symbol.

---

## Recommended evaluation workflow today

Given what's instrumented and what isn't, the practical workflow
for "which strategy is handling this symbol better?" today is:

1. Open the **Compare** page (or the email digest), find the
   symbol's row.
2. Read the **top-candidate strategy** + its Sharpe. That's the
   headline answer.
3. Check the **strategy_consensus** line ("4 of 7 currently
   long"). If majority-long, the trend-following strategies are
   in agreement; if minority-long, dissent is significant.
4. Open the **Backtest** page (Signals.tsx) for the symbol. The
   per-strategy panel shows each strategy's:
   - Current action (BUY / SELL / HOLD)
   - In-position state
   - Backtest stats (Sharpe / CAGR / max DD)
   - The most recent signal date
5. Pivot mentally — which strategies have Sharpe ≥ 1.0? Which
   are currently long? If those two sets mostly overlap, the
   consensus is well-supported. If they don't, dig into why.
6. Sanity-check the **instrument-strategy fit** (STRATEGIES.md §
   "The instrument-strategy fit problem"). If the lone dissenter
   is RSI MR on a momentum ETF, you can safely ignore its vote.

The work to make this single-click rather than 6-step is the
**leaderboard view** described in Lens 1, plus the per-row hit
rate column from Lens 3. Both are small surface-area changes —
the data already exists.

---

## What we'd build to do this well

Listed in rough order of leverage (most useful first):

1. **Leaderboard widget on Decide page**. For the focused symbol,
   show all 7 strategies ranked by Sharpe with a small badge for
   each (BUY / SELL / HOLD-IN / HOLD-OUT). One-line answer to
   "which strategy is doing best." ~3-4 hours.

2. **Per-strategy hit-rate column in the comparator output**.
   Adds one number to every (symbol, strategy) row. Backend
   already has the engine; frontend needs to render it. ~2 hours.

3. **Instrument-type tagging on the universe**. Add a
   `factor_type` field to every entry in
   `strategies/tradepro_strategies/watchlists.py`. ~4-6 hours for
   the data entry across the full universe.

4. **Strategy-fit filter in the consensus engine**. Once
   instrument tags exist, the consensus can either exclude or
   down-weight structurally mismatched strategies (RSI MR on
   momentum ETFs, Donchian on extreme low-vol ETFs, …). Closes
   the MTUM problem. ~3 hours after #3 is done.

5. **Regime-weighted consensus**. Each strategy's vote gets
   weighted by its Sharpe in today's detected regime. Solves Lens
   2. ~6-8 hours including a backfill of per-regime stats per
   strategy.

6. **Dissent-accuracy report**. Per-strategy monthly rollup of
   how often this strategy's dissent vote was correct. ~1 day.

7. **Live-vs-backtest drift dashboard**. Joins paper-trading P&L
   with backtest predictions, alerts on severe drift. Probably
   waits for the DB migration. ~2 days after DB is in place.

Each of these is a strict improvement; none requires any of the
others. Pick the leverage point that matches your current pain.

---

## See also

- [STRATEGIES.md](STRATEGIES.md) — what each strategy does and what
  it suits
- [CONCEPTS.md](strategies/CONCEPTS.md) — glossary of TradePro
  concepts including regime, hit rate, sentiment demotion
- [ROADMAP.md](ROADMAP.md) — Phase 3 (multi-family signals) and
  Phase 4 (signal transparency)

# TradePro — Data Sourcing Plan

Source-of-truth for **what data we need**, **where we currently get
each piece**, **what we'd swap to if we paid**, and **how the swap
should work mechanically**. Updated whenever a new feed is wired in
or a provider's free tier changes.

> **Design rule**: every data type goes through a provider abstraction
> with `provider="yahoo"` (or similar) as the default and an env-var
> override. Adding a paid provider is a `pip install`, a new module,
> and an env-var flip. No code edits to comparator / market_state /
> rationale layers.

---

## 1. Data types we use today

| Type | Used for | Default provider | Free? | Notes |
|---|---|---|---|---|
| **Daily OHLCV** | Backtests, signals, market_state | Yahoo Finance | ✅ Free | yfinance scrape; no key; ~30y history |
| **Adjusted close** | Split / dividend continuity | Yahoo Finance | ✅ Free | Same call as OHLCV |
| **Company info** (name, sector, P/E, dividend yield) | Fundamentals card on Decide | Yahoo (quoteSummary) | ✅ Free | Cached 24h |
| **News headlines** | Sentiment scoring, decision trace | Yahoo Finance | ✅ Free | ~10-20 articles/symbol, last 7d |
| **Historical earnings dates** | Chart markers, beat-and-retreat | Yahoo (earnings_dates) | ✅ Free | ~5y back |
| **Forward earnings calendar** | Position-into-earnings warning | Finnhub (when configured) | ✅ Free tier | 60 calls/min; `TRADEPRO_FINNHUB_API_KEY` env |
| **Macro index** (VIX, 10Y yield) | market_context | Yahoo (`^VIX`, `^TNX`) | ✅ Free | Daily bars only |
| **Trading 212 portfolio** | Portfolio page, holdings-aware decisions | T212 REST | ✅ Free | One key per account; live or demo |

## 2. Data types we want next (priority order)

### 2.1 Analyst PT raises / rating changes
- **Why**: User's earlier suggestion — explains the MU $730→$800 leg.
- **Free**: Finnhub `/stock/recommendation` (free, monthly cap),
  `/stock/upgrade-downgrade` (paid only on Finnhub free tier).
- **Paid**: Benzinga (~$50/mo), TipRanks (~$30/mo), Estimize.
- **Engine impact**: Soft-widen RSI overbought tolerance when a
  fresh PT raise lands. Add `analyst_signal` block to row.

### 2.2 Insider trades (Form 4)
- **Why**: Cluster of insider buys + dip = high-conviction BUY.
- **Free**: **SEC EDGAR** (US-listed only, XML feed, free, public).
  No key required.
- **Paid**: nothing better worth paying for — EDGAR IS the source.
- **Engine impact**: New decision-trace check (insider buy cluster
  last 30d → status: pass). Insider markers on chart alongside the
  earnings "E" dots.

### 2.3 Sector / peer-group news clustering
- **Why**: Detect "Samsung strike threat hits Micron" type catalysts
  via cross-ticker sentiment correlation.
- **Free**: Finnhub `/news?category=general` + `/company-news`.
- **Paid**: Benzinga firehose, Bloomberg news feed.
- **Engine impact**: A `sector_flow` signal on the row when a
  peer-group cluster of negative-/-positive news fires.

### 2.4 Options chain + IV
- **Why**: Implied vol > realised vol = market expects something;
  IV percentile is a strong WAIT signal.
- **Free**: Yahoo options chain (basic, US-listed). CBOE delayed.
- **Paid**: ORATS (~$80/mo), Polygon options ($99/mo).
- **Engine impact**: `iv_rank` on row; trace check #8.

### 2.5 Economic data / macro releases
- **Why**: Anchor "should I trade today?" against macro calendar
  (CPI, NFP, FOMC).
- **Free**: **FRED** (Federal Reserve, free, no key). Trading
  Economics free tier.
- **Paid**: Bloomberg, IBES.
- **Engine impact**: Calendar of upcoming macro events;
  near-event = soft de-risk signal.

### 2.6 Commodity OHLC (TTF, NBP, real coal etc.)
- **Why**: Energy commodity universe needs the European hubs;
  Yahoo doesn't carry them.
- **Free**: nothing reliable.
- **Paid**: ICE Endex / EEX direct (~£300-500/mo), Refinitiv,
  Bloomberg.
- **Engine impact**: Already provisioned via `WATCHLIST_META.provider`;
  just need to wire the AV/ICE fetcher.

### 2.7 Crypto OHLC
- **Why**: Optional — only if user starts trading crypto.
- **Free**: Yahoo (`BTC-USD` etc.), Coinbase public API, Binance public.
- **Paid**: CoinGecko Pro, CryptoCompare.
- **Engine impact**: New universe `crypto_majors`; same plumbing.

## 3. Provider abstraction (the config layer)

### 3.1 Today
```python
# tradepro_strategies/data_fetch.py
def ensure_cached(provider: str, symbol: str, start: datetime, end: datetime):
    if provider == "yahoo":
        return yahoo_fetch(symbol, start, end)
    elif provider == "stooq":
        return stooq_fetch(symbol, start, end)
    raise ValueError(f"unknown provider {provider}")
```

Only `provider` is configurable; news / earnings / fundamentals
each hardcode their source.

### 3.2 Target shape

```python
# tradepro_strategies/providers/__init__.py
PRICE_PROVIDERS    = {"yahoo": YahooProvider(), "alphavantage": AlphaVantage(), ...}
NEWS_PROVIDERS     = {"yahoo": ..., "finnhub": ..., "marketaux": ...}
EARNINGS_PROVIDERS = {"yahoo": ..., "finnhub": ...}
ANALYST_PROVIDERS  = {"finnhub": ..., "benzinga": ...}
INSIDER_PROVIDERS  = {"edgar": ...}
MACRO_PROVIDERS    = {"fred": ..., "yahoo": ...}
```

Selected by env vars with sensible defaults:
- `TRADEPRO_PRICE_PROVIDER` (default `yahoo`)
- `TRADEPRO_NEWS_PROVIDER` (default `yahoo`)
- `TRADEPRO_EARNINGS_PROVIDER` (default `yahoo`)
- `TRADEPRO_ANALYST_PROVIDER` (default empty — feature off)
- `TRADEPRO_INSIDER_PROVIDER` (default empty — feature off)
- `TRADEPRO_MACRO_PROVIDER` (default `yahoo`)

Plus a per-universe override via `WATCHLIST_META[u]["providers"]`
(already exists for `provider` — extend to a nested dict).

### 3.3 Fallback chains

Some calls have multiple legitimate sources. Example pattern:

```python
TRADEPRO_NEWS_PROVIDER_CHAIN=finnhub,yahoo
```

Try Finnhub first; if it returns empty or 429s, fall back to Yahoo.
Logged in the run's JSONL event log so the user can see when a
paid feed quietly fell back to the free one.

## 4. Cost model — when does paying make sense?

| Tier | What you get | £ / mo |
|---|---|---|
| **Free only (today)** | Yahoo prices + news, Finnhub earnings, T212 portfolio | £0 |
| **+ Finnhub paid** | Analyst PT raises, full company news | ~£30 |
| **+ Benzinga** | Real-time news firehose | ~£40 |
| **+ ICE Endex** | TTF / NBP / European gas | ~£400 |
| **+ Polygon** | Options chain + IV percentile | ~£80 |

Sane upgrade path: free → +Finnhub paid (analyst) → +ICE (if energy
focus continues) → +Polygon (if options trading starts). No need
to pay for Bloomberg/Refinitiv until institutional scale.

## 5. Implementation order

1. **Provider abstraction** — restructure today's `ensure_cached`
   into the registry shape. No new data, just the plumbing.
2. **Finnhub analyst signal** (free tier) — first new feed; tests
   the abstraction end-to-end.
3. **SEC EDGAR insider trades** (free) — second feed, different
   shape (XML not JSON), validates the abstraction handles variety.
4. **FRED macro calendar** (free) — third feed, augments
   market_context.
5. Pause; collect 2-3 weeks of signals; decide whether paid feeds
   move the verdict needle enough to justify the cost.

Each step is shippable on its own — no big-bang refactor.

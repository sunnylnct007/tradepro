"""Cross-symbol, cross-strategy comparator.

Given a list of symbols (typically a watchlist like `etf_uk_core`) and a
list of (strategy, params) pairs, run a backtest for every combination,
attach per-regime stress stats, and rank the results.

The output is intentionally JSON-friendly: the same dict that goes to the
artefact directory is the one we POST to /api/ingest/compare so the
website can render the ranked table without re-running anything.

Each (symbol, strategy) row also carries `current_action` ∈ {BUY, SELL,
HOLD} — the signal value on the most recent bar — so the website can
answer "given today, what should I do?" directly from this payload.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import pandas as pd

from .backtest import BacktestConfig, FeeModel, run_backtest
from .cache import ensure_cached
from .external_consensus import ExternalConsensus, _fetch_info, fetch_consensus
from .fundamentals import Fundamentals, fetch_fundamentals
from .llm import get_provider as get_llm_provider
from .market_context import market_context
from .market_state import MarketState, market_state
from .news import NewsItem, fetch_news
from .news_sentiment import (
    ScoredHeadline, SentimentSummary, SentimentTelemetry,
    score_news, summarise_recent,
)
from .observability import RunLogger
from .rationale import Rationale, build_rationale, gather_facts
from .regimes import REGIMES, all_regime_stats
from .remote_settings import (
    DEFAULT_LOOKBACK_DAYS, DEFAULT_MEAN_SENTIMENT_THRESHOLD,
    DEFAULT_MIN_MATERIAL_NEGATIVE, fetch_sentiment_settings,
)
from .schema import SCHEMA_VERSION, ComparePayload
from .strategies import resolve as resolve_strategy

# Compile-time fallback for the prompt version. The thresholds are
# fetched from the API at run start (so the user can tune them via
# the Settings page) but the prompt itself is shipped with the code.
SENTIMENT_PROMPT_VERSION = "v1"             # bump when the scoring prompt changes


@dataclass
class StrategySpec:
    name: str
    params: dict[str, Any] = field(default_factory=dict)

    @property
    def label(self) -> str:
        if not self.params:
            return self.name
        kv = ",".join(f"{k}={v}" for k, v in sorted(self.params.items()))
        return f"{self.name}({kv})"


@dataclass
class CompareConfig:
    provider: str = "yahoo"
    initial_capital: float = 10_000.0
    currency: str = "GBP"
    fees: FeeModel = field(default_factory=FeeModel)
    rank_metric: str = "sharpe"  # one of: sharpe, cagr_pct, total_return_pct, max_drawdown_pct


_NAN = float("nan")

# Yahoo ticker suffix → trading currency. The map is conservative: we
# return None for unknown suffixes rather than guess, so the row gets
# labelled '—' in the UI and the user knows we don't know.
_SUFFIX_CURRENCY: dict[str, str] = {
    "L": "GBP",     # London Stock Exchange (.L)
    "DE": "EUR",    # Deutsche Börse XETRA
    "PA": "EUR",    # Paris (Euronext)
    "AS": "EUR",    # Amsterdam
    "MI": "EUR",    # Milan
    "MC": "EUR",    # Madrid
    "SW": "CHF",    # SIX Swiss
    "T": "JPY",     # Tokyo
    "HK": "HKD",    # Hong Kong
    "TO": "CAD",    # Toronto
    "AX": "AUD",    # ASX
    "NS": "INR",    # NSE India
    "BO": "INR",    # BSE India
}


def _symbol_currency(symbol: str) -> str:
    """Best-effort native trading currency for a Yahoo ticker. Defaults to
    USD for tickers without a known venue suffix (US-listed default)."""
    if not symbol:
        return "USD"
    if "." in symbol:
        suffix = symbol.rsplit(".", 1)[-1].upper()
        return _SUFFIX_CURRENCY.get(suffix, "USD")
    if symbol.startswith("^"):
        # Indices — use a coarse heuristic; ^FTSE/^FTMC are GBP, others
        # default to USD.
        return "GBP" if symbol in ("^FTSE", "^FTMC") else "USD"
    return "USD"


def _data_age_days(prices: pd.DataFrame, end: datetime) -> int | None:
    """How stale is the latest bar relative to the requested `end` date?
    Useful so the UI can flag 'this row's price is from 9 days ago, take
    the verdict with a pinch of salt'."""
    if prices.empty:
        return None
    last = prices.index[-1]
    end_ts = pd.Timestamp(end)
    if end_ts.tzinfo is None and last.tzinfo is not None:
        end_ts = end_ts.tz_localize("UTC")
    if last.tzinfo is None and end_ts.tzinfo is not None:
        last = last.tz_localize("UTC")
    delta = (end_ts - last).days
    return max(0, int(delta))


def _action_from_signal(latest_signal: int) -> str:
    if latest_signal == 1:
        return "BUY"
    if latest_signal == -1:
        return "SELL"
    return "HOLD"


def _safe_float(x) -> float:
    """Replace NaN/inf with None-friendly floats for JSON serialisation."""
    if x is None:
        return _NAN
    try:
        f = float(x)
    except (TypeError, ValueError):
        return _NAN
    if math.isnan(f) or math.isinf(f):
        return _NAN
    return f


def _row_for(
    symbol: str,
    strategy: StrategySpec,
    prices: pd.DataFrame,
    state: MarketState,
    consensus: ExternalConsensus,
    fundamentals: Fundamentals,
    news: list[NewsItem],
    scored_news: list[ScoredHeadline],
    sentiment_summary: SentimentSummary,
    sentiment_status: str,
    end: datetime,
    cfg: CompareConfig,
) -> dict:
    """Run one (symbol, strategy) backtest and return a JSON-ready row."""
    currency = _symbol_currency(symbol)
    data_age_days = _data_age_days(prices, end)
    # Augment each NewsItem with its sentiment score + reason (or None
    # + sentiment_error so the UI can show why scoring failed). Always
    # produced — even on backtest failure paths — so news rendering
    # doesn't depend on the rest of the pipeline succeeding.
    enriched_news = _merge_scored(news, scored_news)
    if prices.empty:
        return {
            "symbol": symbol,
            "strategy": strategy.name,
            "strategy_label": strategy.label,
            "params": dict(strategy.params),
            "bars": 0,
            "stats": {},
            "regimes": [],
            "current_action": "HOLD",
            "latest_signal": 0,
            "latest_bar": None,
            "in_position": False,
            "position_since": None,
            "market_state": state.to_dict(),
            "external_consensus": consensus.to_dict(),
            "fundamentals": fundamentals.to_dict(),
            "news": enriched_news,
            "sentiment_summary": sentiment_summary.to_dict(),
            "sentiment_status": sentiment_status,
            "currency": currency,
            "data_age_days": data_age_days,
            "error": "no_data",
        }

    try:
        signal_fn = resolve_strategy(strategy.name, strategy.params)
        bt_cfg = BacktestConfig(
            initial_capital=cfg.initial_capital,
            currency=cfg.currency,
            fees=cfg.fees,
        )
        result = run_backtest(prices, signal_fn, bt_cfg)
    except Exception as e:  # noqa: BLE001
        return {
            "symbol": symbol,
            "strategy": strategy.name,
            "strategy_label": strategy.label,
            "params": dict(strategy.params),
            "bars": int(len(prices)),
            "stats": {},
            "regimes": [],
            "current_action": "HOLD",
            "latest_signal": 0,
            "latest_bar": None,
            "in_position": False,
            "position_since": None,
            "market_state": state.to_dict(),
            "external_consensus": consensus.to_dict(),
            "fundamentals": fundamentals.to_dict(),
            "news": enriched_news,
            "sentiment_summary": sentiment_summary.to_dict(),
            "sentiment_status": sentiment_status,
            "currency": currency,
            "data_age_days": data_age_days,
            "error": str(e),
        }

    # Re-derive the signal on the (adjusted) prices so we can read today's
    # value. run_backtest already applies the close←adj_close swap; mirror
    # that here so latest_signal is exactly what the executor saw.
    adjusted = prices.assign(close=prices["adj_close"]) if "adj_close" in prices.columns else prices
    full_signals = signal_fn(adjusted).reindex(adjusted.index).fillna(0).astype(int)
    latest_signal = int(full_signals.iloc[-1]) if not full_signals.empty else 0
    latest_bar = adjusted.index[-1].isoformat() if not adjusted.empty else None

    # "Is the strategy currently long this asset?" — find the most recent
    # non-zero signal and look at its sign. This is what a multi-strategy
    # consensus vote ("more than half are long → BUY") needs, since the
    # latest-bar signal alone is mostly 0/HOLD on cross-event strategies.
    in_position = False
    position_since: str | None = None
    nonzero = full_signals[full_signals != 0]
    if not nonzero.empty:
        last_idx = nonzero.index[-1]
        last_kind = int(nonzero.iloc[-1])
        in_position = last_kind == 1
        position_since = last_idx.isoformat()

    regime_df = all_regime_stats(result.equity_curve)
    regime_rows = [
        {
            "key": r["regime_key"],
            "name": r["regime_name"],
            "kind": r["kind"],
            "bars": int(r["bars"]),
            "return_pct": _safe_float(r["return_pct"]),
            "max_drawdown_pct": _safe_float(r["max_drawdown_pct"]),
        }
        for r in regime_df.to_dict(orient="records")
        if int(r["bars"]) > 0
    ]

    return {
        "symbol": symbol,
        "strategy": strategy.name,
        "strategy_label": strategy.label,
        "params": dict(strategy.params),
        "bars": int(len(prices)),
        "stats": {k: _safe_float(v) for k, v in result.stats.items()},
        "regimes": regime_rows,
        "current_action": _action_from_signal(latest_signal),
        "latest_signal": latest_signal,
        "latest_bar": latest_bar,
        "in_position": bool(in_position),
        "position_since": position_since,
        "market_state": state.to_dict(),
        "external_consensus": consensus.to_dict(),
        "fundamentals": fundamentals.to_dict(),
        "news": enriched_news,
        "sentiment_summary": sentiment_summary.to_dict(),
        "sentiment_status": sentiment_status,
        "currency": currency,
        "data_age_days": data_age_days,
        "error": None,
    }


def _attach_bucket_and_rationale(
    rows: list[dict],
    mean_threshold: float,
    min_material: int,
    logger: RunLogger | None = None,
) -> None:
    """Compute the per-symbol bucket (BUY/WAIT/AVOID), apply the
    sentiment demotion rule, then generate a plain-English rationale
    for each symbol. The result is attached to every row that shares
    the symbol — same pattern as market_state."""
    by_symbol: dict[str, list[dict]] = {}
    for r in rows:
        by_symbol.setdefault(r["symbol"], []).append(r)

    for symbol, sym_rows in by_symbol.items():
        sym_rows.sort(key=lambda r: r.get("rank", 1e9))
        best = sym_rows[0]

        ms = best.get("market_state") or {}
        price_verdict = ms.get("entry_signal", "HOLD")
        long_count = sum(1 for r in sym_rows if r.get("in_position"))
        total = len(sym_rows)
        majority_long = long_count > total / 2

        # Same rule the frontend was using; now lives server-side too.
        if price_verdict == "AVOID":
            bucket = "AVOID"
            reason = ms.get("entry_reason") or "Confirmed downtrend."
        elif price_verdict == "WAIT":
            bucket = "WAIT"
            reason = ms.get("entry_reason") or "Better entries likely soon."
        elif majority_long and price_verdict in ("BUY", "HOLD"):
            bucket = "BUY"
            reason = (
                ms.get("entry_reason")
                or f"{long_count} of {total} strategies currently long; "
                   f"price action supports entry."
            )
        else:
            bucket = "WAIT"
            reason = (
                f"Only {long_count} of {total} strategies are currently long "
                f"— wait for more confirmation."
            )

        # Sentiment demotion. Same thresholds the LLM bar shows.
        sentiment_demoted = False
        if bucket == "BUY":
            ss = best.get("sentiment_summary") or {}
            mean = ss.get("mean_sentiment")
            mat_neg = ss.get("material_negative_count", 0)
            if (mean is not None
                    and mean <= mean_threshold
                    and mat_neg >= min_material):
                sentiment_demoted = True
                bucket = "WAIT"
                reason = (
                    f"Sentiment demotion: 7d mean {mean:.2f} ≤ "
                    f"threshold {mean_threshold} AND {mat_neg} "
                    f"material-negative headlines (≥ {min_material})."
                )

        # Build the rationale once per symbol from the best row's data.
        try:
            facts = gather_facts(
                symbol=symbol,
                bucket=bucket,
                bucket_reason=reason,
                long_count=long_count,
                total_strategies=total,
                market_state=ms,
                sentiment_summary=best.get("sentiment_summary"),
                sentiment_status=best.get("sentiment_status"),
                best_strategy_label=best.get("strategy_label", best.get("strategy", "")),
                best_stats=best.get("stats") or {},
                regimes=best.get("regimes") or [],
                fundamentals=best.get("fundamentals"),
                sentiment_demoted=sentiment_demoted,
            )
            rat = build_rationale(facts)
            if logger:
                logger.emit(
                    "compare.rationale_generated",
                    symbol=symbol, bucket=bucket,
                    source=rat.source, verified=rat.verified,
                    model=rat.model,
                )
            rationale_dict = rat.to_dict()
        except Exception as e:  # noqa: BLE001
            if logger:
                logger.emit("compare.rationale_failed", symbol=symbol, error=str(e))
            rationale_dict = None

        # Copy bucket + reason + sentiment-demoted flag + rationale onto
        # every row for this symbol so the frontend can render any row's
        # expand panel without re-deriving.
        for r in sym_rows:
            r["bucket"] = bucket
            r["bucket_reason"] = reason
            r["sentiment_demoted"] = sentiment_demoted
            if rationale_dict is not None:
                r["rationale"] = rationale_dict


def _merge_scored(news: list[NewsItem], scored: list[ScoredHeadline]) -> list[dict]:
    """Pair news items with their sentiment scores. The list lengths
    are guaranteed equal (score_news preserves order), but defensively
    handle drift via title match."""
    out: list[dict] = []
    by_title = {s.title: s for s in scored}
    for raw, paired in zip(news, scored + [None] * (len(news) - len(scored))):
        d = raw.to_dict()
        s = paired if (paired and paired.title == raw.title) else by_title.get(raw.title)
        if s is None:
            d["sentiment"] = None
            d["sentiment_themes"] = []
            d["sentiment_material"] = False
            d["sentiment_model"] = None
            d["sentiment_error"] = "no scoring attempt"
        else:
            d["sentiment"] = s.sentiment
            d["sentiment_themes"] = list(s.themes)
            d["sentiment_material"] = bool(s.material)
            d["sentiment_model"] = s.model
            d["sentiment_error"] = s.error
        out.append(d)
    return out


def _rank_value(row: dict, metric: str) -> float:
    """Sort key for ranking. Higher-is-better for sharpe/cagr/total_return,
    lower-is-better for max_drawdown_pct (which is negative)."""
    v = row.get("stats", {}).get(metric)
    if v is None or (isinstance(v, float) and (math.isnan(v) or math.isinf(v))):
        return -math.inf
    if metric == "max_drawdown_pct":
        # max_drawdown_pct is a negative number; "best" is closest to zero,
        # i.e. largest. Plain ascending-with-negation works.
        return float(v)
    return float(v)


def compare(
    symbols: list[str],
    strategies: list[StrategySpec],
    start: datetime,
    end: datetime,
    cfg: CompareConfig | None = None,
    logger: RunLogger | None = None,
) -> dict:
    """Run every (symbol × strategy) backtest and return a ranked payload.

    The returned dict is the JSON we want to ship to the website — see
    `cli/run_comparison.py` for the wire format.

    `logger` is optional — when passed, the comparator emits a stream
    of structured events (per-symbol fetch + scoring boundaries, per-
    LLM-call latency / cache hit-miss / parse failures) into the run's
    JSONL event log. Without a logger the run is silent but still
    works.
    """
    cfg = cfg or CompareConfig()
    telemetry = SentimentTelemetry()

    # Fetch live demotion settings from the API. Falls back to compiled
    # defaults if the API is unreachable; the source is captured in the
    # log so the run is auditable.
    settings = fetch_sentiment_settings()
    if logger:
        logger.emit(
            "compare.settings_loaded",
            source=settings.source,
            mean_sentiment_threshold=settings.mean_sentiment_threshold,
            min_material_negative_count=settings.min_material_negative_count,
            lookback_days=settings.lookback_days,
            updated_at=settings.updated_at,
        )

    rows: list[dict] = []
    price_cache: dict[str, pd.DataFrame] = {}
    state_cache: dict[str, MarketState] = {}
    consensus_cache: dict[str, ExternalConsensus] = {}
    fundamentals_cache: dict[str, Fundamentals] = {}
    news_cache: dict[str, list[NewsItem]] = {}
    scored_news_cache: dict[str, list[ScoredHeadline]] = {}
    sentiment_summary_cache: dict[str, SentimentSummary] = {}
    sentiment_status_cache: dict[str, str] = {}
    # Top-level errors list — surfaces symbols that failed to fetch or
    # came back empty, so the UI can show 'data unavailable for X' rather
    # than silently dropping them.
    errors: list[dict] = []

    # Resolve LLM provider once per run. NoOpProvider is returned silently
    # when nothing's configured / Ollama is down, so the rest of the loop
    # doesn't need to care — the per-row `sentiment_status` makes that
    # transparent on the frontend.
    llm = get_llm_provider()
    llm_healthy = llm.healthy()
    if logger:
        logger.emit("llm.provider", name=llm.name, model=llm.model, healthy=llm_healthy)

    import time as _time

    for symbol in symbols:
        if symbol not in price_cache:
            sym_start = _time.time()
            if logger:
                logger.emit("compare.symbol.start", symbol=symbol)
            try:
                price_cache[symbol] = ensure_cached(cfg.provider, symbol, start, end)
            except Exception as e:  # noqa: BLE001
                price_cache[symbol] = pd.DataFrame()
                errors.append({"symbol": symbol, "stage": "fetch", "error": str(e)})
                if logger:
                    logger.emit("compare.symbol.fetch_failed", symbol=symbol, error=str(e))
            state_cache[symbol] = market_state(symbol, price_cache[symbol])
            # Yahoo quote summary fetched once per symbol, shared across
            # consensus + fundamentals — saves a 1-2s round-trip per
            # symbol vs fetching twice. News is a separate API call.
            info = _fetch_info(symbol)
            consensus_cache[symbol] = fetch_consensus(symbol, info)
            fundamentals_cache[symbol] = fetch_fundamentals(symbol, info)
            news_cache[symbol] = fetch_news(symbol)
            if logger:
                logger.emit("compare.symbol.fetched",
                            symbol=symbol,
                            bars=len(price_cache[symbol]),
                            news_items=len(news_cache[symbol]))
            # Sentiment scoring is best-effort and visible: every row
            # carries a status flag explaining what happened.
            if not llm_healthy:
                scored_news_cache[symbol] = []
                sentiment_summary_cache[symbol] = summarise_recent([], [], days=7)
                sentiment_status_cache[symbol] = "provider_down"
            elif not news_cache[symbol]:
                scored_news_cache[symbol] = []
                sentiment_summary_cache[symbol] = summarise_recent([], [], days=7)
                sentiment_status_cache[symbol] = "no_news"
            else:
                scoring_t0 = _time.time()
                scored = score_news(
                    news_cache[symbol], llm,
                    telemetry=telemetry, logger=logger, symbol=symbol,
                )
                scored_news_cache[symbol] = scored
                sentiment_summary_cache[symbol] = summarise_recent(
                    scored, news_cache[symbol], days=7,
                )
                failed = sum(1 for s in scored if s.error is not None)
                if failed == 0:
                    sentiment_status_cache[symbol] = "scored"
                elif failed < len(scored):
                    sentiment_status_cache[symbol] = "partial"
                else:
                    sentiment_status_cache[symbol] = "all_failed"
                if failed > 0:
                    errors.append({
                        "symbol": symbol,
                        "stage": "sentiment",
                        "error": f"{failed} of {len(scored)} headlines failed to score",
                    })
                if logger:
                    logger.emit("compare.symbol.scored",
                                symbol=symbol,
                                items=len(scored),
                                failed=failed,
                                ms=int((_time.time() - scoring_t0) * 1000))
            if price_cache[symbol].empty:
                errors.append({"symbol": symbol, "stage": "no_data",
                               "error": "no bars returned for the requested window"})
            if logger:
                logger.emit("compare.symbol.done",
                            symbol=symbol,
                            ms=int((_time.time() - sym_start) * 1000))
        prices = price_cache[symbol]
        state = state_cache[symbol]
        consensus = consensus_cache[symbol]
        fundamentals = fundamentals_cache[symbol]
        news = news_cache[symbol]
        scored_news = scored_news_cache[symbol]
        sentiment_summary = sentiment_summary_cache[symbol]
        sentiment_status = sentiment_status_cache[symbol]
        for strat in strategies:
            rows.append(_row_for(symbol, strat, prices, state, consensus,
                                 fundamentals, news, scored_news,
                                 sentiment_summary, sentiment_status, end, cfg))

    rows.sort(key=lambda r: _rank_value(r, cfg.rank_metric), reverse=True)
    for i, row in enumerate(rows, start=1):
        row["rank"] = i

    # Per-symbol bucket computation + rationale generation. Both are
    # symbol-level (not per-row) so we compute once and copy onto every
    # row for that symbol — matches what the frontend was already doing
    # client-side, but now visible in the JSON payload too.
    _attach_bucket_and_rationale(
        rows, settings.mean_sentiment_threshold,
        settings.min_material_negative_count, logger=logger,
    )

    best_per_strategy: dict[str, dict] = {}
    for row in rows:
        s = row["strategy"]
        if s not in best_per_strategy:
            best_per_strategy[s] = {"symbol": row["symbol"], "rank": row["rank"]}

    best_overall = rows[0] if rows else None

    # Macro / sentiment proxy fetched once per run, not per symbol — VIX
    # and 10Y move at index level, not per-ticker.
    ctx = market_context(start, end).to_dict()

    # Currency-mix flag — false when every row trades in the same currency,
    # true when the universe spans more than one (e.g. etf_all). Frontend
    # uses this to decide whether to show currency tags and a warning
    # against absolute-fee comparisons across rows.
    currencies = {r.get("currency") for r in rows if r.get("currency")}
    is_mixed_currency = len(currencies) > 1
    primary_currency = (
        max(currencies, key=lambda c: sum(1 for r in rows if r.get("currency") == c))
        if currencies else cfg.currency
    )

    payload = {
        "schema_version": SCHEMA_VERSION,
        "kind": "compare",
        "generated_at": datetime.utcnow().isoformat() + "Z",
        "from": start.date().isoformat(),
        "to": end.date().isoformat(),
        "provider": cfg.provider,
        "currency": cfg.currency,
        "rank_metric": cfg.rank_metric,
        "symbols": list(symbols),
        "strategies": [
            {"name": s.name, "params": dict(s.params), "label": s.label}
            for s in strategies
        ],
        "regimes": [
            {"key": r.key, "name": r.name, "kind": r.kind,
             "start": r.start.date().isoformat(), "end": r.end.date().isoformat(),
             "description": r.description}
            for r in REGIMES
        ],
        "market_context": ctx,
        "currency_mix": {
            "is_mixed": is_mixed_currency,
            "primary": primary_currency,
            "currencies": sorted(currencies),
        },
        # Surface every parameter of the sentiment pipeline so the UI
        # can render exactly what rule applied and what model produced
        # the scores — no hidden behaviour.
        "llm": {
            "provider": llm.name,
            "model": llm.model,
            "healthy": llm_healthy,
            "prompt_version": SENTIMENT_PROMPT_VERSION,
            "demotion_rule": {
                "mean_sentiment_threshold": settings.mean_sentiment_threshold,
                "min_material_negative_count": settings.min_material_negative_count,
                "lookback_days": settings.lookback_days,
                "source": settings.source,         # "api" or "defaults"
                "settings_updated_at": settings.updated_at,
                "description": (
                    f"BUY → WAIT when {settings.lookback_days}-day rolling "
                    f"mean sentiment ≤ {settings.mean_sentiment_threshold} "
                    f"AND ≥ {settings.min_material_negative_count} "
                    f"material-negative headlines."
                ),
            },
            # Per-run aggregate of LLM activity — calls made, cache hit
            # rate, latencies. Lets the UI show "scored 56 · 12 from
            # cache · 2.3s avg" so users see the cost / freshness of
            # each refresh.
            "telemetry": telemetry.to_dict(),
        },
        "rows": rows,
        "errors": errors,
        "best_per_strategy": best_per_strategy,
        "best_overall": (
            {"symbol": best_overall["symbol"], "strategy": best_overall["strategy"],
             "rank_metric": cfg.rank_metric,
             "value": best_overall.get("stats", {}).get(cfg.rank_metric)}
            if best_overall else None
        ),
    }

    # Validate-on-emit. Catches drift the moment a field changes shape
    # rather than waiting for the frontend prod build to TypeScript-fail
    # in CI three commits later. Pydantic is tolerant (extra="allow")
    # so adding a new field doesn't break — only changing semantics does.
    try:
        ComparePayload.from_payload_dict(payload)
        if logger:
            logger.emit("compare.schema_validated", schema_version=SCHEMA_VERSION)
    except Exception as e:  # noqa: BLE001
        # Don't block emission — a single misbehaving row shouldn't kill
        # the whole run. Log loudly so a CI smoke-test or the run history
        # page surfaces it.
        if logger:
            logger.emit("compare.schema_validation_failed",
                        schema_version=SCHEMA_VERSION,
                        error=str(e)[:1000])
        # Always include the failure in the payload's errors list — the
        # UI then renders 'schema validation failed' as a visible issue.
        payload.setdefault("errors", []).append({
            "symbol": "*payload*",
            "stage": "schema_validation",
            "error": str(e)[:500],
        })

    return payload

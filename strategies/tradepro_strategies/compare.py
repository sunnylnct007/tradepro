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
import os
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import pandas as pd

from .backtest import BacktestConfig, FeeModel, run_backtest
from .cache import ensure_cached
from .catalysts import extract_catalysts
from .gates.earnings_proximity import (
    EarningsGate as _EarningsGate,
    EarningsGateConfig,
    classify as _eg_classify,
    route as _eg_route,
    sessions_since as _eg_sessions_since,
    sessions_to as _eg_sessions_to,
)
from .gates.catalyst_signal import CatalystConfig, detect_news_catalyst
from .gates.entry_quality import EntryQualityConfig, evaluate_entry_quality
from .combined_verdict import derive_combined_verdict
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
    # When True, override fees.stamp_duty_rate per-symbol via
    # tradepro_strategies.fees.stamp_duty_for_symbol — 0% for UCITS
    # ETFs, 0.5% for LSE shares, 0% for everything else. Default in
    # the CLI; set to False (with an explicit fees.stamp_duty_rate)
    # only when you specifically want a flat rate across the basket.
    stamp_duty_auto: bool = True


_NAN = float("nan")


def _resolve_api_base() -> str:
    """Resolve the TradePro API base URL for in-comparator integration
    calls (Finnhub earnings, analyst upgrades, etc.).

    Order:
      1. TRADEPRO_API_URL env var — explicit override
      2. ~/.tradepro/credentials `api_base_url` — the same file the
         pusher reads, so the worker hits the SAME box for integration
         fetches that it pushes results to. This is the path that
         matters in production: the local Mac API container doesn't
         have FINNHUB_API_KEY, but the AWS one does — pointing at AWS
         lets the worker's analyst_actions / upcoming-earnings calls
         get real data instead of {enabled: false}.
      3. http://localhost:5080 fallback — useful for dev when neither
         the env var nor the credentials file is configured.
    """
    import os as _os
    env = _os.environ.get("TRADEPRO_API_URL")
    if env:
        return env.rstrip("/")
    try:
        from .cli.push_to_api import CRED_PATH
        if CRED_PATH.exists():
            import json as _json
            data = _json.loads(CRED_PATH.read_text())
            base = data.get("api_base_url")
            if base:
                return str(base).rstrip("/")
    except (OSError, ValueError):
        pass
    return "http://localhost:5080"

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
    earnings_history: list[dict] | None = None,
    news_via: str | None = None,
) -> dict:
    """Run one (symbol, strategy) backtest and return a JSON-ready row."""
    currency = _symbol_currency(symbol)
    data_age_days = _data_age_days(prices, end)
    # Augment each NewsItem with its sentiment score + reason (or None
    # + sentiment_error so the UI can show why scoring failed). Always
    # produced — even on backtest failure paths — so news rendering
    # doesn't depend on the rest of the pipeline succeeding.
    enriched_news = _merge_scored(news, scored_news)
    # Catalyst overlay (Phase 17.3) — pull dated events out of the
    # same headlines we already display. The list lands on every row
    # the same way `news` does, so the UI can render it on any of
    # the no-data / error / success paths without conditionals.
    catalysts_list = [c.to_dict() for c in extract_catalysts(enriched_news)]
    history = list(earnings_history or [])
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
            "news_via": news_via,
            "catalysts": catalysts_list,
            "sentiment_summary": sentiment_summary.to_dict(),
            "sentiment_status": sentiment_status,
            "currency": currency,
            "data_age_days": data_age_days,
            "historical_earnings": history,
            "error": "no_data",
        }

    try:
        signal_fn = resolve_strategy(strategy.name, strategy.params)
        # Resolve fees per-symbol when stamp_duty_auto is on so the
        # right SDRT rate (0% for UCITS ETFs, 0.5% for LSE shares,
        # 0% for everything else) hits the backtest. Avoids the
        # silent-Sharpe-bias bug a user can no longer hit by
        # forgetting --stamp-duty 0.
        if cfg.stamp_duty_auto:
            from .fees import stamp_duty_for_symbol
            symbol_fees = FeeModel(
                commission_per_trade=cfg.fees.commission_per_trade,
                stamp_duty_rate=stamp_duty_for_symbol(symbol),
            )
        else:
            symbol_fees = cfg.fees
        bt_cfg = BacktestConfig(
            initial_capital=cfg.initial_capital,
            currency=cfg.currency,
            fees=symbol_fees,
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
            "news_via": news_via,
            "catalysts": catalysts_list,
            "sentiment_summary": sentiment_summary.to_dict(),
            "sentiment_status": sentiment_status,
            "currency": currency,
            "data_age_days": data_age_days,
            "historical_earnings": history,
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

    # Ichimoku targets: price_target, stop_level, rr_ratio, cloud
    # position lines. Computed for the ichimoku_cloud strategy only —
    # other strategies don't have a cloud, surfacing these would
    # confuse the reader. The bucket layer surfaces them at row
    # top-level when the active strategy has them so the website can
    # render "BUY → £42.50, stop £38.10, R/R 2.3x" alongside the
    # verdict.
    ichimoku_extras: dict = {}
    if strategy.name == "ichimoku_cloud":
        try:
            from .strategies import ichimoku_targets
            ichimoku_extras = ichimoku_targets(adjusted, **strategy.params)
        except Exception:  # noqa: BLE001 — best-effort; row still ships
            ichimoku_extras = {}

    return {
        "symbol": symbol,
        "strategy": strategy.name,
        "strategy_label": strategy.label,
        "params": dict(strategy.params),
        "bars": int(len(prices)),
        # _safe_float is for NUMERIC stats only — result.stats also carries
        # stats_suspect (bool) and stats_suspect_reason (str | None), the
        # garbage-bar integrity flag the digest's _publishable() suppresses
        # on. Blindly _safe_float-ing every value ran float("outlier bar...")
        # on the reason string, threw, and silently replaced it with NaN —
        # so the flag survived (bool -> 1.0/0.0) but its human-readable
        # reason was destroyed on every row before it ever left this process.
        "stats": {
            k: (v if k in ("stats_suspect", "stats_suspect_reason") else _safe_float(v))
            for k, v in result.stats.items()
        },
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
        "news_via": news_via,
        "catalysts": catalysts_list,
        "sentiment_summary": sentiment_summary.to_dict(),
        "sentiment_status": sentiment_status,
        "currency": currency,
        "data_age_days": data_age_days,
        "historical_earnings": history,
        "ichimoku": ichimoku_extras or None,
        "error": None,
    }


# ── Verdict pipeline now lives in shared_verdict.py (single source of truth).
# Re-exported so existing `from .compare import <fn>` callers (market_state,
# cross_sectional, mcp.tools, news_context) keep working unchanged.
from .shared_verdict import (  # noqa: E402
    apply_data_gap_integrity,
    apply_earnings_suppressor,
    apply_horizon_and_range_demotion,
    apply_sentiment_demotion,
    apply_swing_strict_demotion,
    cap_bucket_at_low_conviction,
    collect_data_gaps,
    compute_bucket,
    compute_conviction,
    enforce_coherence,
    gap_labels,
)


def _attach_bucket_and_rationale(
    rows: list[dict],
    mean_threshold: float,
    min_material: int,
    logger: RunLogger | None = None,
    llm_healthy: bool = True,
) -> None:
    """Compute the per-symbol bucket (BUY/WAIT/AVOID), apply the
    sentiment demotion rule, then generate a plain-English rationale
    for each symbol. The result is attached to every row that shares
    the symbol — same pattern as market_state."""
    by_symbol: dict[str, list[dict]] = {}
    for r in rows:
        by_symbol.setdefault(r["symbol"], []).append(r)

    # Run-level earnings-feed CANARY (the MA hole). If a GUARANTEED quarterly
    # reporter in this run has no earnings date, the feed is degraded — and a
    # just-reported name (MA: reported yesterday, feed dateless) would surface
    # as a merely-penalised BUY instead of a POST_DIGEST veto. When the canary
    # trips, UNKNOWN escalates to veto for the whole run (fail-loud, run-wide,
    # instead of trusting per-name penalties on a broken feed).
    from .gates.earnings_proximity import (
        CANARY_SYMBOLS as _EG_CANARIES,
        resolve_unknown_when_degraded as _eg_resolve,
    )
    _news_hint_cache: dict[str, bool | None] = {}
    _dead_canaries: list[str] = []
    _canary_api_base: str | None = None
    for _c in _EG_CANARIES:
        _crows = by_symbol.get(_c)
        if _crows:
            _csig = _crows[0].get("earnings_signal") or {}
        else:
            # Canary not in THIS scan's universe (e.g. a narrow us_growth_tech
            # / us_semis scan contains none of MA/V/AXP/JPM/MSFT/AAPL) — fetch
            # its date independently instead of silently skipping it. Without
            # this, a scan whose universe happens to exclude every canary gets
            # ZERO degraded-feed protection: earnings_feed_degraded stays
            # False no matter how broken the feed is, so a name's own
            # EARNINGS_UNKNOWN never escalates past the flat per-name 0.5x
            # penalty (the NVDA case, 2026-08-08 — real Aug-26 earnings date,
            # feed returned nothing, us_semis scan had no canary to catch it).
            _csig = {}
            try:
                from .earnings import fetch_earnings_in_range, fetch_upcoming_earnings
                if _canary_api_base is None:
                    _canary_api_base = _resolve_api_base()
                _hist = fetch_earnings_in_range(_c, lookback_days=120)
                _past_dates = [str(e.get("date"))[:10] for e in _hist if e.get("date")]
                if _past_dates:
                    _csig["last_report_date"] = max(_past_dates)
                _up = fetch_upcoming_earnings(_c, _canary_api_base)
                if _up:
                    _csig["upcoming"] = _up
            except Exception as _exc:  # noqa: BLE001 — best-effort, never block the run
                if logger:
                    logger.emit("compare.canary_fetch_failed", symbol=_c, error=str(_exc))
        _has_date = bool(
            (_csig.get("upcoming") or {}).get("date")
            or _csig.get("last_report_date")
            or (_csig.get("earnings") or {}).get("announce_date")
        )
        if not _has_date:
            _dead_canaries.append(_c)
    earnings_feed_degraded = bool(_dead_canaries)
    if earnings_feed_degraded and logger:
        logger.emit("compare.earnings_feed_degraded", canaries=_dead_canaries)

    from .factor_types import (
        factor_type_for, horizon_for, incompatible_strategies_for,
        is_compatible, strategy_type_for,
    )

    for symbol, sym_rows in by_symbol.items():
        sym_rows.sort(key=lambda r: r.get("rank", 1e9))
        best = sym_rows[0]

        ms = best.get("market_state") or {}
        price_verdict = ms.get("entry_signal", "HOLD")

        # Tag each row with its instrument-fit verdict. The UI uses
        # excluded_for_fit to grey out incompatible rows in the
        # leaderboard and to render the "X strategies excluded"
        # banner alongside the consensus line.
        symbol_factor = factor_type_for(symbol)
        excluded_strategies = list(incompatible_strategies_for(symbol))
        for row in sym_rows:
            row["factor_type"] = symbol_factor
            strategy_name = row.get("strategy", "")
            row["excluded_for_fit"] = not is_compatible(strategy_name, symbol)
            row["excluded_reason"] = (
                f"{strategy_name} is structurally incompatible with "
                f"{symbol_factor}-class instruments — see STRATEGIES.md "
                "'instrument-strategy fit'."
            ) if row["excluded_for_fit"] else None
            # 3-axis classification per IMPROVEMENT_SUGGESTIONS_v1.md §1.
            # horizon + strategy_type are properties of the strategy
            # itself, not the symbol — surface them on every row so the
            # UI / MCP can filter "show me only swing momentum signals".
            row["horizon"] = horizon_for(strategy_name)
            row["strategy_type"] = strategy_type_for(strategy_name)

        # Long-count: count only strategies that are BOTH in position
        # AND historically profitable (Sharpe >= 0 on this symbol),
        # AND structurally compatible with the instrument. A negative-
        # Sharpe strategy holding a long position is bleeding money on
        # the backtest. An incompatible strategy (RSI MR on MTUM) is
        # *philosophically* wrong — its vote should not influence the
        # bucket consensus regardless of its Sharpe.
        def _votes_long(row: dict) -> bool:
            if row.get("excluded_for_fit"):
                return False
            if not row.get("in_position"):
                return False
            sharpe = (row.get("stats") or {}).get("sharpe")
            if sharpe is None:
                return False
            try:
                if float(sharpe) < 0:
                    return False
            except (TypeError, ValueError):
                return False
            return True

        # total = compatible strategies only. The UI's "N of M
        # currently long" line uses this denominator so the math
        # adds up (M = strategies that actually voted, not the
        # full registry).
        compatible_rows = [r for r in sym_rows if not r.get("excluded_for_fit")]
        long_count = sum(1 for r in compatible_rows if _votes_long(r))
        total = len(compatible_rows)
        excluded_count = len(sym_rows) - total
        bucket, reason = compute_bucket(
            price_verdict=price_verdict,
            price_reason=ms.get("entry_reason"),
            long_count=long_count,
            total=total,
        )
        # Verdict FUNNEL (owner 10 Aug 2026: "0 BUY — how is that even
        # possible in a market"): record where each symbol's bucket came
        # from and every demotion that moved it, so the digest can show
        # "N technical BUYs -> M final" split by cause — market judgment
        # vs data problems must never be indistinguishable.
        _funnel_demotions: list[str] = []
        _bucket_consensus = bucket

        # Two-tier sentiment demotion via the standalone helper —
        # same logic, now testable in isolation. See
        # apply_sentiment_demotion docstring for the rule chain.
        ss = best.get("sentiment_summary") or {}
        bucket, reason, sentiment_demoted = apply_sentiment_demotion(
            bucket=bucket,
            reason=reason,
            mean=ss.get("mean_sentiment"),
            material_negative_count=ss.get("material_negative_count", 0),
            mean_threshold=mean_threshold,
            min_material=min_material,
        )

        # Swing-strict overlay — tighter rule for medium-long-term
        # carry. Catches the TSLA case where global thresholds let a
        # BUY through at +0.02 mean + 1 material negative; for a
        # multi-week hold the news backdrop matters more than for a
        # 30-minute intraday trade.
        bucket, reason, swing_strict_demoted = apply_swing_strict_demotion(
            bucket=bucket,
            reason=reason,
            mean=ss.get("mean_sentiment"),
            material_negative_count=ss.get("material_negative_count", 0),
        )
        if sentiment_demoted:
            _funnel_demotions.append("sentiment")
        if swing_strict_demoted:
            _funnel_demotions.append("swing_strict_sentiment")
        sentiment_demoted = sentiment_demoted or swing_strict_demoted

        # Horizon-veto + extreme-range demotion. Fixes the QUAL/USMV-
        # at-100th-percentile BUY bug where the strategy consensus was
        # long but every horizon (swing/long-term/passive) screamed
        # AVOID/WATCH. The bucket vote on its own conflates "already
        # in position" with "good time to add" — this rule separates
        # them by reading the swing horizon's entry-timing verdict.
        bucket, reason, horizon_demoted = apply_horizon_and_range_demotion(
            bucket=bucket,
            reason=reason,
            horizon_classification=best.get("horizon_classification"),
            range_pct=best.get("range_pct") or ms.get("range_pct")
                or ms.get("range_position_pct"),
        )

        # Three-tier conviction classification + BUG-001 conviction
        # veto per IMPROVEMENT_SUGGESTIONS_v1.md §1.3. Conviction is
        # computed AFTER all bucket demotions land so it reflects the
        # final verdict's confidence. If conviction comes out LOW and
        # bucket is still BUY (i.e. compute_bucket missed the trend
        # break), the veto caps at WAIT — belt-and-braces on top of
        # the existing trend gate.
        conviction, conviction_reason = compute_conviction(
            bucket=bucket,
            market_state=ms,
            sentiment_demoted=sentiment_demoted,
            horizon_demoted=horizon_demoted,
        )
        bucket, reason, conviction_demoted = cap_bucket_at_low_conviction(
            bucket=bucket, reason=reason, conviction=conviction,
        )
        if horizon_demoted:
            _funnel_demotions.append("horizon_range")
        if conviction_demoted:
            _funnel_demotions.append("low_conviction")

        # Earnings-proximity GATE — session-based 5-state classification
        # (pre-blackout / post-digest = veto, post-drift / unknown = penalty),
        # replacing the old calendar-day suppressor. Runs BEFORE the ATR exit
        # block below: an earnings gap contaminates ATR(14) for ~14 sessions, so
        # a genuine post-earnings decline mislabels as "0.1 ATR above kijun,
        # support hold" unless veto/penalty lands first. Sessions counted on the
        # XNYS calendar from the REAL report dates (upcoming.date / earnings.
        # announce_date). Fail-loud: a missing date is UNKNOWN (penalise+flag),
        # never CLEAR — a stale feed can't launder earnings names into pristine
        # setups. ETFs (no earnings concept) map to CLEAR, not UNKNOWN.
        from .fees import is_known_etf as _is_etf
        earnings_sig = best.get("earnings_signal") or {}
        _eg_cfg = EarningsGateConfig.from_env()
        _has_earnings = not _is_etf(symbol)
        _next_date = (earnings_sig.get("upcoming") or {}).get("date")
        _last_date = (
            earnings_sig.get("last_report_date")  # Finnhub history (this env)
            or (earnings_sig.get("earnings") or {}).get("announce_date")  # yfinance
        )
        _est = bool((earnings_sig.get("upcoming") or {}).get("isEstimate"))
        _s_to = _eg_sessions_to(_next_date) if _next_date else None
        _s_since = _eg_sessions_since(_last_date) if _last_date else None
        # Verified absence ≠ can't-verify (the UBER case, 12 Aug 2026): when
        # BOTH dates are missing, ask the central store whether that absence
        # is AUTHORITATIVE (fresh bulk-harvested calendar, no rows in the
        # horizons) — that CLEARS the gate. A stale/empty store leaves the
        # honest UNKNOWN penalty in place.
        _absence_verified = False
        if _has_earnings and _s_to is None and _s_since is None:
            try:
                from .earnings import earnings_absence_verified
                _absence_verified = earnings_absence_verified(symbol, _resolve_api_base())
            except Exception:  # noqa: BLE001 — store outage = not verified
                _absence_verified = False
        _eg_state = _eg_classify(_s_to, _s_since, _est, _eg_cfg,
                                 has_earnings=_has_earnings,
                                 verified_no_dates=_absence_verified)
        _eg_dec = _eg_route(_eg_state, _eg_cfg, sessions_to_next=_s_to, sessions_since_last=_s_since)
        # Canary resolution (alert-not-suppress): feed degraded + UNKNOWN →
        # crawl the CONFIGURED news feeds for a recent-earnings mention. Positive
        # mention = just reported → hard veto. No mention/outage → the name stays
        # VISIBLE with an EARNINGS_UNVERIFIED alert (capped, never ⭐) telling the
        # user to verify the date manually — never silently suppressed.
        _eg_hint: bool | None = None
        if earnings_feed_degraded and _eg_state == _EarningsGate.UNKNOWN and _has_earnings:
            if symbol not in _news_hint_cache:
                try:
                    from .news_sites import recent_earnings_mention
                    _news_hint_cache[symbol] = recent_earnings_mention(symbol)
                except Exception:  # noqa: BLE001 — crawl outage = unknown, not "no news"
                    _news_hint_cache[symbol] = None
            _eg_hint = _news_hint_cache[symbol]
        _eg_dec = _eg_resolve(_eg_dec, earnings_feed_degraded, recent_earnings_hint=_eg_hint)
        earnings_gate_info = {
            "state": _eg_state.value, "action": _eg_dec.action, "flag": _eg_dec.flag,
            "score_mult": _eg_dec.score_mult, "rank_cap": _eg_dec.rank_cap,
            "reason": _eg_dec.reason, "sessions_to_next": _s_to,
            "sessions_since_last": _s_since,
            "feed_degraded": earnings_feed_degraded,
            "dead_canaries": _dead_canaries or None,
        }
        earnings_suppressed = _eg_dec.action == "veto"
        if _eg_dec.action == "veto":
            if bucket == "BUY":
                bucket = "WAIT"
                _funnel_demotions.append(f"earnings_veto:{_eg_dec.flag or _eg_state.value}")
            if conviction == "HIGH":
                conviction = "MEDIUM"
            reason = f"{reason} | earnings gate: {_eg_dec.reason}" if reason else _eg_dec.reason
        elif _eg_dec.action == "penalize":
            # POST_DRIFT (real recent report, ATR still inflated) caps conviction.
            # UNKNOWN (missing feed) is FLAG-ONLY — a down earnings feed must not
            # silently degrade every name's conviction; the flag + data-gap make
            # the gap visible (fail-loud) without nuking the whole Decide screen.
            # EARNINGS_UNVERIFIED (degraded feed, alert-not-suppress) caps like
            # drift: visible BUY, but never a confident/high-conviction one.
            if conviction == "HIGH" and (
                _eg_state == _EarningsGate.POST_DRIFT
                or _eg_dec.flag == "EARNINGS_UNVERIFIED"
            ):
                conviction = "MEDIUM"
            reason = f"{reason} | {_eg_dec.flag}: {_eg_dec.reason}" if reason else _eg_dec.reason

        # FAIL-VISIBLE data-gap integrity (NO FALSE POSITIVES). A degraded LLM
        # means the rationale / sentiment-scoring / catalyst layers COULDN'T be
        # computed — so the sentiment demotion may have silently not fired. We
        # surface that as a data gap and CAP conviction (HIGH→MEDIUM) rather than
        # let a confident verdict ride on unverified inputs. (earnings/valuation
        # gaps need an asset-class signal not present here — wired later.)
        data_gaps = collect_data_gaps(available={"llm": llm_healthy})
        _bucket_before_gap = bucket
        bucket, conviction, reason, _gap_capped = apply_data_gap_integrity(
            bucket=bucket, conviction=conviction, reason=reason, gaps=data_gaps,
        )
        if _gap_capped and _bucket_before_gap != bucket:
            _funnel_demotions.append("llm_data_gap")

        # Exit framework — ② of the Alpha Engine. Compute stop_loss /
        # take_profit at signal time so the UI / IBKR card has the
        # mandatory exit triad ready without the user doing math.
        # Anchor on the best row's strategy_type so a momentum signal
        # gets momentum defaults when ATR is missing. The exit block
        # is set on every row for this symbol so any row can be
        # rendered in isolation.
        from .exit_framework import (
            build_ibkr_order_instructions,
            compute_exit_levels,
            compute_position_sizing,
            gate_check_rr,
        )
        best_strategy_name = best.get("strategy", "")
        best_strategy_type = strategy_type_for(best_strategy_name)
        entry_price_val = ms.get("last_price")
        exit_levels = compute_exit_levels(
            entry_price=entry_price_val,
            atr_14=ms.get("atr_14"),
            strategy_type=best_strategy_type,
        )
        rr_gate_pass, rr_gate_reason = gate_check_rr(exit_levels)

        # Position sizing + IBKR card. Only meaningful on a BUY
        # bucket — WAIT / AVOID rows don't carry an entry intent.
        # Account size + risk + FX come from env vars with sensible
        # defaults; once the user-facing Settings page exposes them
        # this wiring picks the value from the settings store
        # without touching call sites.
        sizing_dict: dict | None = None
        ibkr_instructions: dict | None = None
        if bucket == "BUY" and exit_levels is not None and entry_price_val:
            try:
                acct = float(os.environ.get("TRADEPRO_ACCOUNT_SIZE_GBP", "10000"))
                risk_pct = float(
                    os.environ.get("TRADEPRO_RISK_PER_TRADE_PCT", "1.0")
                ) / 100.0
                fx = float(os.environ.get("TRADEPRO_FX_GBPUSD", "1.27"))
            except ValueError:
                acct, risk_pct, fx = 10000.0, 0.01, 1.27
            stop_distance_usd = max(
                entry_price_val - exit_levels.stop_loss, 0.0
            )
            sizing = compute_position_sizing(
                entry_price_usd=entry_price_val,
                stop_distance_usd=stop_distance_usd,
                account_size_gbp=acct,
                risk_per_trade_pct=risk_pct,
                fx_rate_gbpusd=fx,
            )
            if sizing is not None:
                sizing_dict = sizing.to_dict()
                ibkr_instructions = build_ibkr_order_instructions(
                    direction="BUY",
                    entry_price=entry_price_val,
                    stop_loss=exit_levels.stop_loss,
                    take_profit=exit_levels.take_profit,
                    quantity=sizing.suggested_shares,
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
                cross_sectional_momentum=best.get("cross_sectional_momentum"),
                valuation_flag=best.get("valuation_flag"),
                swing_score=best.get("swing_score"),
                horizon_classification=best.get("horizon_classification"),
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

        # Look for an active Ichimoku long position on this symbol —
        # if the ichimoku_cloud strategy is currently long and the
        # cloud-targets dict was computed, lift price_target /
        # stop_level / rr_ratio to symbol top-level so the website
        # can render "BUY → $42.50, stop $38.10, R/R 2.3x" alongside
        # the verdict (TRADEPRO sprint §6).
        ichimoku_promote: dict = {}
        for r in sym_rows:
            if r.get("strategy") != "ichimoku_cloud":
                continue
            if not r.get("in_position"):
                continue
            ich = r.get("ichimoku") or {}
            if ich.get("price_target") is None:
                continue
            ichimoku_promote = {
                "price_target": ich.get("price_target"),
                "stop_level": ich.get("stop_level"),
                "rr_ratio": ich.get("rr_ratio"),
                "price_target_source": "ichimoku_cloud",
            }
            break

        # COMPASS alpha score — computed once per symbol, stamped on every
        # row below.  Uses data already assembled in `best`; sector RS and
        # EPS revision are best-effort (None → neutral factor score 5).
        # Wrapped in a broad try/except so a scorer bug never breaks compare.
        _sector_rs_res = None
        try:
            from .compass_scorer import compute_compass_score
            from .sector_rs import compute_sector_rs
            from .eps_tracker import get_eps_revision
            _sector_rs_res = compute_sector_rs(symbol)
            _eps_rev_res = get_eps_revision(symbol)
            _compass_res = compute_compass_score(
                symbol, best,
                sector_rs_result=_sector_rs_res,
                eps_revision=_eps_rev_res,
            )
            _compass_dict = _compass_res.to_dict()
        except Exception:  # noqa: BLE001 — never break compare run
            _compass_dict = None

        # Entry-quality GATE — a technical BUY on a relative-strength LAGGARD or
        # on THIN volume is the low-quality entry a discretionary trader passes on
        # (the ANET case: RS ~2/10 drifting up on ~0.5× volume near its highs).
        # Demote such a BUY to WAIT + cap conviction; a BUY we CAN'T check (missing
        # RS/volume) is capped, never waved through as a clean high-conviction rec.
        # Only ever touches BUY — never upgrades a WAIT/AVOID.
        _rs_score = (_sector_rs_res or {}).get("rs_score") if isinstance(_sector_rs_res, dict) else None
        _eq = evaluate_entry_quality(
            rs_score=_rs_score,
            volume_ratio=ms.get("volume_ratio_20d"),
            cfg=EntryQualityConfig.from_env(),
        )
        entry_quality_info = _eq.to_dict()
        if bucket == "BUY":
            if _eq.action == "veto":
                bucket = "WAIT"
                _funnel_demotions.append("entry_quality")
                reason = f"{reason} · entry-quality veto: {entry_quality_info['summary']}"
                if conviction == "HIGH":
                    conviction = "MEDIUM"
            elif _eq.action == "flag_missing" and conviction == "HIGH":
                conviction = "MEDIUM"

        # Copy bucket + reason + sentiment-demoted flag + rationale onto
        # every row for this symbol so the frontend can render any row's
        # expand panel without re-deriving.
        for r in sym_rows:
            r["bucket"] = bucket
            r["bucket_reason"] = reason
            r["verdict_funnel"] = {
                "technical": price_verdict,
                "consensus": _bucket_consensus,
                "final": bucket,
                "demotions": list(_funnel_demotions),
            }
            r["sentiment_demoted"] = sentiment_demoted
            # Factor-fit metadata so the UI / MCP can render
            # "N of M currently long (X strategies excluded for fit)"
            # alongside the consensus line and the leaderboard can
            # grey out incompatible rows.
            r["consensus_compatible_count"] = total
            r["consensus_excluded_count"] = excluded_count
            r["consensus_excluded_strategies"] = excluded_strategies
            # Combined verdict — fuses technical bucket + catalyst
            # overlay + analyst flow into a single annotated rec.
            # Phase 17.5 of the catalyst sprint. Computed AFTER all
            # bucket demotions land so the technical layer reflects
            # the final verdict (sentiment + horizon already applied).
            try:
                r["combined_verdict"] = derive_combined_verdict(r)
            except Exception:  # noqa: BLE001 — best-effort; row still ships
                r["combined_verdict"] = None
            # Horizon / range demotion flag surfaced separately so the
            # UI can show "BUY → WAIT because the swing horizon said
            # AVOID at the 100th percentile" instead of just "WAIT".
            r["horizon_demoted"] = horizon_demoted
            # Conviction classification + BUG-001 veto flag. UI uses
            # conviction to gate the BUY badge — LOW caps at WATCH,
            # INVALID blocks display entirely.
            r["conviction"] = conviction
            r["conviction_reason"] = conviction_reason
            r["conviction_demoted"] = conviction_demoted
            # Entry-quality gate result (RS + volume floors) — surfaced so the
            # card/digest can show "BUY → WAIT: weak RS 2/10, thin volume 0.5×"
            # and never present a laggard/thin entry as a clean BUY.
            r["entry_quality"] = entry_quality_info
            # Surfaced data gaps ("couldn't compute" checks) so the Decide UI
            # shows "BUY (unverified: …)" instead of a clean confident verdict.
            r["data_gaps"] = gap_labels(data_gaps)
            # Earnings suppression flag — UI shows a WARNING badge on
            # the card when set, even if the bucket didn't actually
            # change (already WAIT). days_until carried for the
            # tooltip "earnings in Nd".
            r["earnings_suppressed"] = earnings_suppressed
            # Session-based earnings-proximity gate: state (CLEAR/PRE_BLACKOUT/
            # POST_DIGEST/POST_DRIFT/UNKNOWN), action, flag (EARNINGS_DRIFT/
            # UNKNOWN), score/rank penalty — surfaced so the digest/scanner can
            # show "reported 4 sessions ago — drift, ×0.5" and rank-cap it.
            r["earnings_gate"] = earnings_gate_info
            r["earnings_proximity_days"] = earnings_gate_info.get("sessions_to_next")
            # News context block — ⑤ of the Alpha Engine. Reshapes the
            # existing sentiment_summary + news + earnings fields into
            # the SIGNAL_CARD_SPEC §2.3 / §3 shape. Pure transformation,
            # no new network calls; GDELT integration is a follow-on
            # that swaps the data source without changing this wiring.
            from .news_context import compute_news_context
            nc = compute_news_context(
                sentiment_summary=r.get("sentiment_summary"),
                news_items=r.get("news"),
                earnings_proximity_days=earnings_gate_info.get("sessions_to_next"),
            )
            r["news_context"] = nc.to_dict()
            # News CATALYST signal — the missing half of "news in signals":
            # sentiment only DEMOTES; this SURFACES event-driven names (a news-
            # volume spike + its sentiment direction — the oil-breakout case a
            # technically-quiet Ichimoku read misses). Flag only, never flips the
            # verdict to BUY (discretionary entry). Uses the counts news_context
            # just computed — no extra network/LLM call.
            _cat = detect_news_catalyst(
                articles_today=r["news_context"].get("article_count_today"),
                articles_30d_avg=r["news_context"].get("article_count_30d_avg"),
                mean_sentiment=(r.get("sentiment_summary") or {}).get("mean_sentiment"),
                cfg=CatalystConfig.from_env(),
            )
            r["catalyst_signal"] = _cat.to_dict()
            if _cat.active:
                r["catalyst_flag"] = f"CATALYST_{_cat.direction.upper()}"
            # Exit framework block per SIGNAL_CARD_SPEC_v1.md §3. Carry
            # stop / target / RR alongside the verdict so the UI /
            # MCP / IBKR-order-instructions panel can render the
            # "what to type" card without re-deriving.
            r["exit"] = exit_levels.to_dict() if exit_levels else None
            r["rr_gate"] = {
                "passed": rr_gate_pass,
                "reason": rr_gate_reason,
                "floor": 2.0,
            }
            r["sizing"] = sizing_dict
            r["ibkr_order_instructions"] = ibkr_instructions
            # Coherence enforcement (BUG-002 fix per
            # IMPROVEMENT_SUGGESTIONS_v1.md §1.3 + §4) — extracted
            # to enforce_coherence() so the contract is unit-testable
            # in isolation.
            enforce_coherence(
                r,
                bucket=bucket,
                sentiment_demoted=sentiment_demoted,
                horizon_demoted=horizon_demoted,
            )
            if rationale_dict is not None:
                r["rationale"] = rationale_dict
            # Top-level price target keys land on every row of this
            # symbol so the website can read them regardless of which
            # strategy row is in focus. Missing = no active ichimoku
            # signal, frontend renders without the target sub-row.
            if ichimoku_promote:
                r["price_target"] = ichimoku_promote["price_target"]
                r["stop_level"] = ichimoku_promote["stop_level"]
                r["rr_ratio"] = ichimoku_promote["rr_ratio"]
                r["price_target_source"] = ichimoku_promote["price_target_source"]
            # COMPASS multi-factor alpha score — computed once per symbol
            # above, stamped on every row so any strategy row carries it.
            r["compass_score"] = _compass_dict.get("score") if _compass_dict else None
            r["compass_signal"] = _compass_dict.get("signal") if _compass_dict else None
            r["compass_conviction"] = _compass_dict.get("conviction") if _compass_dict else None
            r["compass_breakdown"] = _compass_dict if _compass_dict else None


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
    news_fallback_cache: dict[str, str | None] = {}
    scored_news_cache: dict[str, list[ScoredHeadline]] = {}
    analyst_actions_cache: dict[str, dict | None] = {}
    analyst_recs_cache: dict[str, dict | None] = {}
    sentiment_summary_cache: dict[str, SentimentSummary] = {}
    sentiment_status_cache: dict[str, str] = {}
    # Family-4 (event-driven): post-earnings beat-and-retreat per symbol.
    # Best-effort — yfinance fetch failure produces a no-signal envelope,
    # never blocks the run.
    earnings_signal_cache: dict[str, dict] = {}
    # Historical earnings dates per symbol — feeds the chart's earnings-
    # marker overlay so the user can spot event-driven moves on the
    # price line. ETFs return empty (no earnings), failure returns
    # empty, missing key on a row → frontend renders no markers.
    earnings_history_cache: dict[str, list[dict]] = {}
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
    # Sentiment provider — configurable model via TRADEPRO_SENTIMENT_MODEL. Unset
    # → use the standard provider (no latency change; the symbol-aware prompt still
    # improves scoring). Set to e.g. "gemma3:12b" for the higher-quality symbol-aware
    # scoring proven to fix the +ve-news-scored-negative mis-reads (NOVN trial win,
    # Meta–Reliance partnership) — opt-in because gemma is ~6s/headline vs ~1.3s.
    _sent_model = os.environ.get("TRADEPRO_SENTIMENT_MODEL", "").strip()
    sentiment_llm = llm
    if _sent_model:
        try:
            from .llm.ollama_provider import OllamaProvider as _OllProv
            _cand = _OllProv(model=_sent_model)
            sentiment_llm = _cand if _cand.healthy else llm
        except Exception:  # noqa: BLE001
            sentiment_llm = llm
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
                # A provider occasionally serves a real trading day with a
                # NaN close (confirmed live: SPY 2026-07-28) rather than
                # omitting the row entirely. Left in, it silently nulls
                # cagr_pct/final_equity (run_backtest reads equity.iloc[-1]
                # off this series) and last_price/sma_200 (market_state) —
                # while 52w-high/low and RSI keep working since those skip
                # NaN by default. Same "don't propagate garbage" principle
                # market_state.py already applies to isolated price spikes;
                # scoped to this compare/digest pipeline only, not the
                # shared ensure_cached() cache read (live strategies read
                # that directly and get their own review separately).
                _close_col = "adj_close" if "adj_close" in price_cache[symbol].columns else "close"
                if _close_col in price_cache[symbol].columns:
                    price_cache[symbol] = price_cache[symbol][price_cache[symbol][_close_col].notna()]
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
            from .news import fetch_news_with_fallback
            items, fallback_used = fetch_news_with_fallback(symbol)
            news_cache[symbol] = items
            news_fallback_cache[symbol] = fallback_used
            # Family-4: beat-and-retreat. ETFs don't have earnings
            # (they're funds, not companies), so skip them entirely
            # — saves a yfinance call per ETF and stops the noisy
            # "No earnings dates found, symbol may be delisted"
            # warning yfinance emits for every fund. Only stocks
            # get the BEAT_AND_RETREAT classification.
            from .fees import is_known_etf
            if is_known_etf(symbol):
                earnings_signal_cache[symbol] = {
                    "_source": f"live://earnings/{symbol}",
                    "fired": False,
                    "verdict": "NOT_APPLICABLE",
                    "reason": "ETF — earnings signals are stock-only",
                }
                # ETFs never have earnings — explicit empty list so the
                # frontend renders zero markers (no defensive `?? []`).
                earnings_history_cache[symbol] = []
            else:
                try:
                    from .earnings import (
                        beat_and_retreat_signal,
                        fetch_earnings_in_range,
                        fetch_upcoming_earnings,
                    )
                    sig = beat_and_retreat_signal(
                        symbol, price_cache[symbol],
                    )
                    # Historical earnings dates (~5y) for chart markers.
                    # Shares the same yfinance ticker yfinance just hit
                    # for beat_and_retreat_signal, so the underlying
                    # data is already cached in yfinance's request layer.
                    try:
                        earnings_history_cache[symbol] = fetch_earnings_in_range(
                            symbol, lookback_days=1825,
                        )
                    except Exception as e:  # noqa: BLE001 — best-effort
                        if logger:
                            logger.emit("compare.earnings_history_failed",
                                        symbol=symbol, error=str(e))
                        earnings_history_cache[symbol] = []
                    # Most recent PAST report from the Finnhub earnings history —
                    # feeds the earnings-proximity gate's post-report (digest /
                    # drift) side when yfinance has no recent date (e.g. this env).
                    try:
                        import datetime as _d0
                        _today0 = _d0.date.today()
                        _past = [
                            str(e.get("date"))[:10]
                            for e in (earnings_history_cache.get(symbol) or [])
                            if e.get("date")
                            and _d0.date.fromisoformat(str(e["date"])[:10]) <= _today0
                        ]
                        if _past:
                            sig["last_report_date"] = max(_past)
                    except Exception:  # noqa: BLE001 — best-effort, gate falls back to UNKNOWN
                        pass
                    # Central-store fallback for the post-report side: when
                    # yfinance history had no past date, the bulk-harvested
                    # calendar may still know the last report (it spans
                    # back ~30d) — keeps POST_DIGEST/DRIFT working through
                    # yfinance outages.
                    if not sig.get("last_report_date"):
                        try:
                            from .earnings import last_report_from_store
                            _store_last = last_report_from_store(
                                symbol, _resolve_api_base())
                            if _store_last:
                                sig["last_report_date"] = _store_last
                        except Exception:  # noqa: BLE001 — best-effort
                            pass
                    # Attach the next upcoming earnings (Finnhub) so
                    # the digest can warn about position-into-earnings
                    # volatility. Off-by-default: returns None when
                    # Finnhub isn't configured. Best-effort, never
                    # blocks the run.
                    api_base = _resolve_api_base()
                    upcoming = fetch_upcoming_earnings(symbol, api_base)
                    if upcoming:
                        sig["upcoming"] = upcoming
                    earnings_signal_cache[symbol] = sig
                    # Analyst upgrade/downgrade actions — same Finnhub
                    # plumbing. Off-by-default when FINNHUB_API_KEY
                    # isn't set on the API box; returns None and the
                    # row simply omits the analyst_actions field.
                    try:
                        from .analyst_actions import (
                            fetch_analyst_actions,
                            fetch_analyst_recommendations,
                        )
                        analyst_actions_cache[symbol] = fetch_analyst_actions(
                            symbol, api_base,
                        )
                        # Recommendation trends — monthly buy/hold/sell
                        # counts. Free-tier alternative when the
                        # paid-tier upgrade-downgrade events come back
                        # empty.
                        analyst_recs_cache[symbol] = fetch_analyst_recommendations(
                            symbol, api_base,
                        )
                    except Exception as e:  # noqa: BLE001 — best-effort
                        if logger:
                            logger.emit("compare.analyst_actions_failed",
                                        symbol=symbol, error=str(e))
                        analyst_actions_cache[symbol] = None
                        analyst_recs_cache[symbol] = None
                except Exception as e:  # noqa: BLE001
                    if logger:
                        logger.emit("compare.earnings_failed", symbol=symbol, error=str(e))
                    earnings_signal_cache[symbol] = {
                        "_source": f"live://earnings/{symbol}",
                        "fired": False, "verdict": "NO_RECENT",
                    }
                    # Also seed history with empty so the row builder
                    # never has a missing key.
                    earnings_history_cache.setdefault(symbol, [])
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
                    news_cache[symbol], sentiment_llm,
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
        earnings_signal = earnings_signal_cache.get(symbol, {})
        earnings_history = earnings_history_cache.get(symbol, [])
        news_via = news_fallback_cache.get(symbol)
        analyst_actions = analyst_actions_cache.get(symbol)
        analyst_recs = analyst_recs_cache.get(symbol)
        for strat in strategies:
            row = _row_for(symbol, strat, prices, state, consensus,
                           fundamentals, news, scored_news,
                           sentiment_summary, sentiment_status, end, cfg,
                           earnings_history=earnings_history,
                           news_via=news_via)
            # Attach the per-symbol earnings signal to every (symbol,
            # strategy) row — same pattern as market_state. Family-4
            # is symbol-level not strategy-level.
            if earnings_signal:
                row["earnings_signal"] = earnings_signal
            # Analyst actions — same shape rule. None when Finnhub is
            # disabled or the symbol has no recent activity; the
            # renderer hides the section when missing.
            if analyst_actions:
                row["analyst_actions"] = analyst_actions
            if analyst_recs:
                row["analyst_recommendations"] = analyst_recs
            rows.append(row)

    rows.sort(key=lambda r: _rank_value(r, cfg.rank_metric), reverse=True)
    for i, row in enumerate(rows, start=1):
        row["rank"] = i

    # Cross-basket signals (Family-2 + Family-3) computed BEFORE the
    # bucket/rationale attach so the rationale layer can quote them.
    # Family 3 (cross-sectional momentum): rank + zscore vs basket
    # peers on 12-month return.
    # Family 2 (valuation): cheap/fair/expensive — uses P/E quartile
    # for stock baskets (lower P/E = cheaper) and falls back to
    # dividend-yield quartile for ETF baskets where P/E isn't
    # reported. The hybrid orchestrator picks per basket so growth
    # stocks like NVDA aren't mislabeled "expensive" purely because
    # they don't pay a dividend.
    from .cross_sectional import (
        bucket_by_valuation,
        cross_basket_trace_rows,
        rank_by_momentum,
    )
    momentum_inputs = {
        r["symbol"]: (r.get("market_state") or {}).get("momentum_12m_pct")
        for r in rows
    }
    cs_ranks = rank_by_momentum(momentum_inputs)
    yield_inputs = {
        r["symbol"]: (r.get("fundamentals") or {}).get("dividend_yield_pct")
        for r in rows
    }
    pe_inputs = {
        r["symbol"]: (r.get("fundamentals") or {}).get("forward_pe")
            or (r.get("fundamentals") or {}).get("trailing_pe")
        for r in rows
    }
    val_flags = bucket_by_valuation(pe_inputs, yield_inputs)
    # Per-symbol consensus needs a count over ALL strategy rows for
    # this symbol — compute once per symbol so the swing scorer
    # below sees long_count even on rows beyond rank 1.
    symbol_long_counts: dict[str, dict] = {}
    for r in rows:
        sym = r["symbol"]
        if sym not in symbol_long_counts:
            symbol_rows = [x for x in rows if x["symbol"] == sym]
            symbol_long_counts[sym] = {
                "long": sum(1 for x in symbol_rows if x.get("in_position")),
                "total": len(symbol_rows),
            }

    for r in rows:
        cs = cs_ranks.get(r["symbol"])
        val = val_flags.get(r["symbol"])
        r["cross_sectional_momentum"] = cs
        r["valuation_flag"] = val
        # Inject long_count + total_strategies for the swing scorer
        # below; bucket-vote already attaches them per symbol but
        # not per row.
        counts = symbol_long_counts.get(r["symbol"], {})
        r.setdefault("long_count", counts.get("long"))
        r.setdefault("total_strategies", counts.get("total"))
        # Append Family-2/3/4 signals to the decision_trace so they
        # show up as first-class checks in the Compare expand panel's
        # "Why the verdict" ladder. The rationale's rule_chain reads
        # the same field, so the LLM sees them too.
        ms = r.get("market_state") or {}
        existing_trace = list(ms.get("decision_trace") or [])
        cb_rows = cross_basket_trace_rows(cs, val)
        # Family-4 trace row from the earnings signal (when one fires).
        from .earnings import earnings_trace_row
        ev_row = earnings_trace_row(r.get("earnings_signal"))
        appended = list(cb_rows)
        if ev_row:
            appended.append(ev_row)
        if appended:
            ms["decision_trace"] = existing_trace + appended
            r["market_state"] = ms

    # LLM catalyst seat (2026-06-13) — the news-reasoning layer the swing event
    # score reads. Judges whether each symbol's headlines carry a GENUINE,
    # symbol-specific catalyst (a sentiment mean can't: TSLA's SpaceX-IPO +0.7
    # dilutes to a -0.15 mean). Computed once per symbol (memoised + disk-cached
    # by headline-hash), attached BEFORE the swing scorer. Conservative: only a
    # STRONG grounded catalyst nudges the event layer +1 downstream, never
    # decisive alone. Best-effort — degrades to neutral, never blocks the run.
    from .catalyst_llm import judge_catalyst
    from .fees import is_known_etf
    _catalyst_by_symbol: dict[str, dict | None] = {}
    for r in rows:
        sym = r.get("symbol")
        if sym not in _catalyst_by_symbol:
            # ETFs don't have single-name catalysts (they're baskets, not
            # companies) — skip the LLM call entirely: it's wasted work and
            # adds Ollama contention with the sentiment + rationale calls.
            if is_known_etf(sym):
                _catalyst_by_symbol[sym] = None
            else:
                try:
                    # No provider= → judge_catalyst uses its stronger catalyst
                    # model (gemma3:12b), not the fast 8b used for bulk sentiment.
                    _catalyst_by_symbol[sym] = judge_catalyst(sym, r.get("news"))
                except Exception as e:  # noqa: BLE001
                    if logger:
                        logger.emit("compare.catalyst_failed", symbol=sym, error=str(e))
                    _catalyst_by_symbol[sym] = None
        r["llm_catalyst"] = _catalyst_by_symbol[sym]

    # Phase-X composite swing-trade scorer (0-8 across four families).
    # Computed AFTER all signal annotations are attached so each layer
    # sees the same row shape the rationale and email digest see.
    from .swing import evaluate_swing
    for r in rows:
        try:
            r["swing_score"] = evaluate_swing(r).to_dict()
        except Exception as e:  # noqa: BLE001
            if logger:
                logger.emit("compare.swing_scorer_failed",
                            symbol=r.get("symbol"), error=str(e))
            r["swing_score"] = None

    # Horizon Classification Engine (TRADEPRO-SPEC-001 §6.2).
    # Runs LAST — needs swing_score (event layer → has_catalyst),
    # valuation_flag, cross_sectional_momentum and the market_state
    # decision_trace already attached. Output is a sibling field, not
    # a modifier on existing fields, so the bucket vote is unchanged.
    from .horizons import classify_horizons
    for r in rows:
        try:
            hz = classify_horizons(r)
            r["horizon_classification"] = hz.to_dict()
            # Also surface range_pct at row top-level so the frontend
            # can read it without descending into the nested object.
            if hz.range_pct is not None:
                r.setdefault("range_pct", hz.range_pct)
        except Exception as e:  # noqa: BLE001
            if logger:
                logger.emit("compare.horizons_failed",
                            symbol=r.get("symbol"), error=str(e))
            r["horizon_classification"] = None

    # Risk rating (Phase R). Runs LAST — sees the bucket vote, range
    # position, sentiment summary, cross-basket z, all attached. Output
    # is a sibling field carrying rating + audit trail (factors list)
    # so every surface (dashboard / email / PDF / MCP) can render the
    # same auditable rationale instead of a black-box pill.
    from .risk import compute_risk_rating
    for r in rows:
        try:
            r["risk_rating"] = compute_risk_rating(r).to_dict()
        except Exception as e:  # noqa: BLE001
            if logger:
                logger.emit("compare.risk_failed",
                            symbol=r.get("symbol"), error=str(e))
            r["risk_rating"] = None

    # Gem hunter (Phase G). Annotation only — surfaces names that
    # match the contrarian profile (down ≥25% from 5y peak, in lower
    # quartile of 52w range, CHEAP valuation, recovery signal firing,
    # sentiment not hostile). Surfaces alongside the existing bucket
    # vote so the user gets the trend-following AND mean-reversion
    # lens on the same data.
    #
    # Also evaluates the v2 exit framework on every row (not just
    # gems) so a position the user already holds in a non-gem name
    # can still trigger RECLASSIFIED / THESIS_BROKEN signals. The
    # GemsCard renders these alongside the entry verdict.
    from .gems import evaluate_gem, evaluate_gem_exit
    for r in rows:
        try:
            r["gem_verdict"] = evaluate_gem(r).to_dict()
        except Exception as e:  # noqa: BLE001
            if logger:
                logger.emit("compare.gems_failed",
                            symbol=r.get("symbol"), error=str(e))
            r["gem_verdict"] = None
        try:
            r["gem_exit_verdict"] = evaluate_gem_exit(r).to_dict()
        except Exception as e:  # noqa: BLE001
            if logger:
                logger.emit("compare.gem_exit_failed",
                            symbol=r.get("symbol"), error=str(e))
            r["gem_exit_verdict"] = None

    # Per-symbol bucket computation + rationale generation. Both are
    # symbol-level (not per-row) so we compute once and copy onto every
    # row for that symbol — matches what the frontend was already doing
    # client-side, but now visible in the JSON payload too. Cross-basket
    # signals are now in the row dict so gather_facts can pull them.
    _attach_bucket_and_rationale(
        rows, settings.mean_sentiment_threshold,
        settings.min_material_negative_count, logger=logger,
        llm_healthy=llm_healthy,
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
                # Stronger tier: any bucket → AVOID when news flow is
                # materially negative (AMZN-class). Hardcoded for now;
                # add to remote_settings when the user wants to tune.
                "avoid_mean_threshold": -0.45,
                "avoid_min_material_negative_count": 3,
                "source": settings.source,         # "api" or "defaults"
                "settings_updated_at": settings.updated_at,
                "description": (
                    f"BUY → WAIT when {settings.lookback_days}-day rolling "
                    f"mean sentiment ≤ {settings.mean_sentiment_threshold} "
                    f"AND ≥ {settings.min_material_negative_count} "
                    f"material-negative headlines. "
                    f"Any → AVOID when mean ≤ -0.45 AND ≥ 3 material-"
                    f"negative headlines (separates 'news backdrop is "
                    f"bad' from 'news flow is genuinely hostile')."
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
        # Run-level earnings-feed health. Per-row row["earnings_gate"]["feed_
        # degraded"] already carries this (set inside _attach_bucket_and_
        # rationale, a DIFFERENT function — earnings_feed_degraded/
        # _dead_canaries are its locals, not visible here; a first version
        # of this referenced them directly and crashed every single compare
        # run with NameError, silently, for hours), but buried inside 40+
        # individual rows it reads like organic per-symbol signal rather
        # than the one outage it actually is — this lets a consumer (the
        # digest email, the UI) render ONE top-line summary instead of
        # reconstructing it by counting rows. Same value on every row in a
        # single run (computed once), so checking any one row is correct.
        "earnings_feed_degraded": any(
            (r.get("earnings_gate") or {}).get("feed_degraded") for r in rows
        ),
        "dead_canaries": next(
            (
                (r.get("earnings_gate") or {}).get("dead_canaries")
                for r in rows
                if (r.get("earnings_gate") or {}).get("dead_canaries")
            ),
            None,
        ),
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

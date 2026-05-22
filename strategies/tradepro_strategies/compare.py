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


def compute_bucket(
    *,
    price_verdict: str,
    price_reason: str | None,
    long_count: int,
    total: int,
) -> tuple[str, str]:
    """Pure helper: roll the now-or-wait verdict + the per-strategy
    long/flat votes into a single bucket (BUY/WAIT/AVOID) and a
    one-line reason. Sentiment demotion is layered on by callers that
    have news data; this helper stays sentiment-free so on-demand
    paths (the MCP `evaluate_symbols` tool) can use it without paying
    the news-fetching latency.

    BUY requires price_verdict == "BUY" — when market_state has
    decided HOLD/WAIT/AVOID, those carry through regardless of how
    many strategies are still long. The earlier "HOLD + majority
    long → BUY" promotion conflated "already in position" with
    "good time to add" and was responsible for the MTUM / VLUE /
    QUAL / USMV class of contradictions (bucket=BUY while the same
    row's entry_signal=HOLD at 96-100th-pctile of 52w range).

    A confident BUY also requires majority-strategy long — otherwise
    only one or two strategies have an edge here and we WAIT for
    broader confirmation.
    """
    majority_long = long_count > total / 2 if total > 0 else False
    if price_verdict == "AVOID":
        return "AVOID", price_reason or "Confirmed downtrend."
    if price_verdict == "WAIT":
        return "WAIT", price_reason or "Better entries likely soon."
    if price_verdict == "BUY":
        if majority_long:
            return (
                "BUY",
                price_reason
                or f"{long_count} of {total} strategies currently long; "
                   f"price action supports entry.",
            )
        return (
            "WAIT",
            f"Price-action gate passes but only {long_count} of {total} "
            f"strategies are long — wait for broader confirmation.",
        )
    # price_verdict == "HOLD" (or anything else we didn't model) →
    # never BUY. HOLD means "no fresh entry edge per market_state".
    # If you're already long, the per-strategy in_position state
    # tells you that; the bucket should not say BUY.
    if majority_long:
        consensus = f"{long_count} of {total} strategies currently long"
        if price_reason:
            return ("WAIT", f"{consensus} but {price_reason} — no fresh entry edge.")
        return ("WAIT", f"{consensus} but no fresh entry edge per market_state.")
    return (
        "WAIT",
        f"Only {long_count} of {total} strategies are currently long "
        f"— wait for more confirmation.",
    )


def compute_conviction(
    *,
    bucket: str,
    market_state: dict,
    sentiment_demoted: bool,
    horizon_demoted: bool,
) -> tuple[str, str]:
    """Three-tier conviction classification per
    IMPROVEMENT_SUGGESTIONS_v1.md §1.3 — the safety net the spec adds
    on top of the bucket pipeline. Returns (conviction, reason).

    Decision tree (first match wins):
      - trend filters failing → LOW (regardless of bucket; the BUG-001
        belt-and-braces — even if compute_bucket regresses, a BUY on a
        broken trend gets demoted to WATCH downstream).
      - sentiment or horizon demotion fired → MEDIUM (the system
        adjusted away from the raw price signal; conviction follows).
      - bucket = BUY with volume confirmation → HIGH.
      - bucket = BUY without volume confirmation → MEDIUM.
      - bucket = WAIT/AVOID and trend ok → MEDIUM (these aren't entry
        recommendations, but conviction in the *avoid* call is still
        normal-confidence when filters agree).

    Trend filters: pass when above_sma_200 AND
    ichimoku_cloud_position != BELOW_CLOUD. Missing data fails open
    (treat as trend OK) so we don't punish symbols whose ichimoku
    series hasn't computed yet — the bucket pipeline itself catches
    the actual price-below-SMA200 case via the existing trend gate
    (task #70).

    Volume confirmation: volume_ratio_20d >= 1.2 (a fifth above the
    20-day average). Missing → treated as "not confirmed" so we cap
    at MEDIUM rather than promoting to HIGH on thin data.
    """
    above_sma_200 = market_state.get("above_sma_200")
    ichi_pos = (market_state.get("ichimoku_cloud_position") or "").upper()
    # Only fail trend when we have evidence either way; missing data
    # is not the same as "trend broken".
    sma_breaks_trend = above_sma_200 is False
    ichi_breaks_trend = ichi_pos == "BELOW_CLOUD"
    trend_broken = sma_breaks_trend or ichi_breaks_trend
    if trend_broken:
        bits = []
        if sma_breaks_trend:
            bits.append("price below 200d SMA")
        if ichi_breaks_trend:
            bits.append("below Ichimoku cloud")
        return ("LOW", f"Trend filters failing: {' + '.join(bits)}.")
    if sentiment_demoted:
        return ("MEDIUM", "Sentiment demotion fired — verdict moved off raw price signal.")
    if horizon_demoted:
        return ("MEDIUM", "Horizon / range demotion fired — verdict adjusted by horizon view.")
    if bucket == "BUY":
        vol_ratio = market_state.get("volume_ratio_20d")
        try:
            confirmed = vol_ratio is not None and float(vol_ratio) >= 1.2
        except (TypeError, ValueError):
            confirmed = False
        if confirmed:
            return ("HIGH", f"Trend + consensus + volume confirm (vol_ratio={vol_ratio:.2f}).")
        return ("MEDIUM", "Trend + consensus agree; volume confirmation absent or thin.")
    return ("MEDIUM", "Trend filters pass; bucket is WAIT/AVOID so no entry recommendation.")


def cap_bucket_at_low_conviction(
    *,
    bucket: str,
    reason: str,
    conviction: str,
) -> tuple[str, str, bool]:
    """If conviction == LOW and bucket would otherwise read as a BUY,
    demote bucket → WAIT. Per IMPROVEMENT_SUGGESTIONS_v1.md §1.3, LOW
    conviction means "WATCH only — no entry recommendation". Returns
    (bucket, reason, demoted).

    Pure function — no row mutation. Tested directly in
    features/conviction.feature so the contract is auditable
    independent of the wider compare flow.
    """
    if conviction == "LOW" and bucket == "BUY":
        return (
            "WAIT",
            ("BUG-001 conviction veto: trend filters failing, bucket "
             "capped at WAIT (was BUY). Original reason: " + (reason or "—")),
            True,
        )
    return bucket, reason, False


def apply_earnings_suppressor(
    *,
    bucket: str,
    reason: str,
    conviction: str,
    days_until_earnings: int | None,
    threshold_days: int = 7,
) -> tuple[str, str, str, bool]:
    """Suppress entry recommendations on swings into earnings.

    Per IMPROVEMENT_SUGGESTIONS_v1.md §2.2 + SIGNAL_CARD_SPEC_v1.md §2.2:
    when an earnings announcement lands within `threshold_days`, a
    swing BUY isn't actionable — the post-print gap can swallow the
    entire reward leg of a 1:2 setup. We don't try to predict the
    beat/miss; we just refuse to call BUY into the event window.

    Returns (bucket, reason, conviction, suppressed). Demotes:
      - bucket BUY → WAIT (no entry recommendation)
      - conviction HIGH → MEDIUM (only HIGH gets demoted; LOW stays LOW)

    Pure function — no row mutation. Tested directly in
    features/earnings_suppressor.feature so the threshold semantics
    stay auditable.
    """
    if days_until_earnings is None:
        return bucket, reason, conviction, False
    try:
        days = int(days_until_earnings)
    except (TypeError, ValueError):
        return bucket, reason, conviction, False
    if days < 0 or days > threshold_days:
        return bucket, reason, conviction, False

    new_bucket = "WAIT" if bucket == "BUY" else bucket
    new_conviction = "MEDIUM" if conviction == "HIGH" else conviction
    if new_bucket == bucket and new_conviction == conviction:
        # Nothing changed — bucket was already WAIT / AVOID and
        # conviction wasn't HIGH. Still mark suppressed so the UI can
        # surface the WARNING flag.
        return bucket, reason, conviction, True

    suppress_note = (
        f"Earnings suppression: earnings in {days}d (threshold "
        f"{threshold_days}d). Original verdict: {bucket}. Post-print gap "
        "risk swamps the reward leg of a 1:2 setup."
    )
    return new_bucket, suppress_note, new_conviction, True


def enforce_coherence(
    row: dict,
    *,
    bucket: str,
    sentiment_demoted: bool,
    horizon_demoted: bool,
) -> None:
    """Mutate `row` in-place so `market_state.entry_signal` agrees
    with the final `bucket`, and surface a top-level `coherence`
    block. The raw price-action signal is preserved as
    `market_state.raw_entry_signal` for the decision trace.

    BUG-002 surface fix per IMPROVEMENT_SUGGESTIONS_v1.md §1.3 + §4:
    on every shipped row the two fields are equal by construction
    (the panel's `coherence_check` resolver compares them directly),
    while the `coherence.supersede_reason` field labels *why* the
    raw signal was overridden — sentiment_demotion, horizon_demotion,
    or consensus_or_factor_fit when neither of those fired but the
    bucket vote still moved away from the raw price verdict (e.g.
    not enough strategies are long to promote HOLD→BUY).
    """
    ms_dict = row.get("market_state") or {}
    raw_entry_sig = ms_dict.get("entry_signal")
    supersede_reason: str | None
    if raw_entry_sig and raw_entry_sig != bucket:
        if sentiment_demoted:
            supersede_reason = "sentiment_demotion"
        elif horizon_demoted:
            supersede_reason = "horizon_demotion"
        else:
            supersede_reason = "consensus_or_factor_fit"
        ms_dict["raw_entry_signal"] = raw_entry_sig
        ms_dict["entry_signal"] = bucket
        ms_dict["entry_signal_superseded_by"] = bucket
        ms_dict["entry_signal_note"] = (
            f"Raw price signal was {raw_entry_sig}; final verdict "
            f"is {bucket} (reason: {supersede_reason})."
        )
        row["market_state"] = ms_dict
    else:
        supersede_reason = None
    final_entry_sig = ms_dict.get("entry_signal", bucket)
    row["coherence"] = {
        "today_bucket": bucket,
        "entry_signal": final_entry_sig,
        "raw_entry_signal": ms_dict.get("raw_entry_signal", raw_entry_sig),
        "consistent": final_entry_sig == bucket,
        "supersede_reason": supersede_reason,
    }


def apply_sentiment_demotion(
    *,
    bucket: str,
    reason: str,
    mean: float | None,
    material_negative_count: int | None,
    mean_threshold: float = -0.30,
    min_material: int = 2,
    avoid_mean_threshold: float = -0.45,
    avoid_min_material: int = 3,
) -> tuple[str, str, bool]:
    """Two-tier sentiment demotion. Returns (bucket, reason, demoted).

    Tier 1 — STRONGER (any → AVOID): mean ≤ -0.45 AND ≥3 material-
    negative headlines. News flow is materially worse than a routine
    WAIT — separates "negative backdrop" (Tier 2) from "genuinely
    hostile" (Tier 1). Fires regardless of starting bucket so a BUY
    or WAIT both land in AVOID when the news is this bad.

    Tier 2 — STANDARD (BUY → WAIT): mean ≤ -0.30 AND ≥2 material-
    negative headlines. The original demotion rule kept for backwards
    compatibility — flagging a BUY when sentiment is bad enough to
    warrant sitting out, but not bad enough to call AVOID.

    Pure function — no side effects, no row mutation. Tested
    directly in features/sentiment_demotion.feature so each tier's
    behaviour is auditable independent of the wider compare flow.
    """
    mat_neg = material_negative_count or 0
    if (mean is not None
            and mean <= avoid_mean_threshold
            and mat_neg >= avoid_min_material
            and bucket != "AVOID"):
        return (
            "AVOID",
            (f"Sentiment demotion to AVOID: 7d mean {mean:.2f} ≤ "
             f"{avoid_mean_threshold} AND {mat_neg} material-negative "
             f"headlines (≥ {avoid_min_material}) — news flow is "
             f"materially worse than a routine WAIT."),
            True,
        )
    if bucket == "BUY":
        if (mean is not None
                and mean <= mean_threshold
                and mat_neg >= min_material):
            return (
                "WAIT",
                (f"Sentiment demotion: 7d mean {mean:.2f} ≤ "
                 f"threshold {mean_threshold} AND {mat_neg} "
                 f"material-negative headlines (≥ {min_material})."),
                True,
            )
    return bucket, reason, False


def apply_horizon_and_range_demotion(
    *,
    bucket: str,
    reason: str,
    horizon_classification: dict | None,
    range_pct: float | None,
    extreme_range_threshold: float = 85.0,
) -> tuple[str, str, bool]:
    """Demote a BUY to WAIT when the entry-timing risk is bad enough
    that the bucket-vote consensus shouldn't override it.

    Two veto rules:

    Rule A — Swing-horizon AVOID veto. The swing horizon (1-8w) reads
    the same data the bucket vote does, but specifically scores
    entry-timing risk (range_pct, RSI, drawdown proximity). If swing
    has decided AVOID, the row should NOT surface as BUY — that means
    the trend-followers are still long but the entry edge is gone.
    The previous logic (HOLD + majority-long → BUY) ignored this and
    surfaced BUY on QUAL/USMV at the 100th-percentile of their 52w
    range. This rule downgrades that to WAIT.

    Rule B — Extreme range-pct cap. range_pct ≥ 95 means the price
    is sitting at the absolute top of its 52w range — a literal
    new-high zone. Buying at the high is the worst-timed entry by
    construction; downgrade BUY → WAIT independent of horizon.

    Pure function. Returns (bucket, reason, demoted_flag). Demotion
    flag lets the bucket trace surface "downgraded by horizon veto"
    as a separate line so the user can see why it didn't BUY.
    """
    if bucket != "BUY":
        return bucket, reason, False

    # Rule A — swing-horizon AVOID veto.
    if horizon_classification:
        swing = (horizon_classification.get("swing") or {})
        swing_signal = swing.get("signal")
        swing_score = swing.get("score")
        if swing_signal == "AVOID":
            score_str = f" (score {swing_score}/8)" if swing_score is not None else ""
            return (
                "WAIT",
                (f"Horizon demotion: swing horizon AVOID{score_str} — "
                 f"entry-timing edge is gone even though the multi-"
                 f"strategy consensus is still long. {reason}"),
                True,
            )

    # Rule B — extreme range-position cap, GATED on the swing horizon
    # NOT saying BUY. The unconditional version of this rule would have
    # blocked legitimate breakout BUYs like MU on the Deutsche Bank
    # upgrade (new 52w high + fresh catalyst). When the swing horizon
    # scores the row as BUY, that means the event-driven layer found
    # something — let the BUY through despite the high range_pct.
    #
    # Threshold tightened from 95 → 85 May 2026 (user bug report #7):
    # at 85th percentile the geometric risk/reward is already
    # asymmetric (3p upside vs 8p downside in the typical case) and
    # the swing-BUY exception still preserves breakouts with a real
    # catalyst — etf_factor BUYs at 96th pctile no longer pass without
    # the swing layer explicitly agreeing.
    if range_pct is not None and range_pct >= extreme_range_threshold:
        swing_signal_b = ((horizon_classification or {}).get("swing") or {}).get("signal")
        if swing_signal_b != "BUY":
            return (
                "WAIT",
                (f"Range demotion: {range_pct:.0f}th percentile of 52w range "
                 f"(≥ {extreme_range_threshold:.0f}) AND swing horizon not BUY "
                 f"— buying near the top without a fresh catalyst. {reason}"),
                True,
            )

    # Rule C — long-term BUY + swing AVOID: position-only call, not a
    # fresh swing entry. Surfaces the NVDA/AMZN class where the
    # multi-year story is intact but the entry timing is bad. Doesn't
    # change the bucket (it stays whatever Rules A/B and compute_bucket
    # produced) but enriches the reason so the user sees the split.
    if horizon_classification:
        swing_signal_c = (horizon_classification.get("swing") or {}).get("signal")
        long_signal_c = (horizon_classification.get("long_term") or {}).get("signal")
        if long_signal_c == "BUY" and swing_signal_c == "AVOID":
            return (
                bucket,
                (f"{reason} "
                 f"Long-term horizon = BUY but swing horizon = AVOID: "
                 f"strong multi-year hold candidate, NOT a fresh swing "
                 f"entry today."),
                False,
            )

    # Rule D — passive-only BUY guard. If passive horizon (3-5yr DCA)
    # is the ONLY horizon saying BUY and neither swing (1-8w) nor
    # long-term (6-18m) confirms, the row shouldn't surface as today's
    # action — the DCA thesis is "buy a little, regularly", not "buy
    # the whole position at today's open". User Bug #10. Demote BUY to
    # WAIT with an explicit DCA framing so the user gets routed to
    # the right mental model.
    if horizon_classification:
        swing_d = (horizon_classification.get("swing") or {}).get("signal")
        long_d = (horizon_classification.get("long_term") or {}).get("signal")
        passive_d = (horizon_classification.get("passive") or {}).get("signal")
        if (
            passive_d == "BUY"
            and swing_d not in ("BUY",)
            and long_d not in ("BUY",)
        ):
            return (
                "WAIT",
                (f"Passive horizon = BUY (good DCA candidate) but neither "
                 f"swing nor long-term horizons confirm — this is a regular-"
                 f"contribution thesis, not a same-day full-position entry. "
                 f"{reason}"),
                True,
            )

    return bucket, reason, False


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

        # Earnings-proximity suppressor — ④ of the Alpha Engine.
        # When earnings land inside the 7d window, refuse to call BUY
        # (post-print gap swallows the reward leg of a 1:2 setup).
        # earnings_signal.upcoming.days_until is populated upstream
        # from the Finnhub-backed /api/integrations/finnhub/earnings-
        # calendar endpoint; absent / disabled → no suppression.
        earnings_sig = best.get("earnings_signal") or {}
        days_until_earnings = (
            (earnings_sig.get("upcoming") or {}).get("days_until")
        )
        bucket, reason, conviction, earnings_suppressed = apply_earnings_suppressor(
            bucket=bucket,
            reason=reason,
            conviction=conviction,
            days_until_earnings=days_until_earnings,
        )

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

        # Copy bucket + reason + sentiment-demoted flag + rationale onto
        # every row for this symbol so the frontend can render any row's
        # expand panel without re-deriving.
        for r in sym_rows:
            r["bucket"] = bucket
            r["bucket_reason"] = reason
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
            # Earnings suppression flag — UI shows a WARNING badge on
            # the card when set, even if the bucket didn't actually
            # change (already WAIT). days_until carried for the
            # tooltip "earnings in Nd".
            r["earnings_suppressed"] = earnings_suppressed
            r["earnings_proximity_days"] = days_until_earnings
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

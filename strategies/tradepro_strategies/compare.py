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
from .regimes import REGIMES, all_regime_stats
from .strategies import resolve as resolve_strategy


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


def _row_for(symbol: str, strategy: StrategySpec, prices: pd.DataFrame, cfg: CompareConfig) -> dict:
    """Run one (symbol, strategy) backtest and return a JSON-ready row."""
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
            "error": str(e),
        }

    # Re-derive the signal on the (adjusted) prices so we can read today's
    # value. run_backtest already applies the close←adj_close swap; mirror
    # that here so latest_signal is exactly what the executor saw.
    adjusted = prices.assign(close=prices["adj_close"]) if "adj_close" in prices.columns else prices
    full_signals = signal_fn(adjusted).reindex(adjusted.index).fillna(0).astype(int)
    latest_signal = int(full_signals.iloc[-1]) if not full_signals.empty else 0
    latest_bar = adjusted.index[-1].isoformat() if not adjusted.empty else None

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
        "error": None,
    }


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
) -> dict:
    """Run every (symbol × strategy) backtest and return a ranked payload.

    The returned dict is the JSON we want to ship to the website — see
    `cli/run_comparison.py` for the wire format.
    """
    cfg = cfg or CompareConfig()

    rows: list[dict] = []
    price_cache: dict[str, pd.DataFrame] = {}

    for symbol in symbols:
        if symbol not in price_cache:
            price_cache[symbol] = ensure_cached(cfg.provider, symbol, start, end)
        prices = price_cache[symbol]
        for strat in strategies:
            rows.append(_row_for(symbol, strat, prices, cfg))

    rows.sort(key=lambda r: _rank_value(r, cfg.rank_metric), reverse=True)
    for i, row in enumerate(rows, start=1):
        row["rank"] = i

    best_per_strategy: dict[str, dict] = {}
    for row in rows:
        s = row["strategy"]
        if s not in best_per_strategy:
            best_per_strategy[s] = {"symbol": row["symbol"], "rank": row["rank"]}

    best_overall = rows[0] if rows else None

    return {
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
        "rows": rows,
        "best_per_strategy": best_per_strategy,
        "best_overall": (
            {"symbol": best_overall["symbol"], "strategy": best_overall["strategy"],
             "rank_metric": cfg.rank_metric,
             "value": best_overall.get("stats", {}).get(cfg.rank_metric)}
            if best_overall else None
        ),
    }

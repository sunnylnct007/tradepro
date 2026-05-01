"""Tool implementations — separated from the FastMCP registration so
each tool is unit-testable without an MCP server running.

Every tool returns a dict that includes a `_source` field. This is the
URI the LLM is required to cite when claiming a fact from the output.
The verifier (verify_answer) walks the citations and confirms the
claimed numbers actually exist at the cited paths.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Any

import requests


def _api_base() -> str:
    """API to read live state from. Defaults to the local container;
    override with TRADEPRO_API_URL for the deployed instance."""
    return os.environ.get("TRADEPRO_API_URL", "http://localhost:5080").rstrip("/")


def _get(path: str, params: dict | None = None, timeout: float = 10.0) -> dict:
    url = f"{_api_base()}{path}"
    resp = requests.get(url, params=params or {}, timeout=timeout)
    resp.raise_for_status()
    return resp.json()


def list_universes() -> dict:
    """List every comparator universe currently cached in the API
    along with its freshness. The first thing an LLM should call
    when it doesn't know which universe a symbol belongs to."""
    try:
        data = _get("/api/compare/universes")
        universes = data.get("universes", [])
    except Exception as e:  # noqa: BLE001
        return {
            "_source": f"{_api_base()}/api/compare/universes",
            "ok": False,
            "error": str(e),
            "universes": [],
        }
    return {
        "_source": f"{_api_base()}/api/compare/universes",
        "fetched_at": _now_iso(),
        "ok": True,
        "universes": universes,
    }


def get_compare(universe: str) -> dict:
    """Full ranked-comparison payload for a universe. Each row has its
    own `_source` substring so a claim like 'QQQ Sharpe 0.94' can be
    cited as `tradepro://compare/etf_us_core/rows[0]/stats/sharpe`."""
    if not universe:
        return _err("get_compare", "universe is required")
    try:
        data = _get("/api/compare/latest", params={"universe": universe})
    except Exception as e:  # noqa: BLE001
        return _err("get_compare", str(e), universe=universe)

    # Annotate each row with its citation path so the LLM can quote
    # individual cells without re-hashing.
    rows = data.get("payload", {}).get("rows", []) or []
    seen_symbols: set[str] = set()
    for i, row in enumerate(rows):
        sym = row.get("symbol", "")
        row["_source"] = f"tradepro://compare/{universe}/rows[{i}]"
        # First row per symbol is the "best by rank" row — surface that
        # cleanly for the LLM to locate it.
        if sym and sym not in seen_symbols:
            row["_source_symbol_best"] = f"tradepro://compare/{universe}/best/{sym}"
            seen_symbols.add(sym)
    return {
        "_source": f"tradepro://compare/{universe}",
        "fetched_at": _now_iso(),
        "universe": universe,
        "ok": True,
        "envelope": data,
    }


def get_market_state(symbol: str, lookback_days: int = 365) -> dict:
    """Compute the current decision_trace for any ticker on demand.
    Doesn't need the symbol to be in a universe — fetches prices,
    builds market_state, returns the rule chain."""
    if not symbol:
        return _err("get_market_state", "symbol is required")
    try:
        # Lazy imports so importing the mcp package is cheap.
        from datetime import timedelta
        from ..cache import ensure_cached
        from ..market_state import market_state
        end = datetime.now(timezone.utc)
        start = end - timedelta(days=max(lookback_days, 365))
        prices = ensure_cached("yahoo", symbol, start, end)
        state = market_state(symbol, prices)
    except Exception as e:  # noqa: BLE001
        return _err("get_market_state", str(e), symbol=symbol)
    out = state.to_dict()
    out["_source"] = f"live://market_state/{symbol}"
    out["fetched_at"] = _now_iso()
    out["bars_used"] = int(len(prices))
    return out


def get_news_with_sentiment(symbol: str, limit: int = 8) -> dict:
    """Recent headlines for a symbol with LLM-scored sentiment.
    Each item is citable individually so the LLM can quote a specific
    headline + its score."""
    if not symbol:
        return _err("get_news_with_sentiment", "symbol is required")
    try:
        from ..news import fetch_news
        from ..news_sentiment import score_news, summarise_recent
        items = fetch_news(symbol, limit=limit)
        scored = score_news(items)
        summary = summarise_recent(scored, items, days=7)
    except Exception as e:  # noqa: BLE001
        return _err("get_news_with_sentiment", str(e), symbol=symbol)
    enriched = []
    for i, (raw, s) in enumerate(zip(items, scored)):
        d = raw.to_dict()
        d.update({
            "sentiment": s.sentiment,
            "themes": s.themes,
            "material": s.material,
            "model": s.model,
            "error": s.error,
            "_source": f"live://news/{symbol}/items[{i}]",
        })
        enriched.append(d)
    return {
        "_source": f"live://news/{symbol}",
        "fetched_at": _now_iso(),
        "symbol": symbol,
        "ok": True,
        "items": enriched,
        "summary_7d": summary.to_dict(),
    }


def get_regime_history(universe: str, symbol: str, strategy: str | None = None) -> dict:
    """Per-historical-regime stats for one (symbol, strategy) cell.
    Pulls from the cached comparator output rather than re-running —
    fastest answer to 'how did this survive 2008?'."""
    if not universe or not symbol:
        return _err("get_regime_history", "universe and symbol are required")
    try:
        data = _get("/api/compare/latest", params={"universe": universe})
    except Exception as e:  # noqa: BLE001
        return _err("get_regime_history", str(e),
                    universe=universe, symbol=symbol)
    rows = data.get("payload", {}).get("rows", []) or []
    # Pick the matching row by symbol + (optionally) strategy. If no
    # strategy specified, use the best-ranked row for the symbol.
    matching = [r for r in rows if r.get("symbol") == symbol]
    if strategy:
        matching = [r for r in matching if r.get("strategy") == strategy]
    if not matching:
        return _err("get_regime_history",
                    f"no row for symbol={symbol} strategy={strategy} in {universe}")
    matching.sort(key=lambda r: r.get("rank", 1e9))
    row = matching[0]
    return {
        "_source": f"tradepro://compare/{universe}/best/{symbol}/regimes",
        "fetched_at": _now_iso(),
        "universe": universe,
        "symbol": symbol,
        "strategy_used": row.get("strategy"),
        "regimes": row.get("regimes", []),
        "stats": row.get("stats", {}),
    }


def get_health() -> dict:
    """System health — API + Mac heartbeat + cache freshness. Useful
    first call to tell the user 'data is stale, take with a pinch of
    salt' before answering."""
    try:
        return {
            "_source": f"{_api_base()}/health/details",
            "fetched_at": _now_iso(),
            "ok": True,
            "data": _get("/health/details"),
        }
    except Exception as e:  # noqa: BLE001
        return _err("get_health", str(e))


def run_comparison(
    universe: str,
    rank_metric: str = "sharpe",
    strategies: list[str] | None = None,
) -> dict:
    """Fire a fresh comparator run and return the new payload. Slow
    (10–60s depending on universe size). Use sparingly — usually the
    cached `get_compare` is enough."""
    if not universe:
        return _err("run_comparison", "universe is required")
    try:
        from datetime import timedelta
        from ..backtest import FeeModel
        from ..compare import CompareConfig, StrategySpec, compare
        from ..strategies import available
        from ..watchlists import resolve as resolve_watchlist
        symbols = resolve_watchlist(universe)
        all_strats = strategies or available()
        specs = [StrategySpec(name=n) for n in all_strats]
        end = datetime.now(timezone.utc)
        start = end - timedelta(days=365 * 5)
        cfg = CompareConfig(
            provider="yahoo",
            initial_capital=10_000.0,
            currency="USD",
            rank_metric=rank_metric,
            fees=FeeModel(commission_per_trade=0.0, stamp_duty_rate=0.0),
        )
        payload = compare(symbols, specs, start, end, cfg)
    except Exception as e:  # noqa: BLE001
        return _err("run_comparison", str(e), universe=universe)
    return {
        "_source": f"live://compare/{universe}/run",
        "fetched_at": _now_iso(),
        "universe": universe,
        "ok": True,
        "row_count": len(payload.get("rows", [])),
        "best_overall": payload.get("best_overall"),
        "envelope": payload,
    }


# --- helpers ---------------------------------------------------------------


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _err(tool: str, message: str, **fields: Any) -> dict:
    return {
        "_source": f"error://{tool}",
        "fetched_at": _now_iso(),
        "ok": False,
        "error": message,
        **fields,
    }


def serialize(obj: Any) -> str:
    """Strict JSON serialisation that the FastMCP layer can hand back
    to the LLM. Handles dataclasses + datetime defensively."""
    return json.dumps(obj, default=str, ensure_ascii=False)

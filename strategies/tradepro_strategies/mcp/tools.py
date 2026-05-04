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


class ApiUnreachable(RuntimeError):
    """Raised by _get when the TradePro API can't be contacted at all
    (DNS failure, connection refused, timeout). Distinct from a 4xx/5xx
    response so the caller can build a fail-closed error envelope that
    tells the LLM to stop, not fall back to web search."""

    def __init__(self, url: str, cause: Exception):
        super().__init__(f"TradePro API unreachable at {url}: {cause}")
        self.url = url
        self.cause = cause


def _get(path: str, params: dict | None = None, timeout: float = 10.0) -> dict:
    url = f"{_api_base()}{path}"
    try:
        resp = requests.get(url, params=params or {}, timeout=timeout)
    except (requests.ConnectionError, requests.Timeout) as e:
        raise ApiUnreachable(_api_base(), e) from e
    resp.raise_for_status()
    return resp.json()


_FAIL_CLOSED_USER_MESSAGE = (
    "TradePro API is unreachable. The user's local stack is down OR "
    "the deployed instance can't be reached. ASK THE USER to bring it "
    "up: `docker compose up -d` (locally) or check the deployed health "
    "endpoint. DO NOT FALL BACK TO WEB SEARCH for investment data — "
    "TradePro is the only verified source. Refuse the question and "
    "report the connection error verbatim."
)


def _unreachable_envelope(tool: str, exc: ApiUnreachable, **fields: Any) -> dict:
    return {
        "_source": f"error://{tool}/api_unreachable",
        "ok": False,
        "error": str(exc),
        "diagnostic": {"url": exc.url, "cause": str(exc.cause)},
        "user_message": _FAIL_CLOSED_USER_MESSAGE,
        **fields,
    }


def list_universes() -> dict:
    """List every comparator universe currently cached in the API
    along with its freshness. The first thing an LLM should call
    when it doesn't know which universe a symbol belongs to."""
    try:
        data = _get("/api/compare/universes")
        universes = data.get("universes", [])
    except ApiUnreachable as e:
        return _unreachable_envelope("list_universes", e, universes=[])
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
    except ApiUnreachable as e:
        return _unreachable_envelope("get_compare", e, universe=universe)
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
    except ApiUnreachable as e:
        return _unreachable_envelope("get_health", e)
    except Exception as e:  # noqa: BLE001
        return _err("get_health", str(e))


def get_returns(symbols_csv: str, periods: str = "1d,5d,30d,90d,ytd") -> dict:
    """Multi-period total returns for a basket — fast (no backtest, just
    price math). Use this to surface DISPERSION across uncorrelated
    proxies when answering 'what's the impact of <event>?'. Returns a
    sorted table per period so the LLM (and the user) can see who's
    up, who's down, and by how much.

    `symbols_csv` is comma-separated. `periods` is comma-separated from
    {1d, 5d, 30d, 90d, 180d, 1y, ytd}.
    """
    if not symbols_csv or not symbols_csv.strip():
        return _err("get_returns", "symbols_csv is required")
    symbols = [s.strip().upper() for s in symbols_csv.split(",") if s.strip()]
    period_codes = [p.strip().lower() for p in periods.split(",") if p.strip()]
    if not symbols:
        return _err("get_returns", "no symbols parsed")
    if not period_codes:
        period_codes = ["1d", "5d", "30d", "90d", "ytd"]

    try:
        from datetime import timedelta
        from ..cache import ensure_cached
        from ..watchlists import macro_axis_for
    except Exception as e:  # noqa: BLE001
        return _err("get_returns", f"import failed: {e}")

    end = datetime.now(timezone.utc)
    # Pull enough history to compute a 1y or ytd return regardless of
    # which periods the caller wants — cheap relative to a backtest.
    start = end - timedelta(days=400)

    rows: list[dict] = []
    for sym in symbols:
        try:
            prices = ensure_cached("yahoo", sym, start, end)
        except Exception as e:  # noqa: BLE001
            rows.append({
                "_source": f"error://returns/{sym}",
                "symbol": sym, "ok": False,
                "error": f"price fetch failed: {e}",
            })
            continue
        if prices.empty:
            rows.append({
                "_source": f"error://returns/{sym}",
                "symbol": sym, "ok": False,
                "error": "no price data",
            })
            continue
        series = prices["adj_close"] if "adj_close" in prices.columns else prices["close"]
        last = float(series.iloc[-1])
        last_dt = prices.index[-1]
        out: dict = {
            "_source": f"live://returns/{sym}",
            "symbol": sym, "ok": True,
            "macro_axis": macro_axis_for(sym),
            "as_of": last_dt.isoformat(),
            "last_price": last,
        }
        for code in period_codes:
            ref = _ref_price(series, code, last_dt)
            if ref is None or ref == 0:
                out[f"return_{code}_pct"] = None
            else:
                out[f"return_{code}_pct"] = (last - ref) / ref * 100.0
        rows.append(out)

    return {
        "_source": "live://returns",
        "fetched_at": _now_iso(),
        "ok": True,
        "symbols": symbols,
        "periods": period_codes,
        "rows": rows,
    }


def _ref_price(series, code: str, last_dt) -> float | None:
    """Look up the reference close for a period code. Walks backwards
    on weekends/holidays so a Saturday request still anchors on
    Friday's bar."""
    from datetime import timedelta
    if code == "ytd":
        # First trading day of the current calendar year.
        year = last_dt.year
        ytd = series[series.index.year == year]
        return float(ytd.iloc[0]) if not ytd.empty else None
    days_map = {
        "1d": 1, "5d": 5, "30d": 30, "90d": 90, "180d": 180, "1y": 365,
    }
    n = days_map.get(code)
    if n is None:
        return None
    target = last_dt - timedelta(days=n)
    # Pick the closest bar at or before the target date.
    sub = series[series.index <= target]
    if sub.empty:
        return None
    return float(sub.iloc[-1])


def evaluate_symbols(symbols_csv: str, lookback_years: int = 5) -> dict:
    """Run every available strategy against any one or more tickers —
    no universe required. Mirrors what the Compare page shows for a
    universe row, but ad-hoc and for symbols Claude Desktop names on
    the fly. ~10-15s per symbol (5 backtests + market_state per ticker).
    News, sentiment, fundamentals, and consensus are intentionally
    skipped here — they're slow and need not block the multi-strategy
    bucket vote, which is the primary signal an investor cares about.
    """
    if not symbols_csv or not symbols_csv.strip():
        return _err("evaluate_symbols", "symbols_csv is required (e.g. 'VWRP.L,SWDA.L')")
    symbols = [s.strip().upper() for s in symbols_csv.split(",") if s.strip()]
    if not symbols:
        return _err("evaluate_symbols", "no symbols parsed from input")

    try:
        from datetime import timedelta
        from ..backtest import BacktestConfig, FeeModel, run_backtest
        from ..cache import ensure_cached
        from ..compare import compute_bucket
        from ..market_state import market_state
        from ..regimes import all_regime_stats
        from ..strategies import available as available_strategies
        from ..strategies import resolve as resolve_strategy
    except Exception as e:  # noqa: BLE001
        return _err("evaluate_symbols", f"import failed: {e}")

    end = datetime.now(timezone.utc)
    start = end - timedelta(days=365 * max(int(lookback_years), 1))
    fees = FeeModel(commission_per_trade=0.0, stamp_duty_rate=0.0)
    bt_cfg = BacktestConfig(
        initial_capital=10_000.0, currency="USD", fees=fees,
    )
    strategy_names = available_strategies()

    results: list[dict] = []
    for sym in symbols:
        try:
            prices = ensure_cached("yahoo", sym, start, end)
        except Exception as e:  # noqa: BLE001
            results.append({
                "_source": f"error://evaluate/{sym}",
                "symbol": sym, "ok": False,
                "error": f"price fetch failed: {e}",
            })
            continue

        if prices.empty:
            results.append({
                "_source": f"error://evaluate/{sym}",
                "symbol": sym, "ok": False,
                "error": "no price data returned (invalid ticker?)",
            })
            continue

        ms = market_state(sym, prices)
        # run_backtest applies the close <- adj_close swap internally;
        # the latest-signal recompute below has to mirror it so what
        # we report as 'in_position' is what the executor saw.
        adjusted = (
            prices.assign(close=prices["adj_close"])
            if "adj_close" in prices.columns else prices
        )

        strat_rows: list[dict] = []
        for sname in strategy_names:
            try:
                signal_fn = resolve_strategy(sname, {})
                bt = run_backtest(prices, signal_fn, bt_cfg)
                signals = (
                    signal_fn(adjusted).reindex(adjusted.index)
                    .fillna(0).astype(int)
                )
                latest = int(signals.iloc[-1]) if not signals.empty else 0
                nonzero = signals[signals != 0]
                in_pos = bool(nonzero.iloc[-1] == 1) if not nonzero.empty else False
                position_since = nonzero.index[-1].isoformat() if not nonzero.empty else None
                # Regime stats per strategy run — same view get_regime_history
                # would surface for a universe member. Attaching here makes
                # ad-hoc tickers stress-testable without a pre-built universe
                # cache, which was the gap that pushed Claude Desktop to
                # web search when asked about VUKE.L's regime history.
                regime_rows: list[dict] = []
                try:
                    regime_df = all_regime_stats(bt.equity_curve)
                    for r in regime_df.to_dict(orient="records"):
                        bars = int(r.get("bars") or 0)
                        if bars <= 0:
                            continue
                        regime_rows.append({
                            "key": r.get("regime_key"),
                            "name": r.get("regime_name"),
                            "kind": r.get("kind"),
                            "bars": bars,
                            "return_pct": _safe_num(r.get("return_pct")),
                            "max_drawdown_pct": _safe_num(r.get("max_drawdown_pct")),
                            "_source": (
                                f"live://evaluate/{sym}/strategies/{sname}"
                                f"/regimes/{r.get('regime_key')}"
                            ),
                        })
                except Exception:  # noqa: BLE001
                    # Regime stats are nice-to-have, never gate the row.
                    regime_rows = []
                strat_rows.append({
                    "_source": f"live://evaluate/{sym}/strategies/{sname}",
                    "strategy": sname,
                    "in_position": in_pos,
                    "position_since": position_since,
                    "latest_signal": latest,
                    "stats": {
                        k: (float(v) if isinstance(v, (int, float)) else v)
                        for k, v in (bt.stats or {}).items()
                    },
                    "regimes": regime_rows,
                    "error": None,
                })
            except Exception as e:  # noqa: BLE001
                strat_rows.append({
                    "_source": f"error://evaluate/{sym}/strategies/{sname}",
                    "strategy": sname,
                    "in_position": False,
                    "error": str(e),
                })

        long_count = sum(1 for r in strat_rows if r.get("in_position"))
        total = len(strat_rows)
        bucket, bucket_reason = compute_bucket(
            price_verdict=ms.entry_signal,
            price_reason=ms.entry_reason,
            long_count=long_count,
            total=total,
        )
        results.append({
            "_source": f"live://evaluate/{sym}",
            "symbol": sym,
            "ok": True,
            "bucket": bucket,
            "bucket_reason": bucket_reason,
            "long_count": long_count,
            "total_strategies": total,
            "market_state": ms.to_dict(),
            "strategies": strat_rows,
        })

    return {
        "_source": "live://evaluate",
        "fetched_at": _now_iso(),
        "ok": True,
        "lookback_years": lookback_years,
        "symbols": symbols,
        "results": results,
    }


def _load_push_credentials() -> tuple[str | None, str | None, str]:
    """Resolve (base_url, token, source) for /api/ingest/* pushes.
    Order: ~/.tradepro/credentials JSON file → TRADEPRO_API_URL +
    TRADEPRO_API_TOKEN env. `source` describes which path won so the
    return envelope can cite it ('file' / 'env' / 'none')."""
    import json as _json
    from pathlib import Path
    base: str | None = None
    token: str | None = None
    source = "none"
    cred_path = Path.home() / ".tradepro" / "credentials"
    if cred_path.is_file():
        try:
            data = _json.loads(cred_path.read_text())
            base = data.get("api_base_url")
            token = data.get("api_token")
            if base or token:
                source = "file"
        except (_json.JSONDecodeError, OSError):
            pass
    if not base:
        base = os.environ.get("TRADEPRO_API_URL")
        if base and source == "none":
            source = "env"
    if not token:
        token = os.environ.get("TRADEPRO_API_TOKEN")
        if token and source != "file":
            source = "env" if source == "none" else source
    return (base.rstrip("/") if base else None, token, source)


def _push_compare(payload: dict) -> dict:
    """POST a compare payload to /api/ingest/compare so the UI sees
    the refresh on its next page load. Returns a structured envelope
    instead of raising; caller stitches it into the run_comparison
    response."""
    base, token, source = _load_push_credentials()
    if not base or not token:
        return {
            "ok": False,
            "skipped": True,
            "reason": (
                "no push credentials — set TRADEPRO_API_URL + "
                "TRADEPRO_API_TOKEN in the MCP env, or write "
                "~/.tradepro/credentials as JSON. The comparison still "
                "ran and the payload is in the response; only the "
                "cache push was skipped."
            ),
            "source": source,
        }
    url = f"{base}/api/ingest/compare"
    try:
        resp = requests.post(
            url,
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=30,
        )
    except requests.RequestException as e:
        return {
            "ok": False,
            "url": url,
            "source": source,
            "error": str(e),
        }
    return {
        "ok": 200 <= resp.status_code < 300,
        "url": url,
        "http_status": resp.status_code,
        "response_preview": (resp.text or "")[:200],
        "source": source,
    }


def run_comparison(
    universe: str,
    rank_metric: str = "sharpe",
    strategies: list[str] | None = None,
    push: bool = False,
) -> dict:
    """Fire a fresh comparator run and return the new payload. Slow
    (10–60s depending on universe size). Use sparingly — usually the
    cached `get_compare` is enough.

    When `push=True`, the resulting payload is also POSTed to
    /api/ingest/compare so the Compare page reflects the refresh
    on its next load (otherwise the run is ephemeral to the MCP
    process). Push uses the same creds path as `tradepro-push`."""
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

    push_result: dict | None = None
    if push:
        push_result = _push_compare(payload)
    return {
        "_source": f"live://compare/{universe}/run",
        "fetched_at": _now_iso(),
        "universe": universe,
        "ok": True,
        "row_count": len(payload.get("rows", [])),
        "best_overall": payload.get("best_overall"),
        "push_result": push_result,
        "envelope": payload,
    }


# --- helpers ---------------------------------------------------------------


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe_num(x: Any) -> float | None:
    """Coerce a numeric to a JSON-safe float; returns None for NaN /
    inf / non-numeric values so the regime envelope never breaks JSON
    serialisation downstream."""
    try:
        f = float(x)
    except (TypeError, ValueError):
        return None
    import math as _math
    if _math.isnan(f) or _math.isinf(f):
        return None
    return f


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

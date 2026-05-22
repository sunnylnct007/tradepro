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


def _default_timeout() -> float:
    """Per-call HTTP timeout for the MCP layer. Default 30s — generous
    enough to survive a worker mid-refresh + cold compare cache, tight
    enough that Claude Desktop doesn't sit on a dead tool call. Tune
    via `TRADEPRO_MCP_TIMEOUT` env if a deployment hits genuinely slow
    upstream services."""
    raw = os.environ.get("TRADEPRO_MCP_TIMEOUT")
    if raw:
        try:
            v = float(raw)
            if 1.0 <= v <= 120.0:
                return v
        except ValueError:
            pass
    return 30.0


def _resolve_api_token() -> str | None:
    """Bearer token for the TradePro API. Resolution order:
       1. TRADEPRO_API_TOKEN env var (Claude Desktop / explicit override)
       2. ~/.tradepro/credentials JSON file (Mac convention — same place
          the push pipeline reads from)
    Returns None when neither path yields a token; the API allows
    anonymous reads when `Firebase:RequireAuth=false` (local dev)."""
    token = os.environ.get("TRADEPRO_API_TOKEN")
    if token:
        return token
    try:
        from pathlib import Path
        cred_path = Path.home() / ".tradepro" / "credentials"
        if cred_path.is_file():
            data = json.loads(cred_path.read_text())
            t = data.get("api_token")
            if t:
                return t
    except (json.JSONDecodeError, OSError):
        pass
    return None


def _auth_headers() -> dict[str, str]:
    """Bearer header for the TradePro API. The same ingest token the
    Mac worker uses for /api/ingest/* now also unlocks /api/* reads
    when the prod API has `Firebase:RequireAuth=true` — single secret,
    no separate read token. Empty dict when no token is available so
    local dev keeps working without it."""
    token = _resolve_api_token()
    if not token:
        return {}
    return {"Authorization": f"Bearer {token}"}


def _get(path: str, params: dict | None = None,
         timeout: float | None = None) -> dict:
    url = f"{_api_base()}{path}"
    try:
        resp = requests.get(
            url, params=params or {},
            headers=_auth_headers(),
            timeout=timeout if timeout is not None else _default_timeout(),
        )
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


# Fields stripped by default in the MCP-friendly compact mode. Each is
# verbose (multi-KB per row) and rarely useful for the LLM at the
# universe-overview level — the user can pull the same data per-symbol
# via get_news_with_sentiment / get_regime_history / get_market_state.
_BLOAT_FIELDS = (
    "decision_trace",     # the per-rule trace; rationale already summarises
    "news",               # full headlines + sentiment per row
    "rationale",          # 5-paragraph LLM block per row
    "sentiment_summary",  # rolling sentiment object
    "regimes",            # per-row regime stats
    "external_consensus", # analyst consensus block
    "fundamentals",       # full quoteSummary
    "historical_earnings",# 5y earnings dates list
    "closes_30d",         # 30-element close array (chart input)
    "swing_score",        # detailed swing scorer block
    "horizon_classification",  # multi-horizon verdict block
)


def get_compare(
    universe: str,
    top_n: int | None = None,
    fields: str | list[str] | None = None,
    strip_bloat: bool = False,
) -> dict:
    """Full ranked-comparison payload for a universe. Each row has its
    own `_source` substring so a claim like 'QQQ Sharpe 0.94' can be
    cited as `tradepro://compare/etf_us_core/rows[0]/stats/sharpe`.

    Optional shape controls so callers (especially MCP, which has a
    tool-result size limit) can downsize the payload:

      top_n: keep only the first N rows after ranking. None = all.
      fields: comma-separated string OR list of row-field names to
        KEEP. Everything else is dropped. None = keep all.
      strip_bloat: drop the standard "verbose" fields
        (decision_trace, news, rationale, etc.) — overridden by
        `fields` if also set.
    """
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

    # Apply top_n FIRST so any bloat stripping only iterates the rows
    # we're keeping.
    original_count = len(rows)
    truncated = False
    if isinstance(top_n, int) and top_n > 0 and original_count > top_n:
        rows = rows[:top_n]
        truncated = True

    # Field whitelisting / bloat stripping.
    if fields is not None:
        if isinstance(fields, str):
            field_set = {f.strip() for f in fields.split(",") if f.strip()}
        else:
            field_set = set(fields)
        # Always preserve identity + citation fields so the LLM can
        # still cite back to source.
        field_set |= {"symbol", "strategy", "_source", "_source_symbol_best"}
        rows = [
            {k: v for k, v in row.items() if k in field_set}
            for row in rows
        ]
    elif strip_bloat:
        rows = [
            {k: v for k, v in row.items() if k not in _BLOAT_FIELDS}
            for row in rows
        ]

    # Mutate the envelope's rows array in place so the rest of the
    # payload (universe meta, errors, market_context) stays intact.
    if "payload" in data and isinstance(data["payload"], dict):
        data["payload"]["rows"] = rows

    return {
        "_source": f"tradepro://compare/{universe}",
        "fetched_at": _now_iso(),
        "universe": universe,
        "ok": True,
        "envelope": data,
        "row_count_returned": len(rows),
        "row_count_total": original_count,
        "truncated": truncated,
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
    # strategy is specified, pick the best-Sharpe strategy for this
    # symbol — not the comparator's overall rank, which can favour
    # buy_and_hold on universe-level momentum metrics (Bug #15). The
    # regime question is "which strategy survived the bad periods best?"
    # — that's a per-symbol Sharpe answer, not a universe rank.
    matching = [r for r in rows if r.get("symbol") == symbol]
    if strategy:
        matching = [r for r in matching if r.get("strategy") == strategy]
    if not matching:
        return _err("get_regime_history",
                    f"no row for symbol={symbol} strategy={strategy} in {universe}")

    def _sharpe(r: dict) -> float:
        s = (r.get("stats") or {}).get("sharpe")
        try:
            return float(s) if s is not None else float("-inf")
        except (TypeError, ValueError):
            return float("-inf")

    if not strategy:
        matching.sort(key=_sharpe, reverse=True)
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


def get_strategy_leaderboard(
    universe: str,
    symbol: str,
    metric: str = "sharpe",
) -> dict:
    """Ranked per-strategy leaderboard for one symbol.

    Answers "which strategy is doing best on AVGO?" directly — sorts
    every strategy with a row for ``symbol`` in ``universe`` by
    ``metric`` (default ``sharpe``) and projects the columns a human
    would scan: action today, position state, the backtest stats, and
    a delta vs the buy-and-hold null model.

    The ``action_label`` field collapses ``current_action`` +
    ``in_position`` into one of BUY / SELL / HOLD-IN / HOLD-OUT so the
    LLM has a single value to reason with — same vocabulary the
    Backtest page header uses post-2026-05-18.

    Metrics: ``sharpe`` (default), ``cagr_pct``, ``max_drawdown_pct``.
    For drawdown, smaller (less negative) is better — the ranking
    treats it as an absolute value for the sort.

    Cite an entry as
    ``tradepro://compare/<universe>/leaderboard/<symbol>/strategies[<i>]``.
    """
    if not universe or not symbol:
        return _err("get_strategy_leaderboard",
                    "universe and symbol are required")
    if metric not in ("sharpe", "cagr_pct", "max_drawdown_pct"):
        return _err("get_strategy_leaderboard",
                    f"unsupported metric {metric!r}; use sharpe | cagr_pct | max_drawdown_pct")
    try:
        data = _get("/api/compare/latest", params={"universe": universe})
    except ApiUnreachable as e:
        return _unreachable_envelope(
            "get_strategy_leaderboard", e,
            universe=universe, symbol=symbol, strategies=[])
    except Exception as e:  # noqa: BLE001
        return _err("get_strategy_leaderboard", str(e),
                    universe=universe, symbol=symbol)

    rows = data.get("payload", {}).get("rows", []) or []
    matching = [r for r in rows if r.get("symbol") == symbol]
    if not matching:
        return _err("get_strategy_leaderboard",
                    f"no rows for symbol={symbol} in {universe}",
                    universe=universe, symbol=symbol)

    def _metric(r: dict) -> float:
        s = (r.get("stats") or {}).get(metric)
        try:
            v = float(s) if s is not None else None
        except (TypeError, ValueError):
            return float("-inf")
        if v is None:
            return float("-inf")
        # For drawdown, less-negative is better — sort by absolute
        # distance from zero (descending = "best first").
        if metric == "max_drawdown_pct":
            return -abs(v)
        return v

    def _action_label(r: dict) -> str:
        action = (r.get("current_action") or "HOLD").upper()
        if action == "HOLD":
            return "HOLD-IN" if r.get("in_position") else "HOLD-OUT"
        return action

    # Find the buy_and_hold null model for this symbol so each row
    # can show its sharpe delta vs the do-nothing baseline.
    bh_row = next((r for r in matching if r.get("strategy") == "buy_and_hold"), None)
    bh_sharpe = ((bh_row or {}).get("stats") or {}).get("sharpe")

    matching.sort(key=_metric, reverse=True)
    # Pull the first row's factor / exclusion metadata; the compare
    # engine writes the same values to every row of a given symbol.
    first = matching[0] if matching else {}
    factor_type = first.get("factor_type")
    excluded_strategies = first.get("consensus_excluded_strategies") or []
    leaderboard = []
    for i, r in enumerate(matching):
        stats = r.get("stats") or {}
        sharpe = stats.get("sharpe")
        delta_vs_bh = None
        if sharpe is not None and bh_sharpe is not None:
            try:
                delta_vs_bh = round(float(sharpe) - float(bh_sharpe), 3)
            except (TypeError, ValueError):
                delta_vs_bh = None
        leaderboard.append({
            "_source": (
                f"tradepro://compare/{universe}/leaderboard/{symbol}"
                f"/strategies[{i}]"
            ),
            "rank": i + 1,
            "strategy": r.get("strategy"),
            "strategy_label": r.get("strategy_label"),
            "action": r.get("current_action"),
            "in_position": bool(r.get("in_position")),
            "action_label": _action_label(r),
            "position_since": r.get("position_since"),
            "sharpe": _safe_num(sharpe),
            "cagr_pct": _safe_num(stats.get("cagr_pct")),
            "max_drawdown_pct": _safe_num(stats.get("max_drawdown_pct")),
            "win_rate": _safe_num(stats.get("win_rate")),
            "delta_vs_buy_and_hold": delta_vs_bh,
            "is_top": i == 0,
            "is_baseline": r.get("strategy") == "buy_and_hold",
            # Phase 6.5 instrument-strategy fit. When True, this
            # strategy was excluded from the consensus count for
            # structural-incompatibility reasons (e.g. RSI MR on
            # a momentum-factor ETF). The Sharpe is still valid as
            # backtest history; it just shouldn't influence "should
            # I buy today?".
            "excluded_for_fit": bool(r.get("excluded_for_fit")),
            "excluded_reason": r.get("excluded_reason"),
        })
    return {
        "_source": f"tradepro://compare/{universe}/leaderboard/{symbol}",
        "fetched_at": _now_iso(),
        "universe": universe,
        "symbol": symbol,
        "metric": metric,
        "factor_type": factor_type,
        "incompatible_strategies": list(excluded_strategies),
        "buy_and_hold_sharpe": _safe_num(bh_sharpe),
        "strategies": leaderboard,
    }


def get_instrument_fit(symbol: str) -> dict:
    """Instrument classification + which strategies suit this symbol.

    Returns the symbol's factor classification (momentum / value /
    quality / low_vol / broad_equity / bond / commodity / crypto /
    single_stock / ...) and the list of TradePro strategies that are
    structurally incompatible with that classification. The MTUM /
    RSI-mean-reversion contradiction is the canonical example —
    elevated RSI is what a momentum ETF is *designed* to have, so
    the mean-reversion strategy produces structurally-wrong SELL
    signals on MTUM regardless of its Sharpe on backtest.

    Call this before recommending or excluding a strategy on a
    specific symbol — the consensus engine already uses this to
    filter incompatible votes, but the user-facing answer should
    explain *why* a strategy was suppressed. Cite the classification
    as ``tradepro://instruments/<symbol>/factor_type``.
    """
    if not symbol:
        return _err("get_instrument_fit", "symbol is required")
    from ..factor_types import (
        factor_type_for, incompatible_strategies_for,
        STRATEGIES, INCOMPATIBLE_STRATEGIES,
    )
    ft = factor_type_for(symbol)
    incompatible = incompatible_strategies_for(symbol)
    compatible = tuple(s for s in STRATEGIES if s not in incompatible)
    # Human-readable rationale for the classification.
    reason = {
        "momentum": "Tracks an MSCI Momentum index — holds assets with elevated RSI by construction. Mean-reversion strategies see this as 'overbought' but the asset is doing exactly what it's designed to do.",
        "value": "Tilts toward low PE / cheap fundamentals. Trend strategies are slow but not structurally wrong — value plays can underperform momentum-followers in a momentum regime.",
        "quality": "Diversified high-ROE / low-debt names. Broad strategy fit.",
        "low_vol": "Min-vol construction means the std-dev is bounded by design. Breakout strategies (Donchian, Ichimoku) need volatility to fire meaningfully and tend to false-start on these.",
        "size": "Small-cap tilt — vol is higher and trends/reversions both happen. Broad strategy fit.",
        "growth": "High-growth tech / momentum-adjacent. Broad strategy fit (lean trend-following).",
        "broad_equity": "Market-cap weighted; no factor tilt within. The bread-and-butter case where every strategy is appropriate.",
        "broad_sector": "Sector concentration without a factor tilt within. Broad strategy fit.",
        "country": "Region / country exposure. Broad strategy fit.",
        "bond": "Fixed-income instrument. Donchian breakouts fire rarely on bonds (price tightly bounded by duration / coupon math) and RSI-MR fires on a different timescale than yield moves.",
        "commodity": "Real assets — can trend (oil) or chop (gold). Broad strategy fit; pick by recent regime.",
        "currency_pair": "FX — generally mean-reverting but with regime shifts. Broad strategy fit.",
        "crypto": "Extreme volatility breaks mean-reversion thresholds; 'oversold' RSI readings persist for weeks without the reversion that mean-reversion strategies require.",
        "single_stock": "Individual equity — depends on the stock's regime. Default to broad strategy fit.",
        "unclassified": "Not in the classification table. All strategies vote by default.",
    }.get(ft, "")
    return {
        "_source": f"tradepro://instruments/{symbol}/factor_type",
        "fetched_at": _now_iso(),
        "symbol": symbol,
        "factor_type": ft,
        "classification_reason": reason,
        "compatible_strategies": list(compatible),
        "incompatible_strategies": list(incompatible),
        "incompatibility_table_size": len(INCOMPATIBLE_STRATEGIES),
    }


def get_portfolio() -> dict:
    """User's open Trading 212 positions with computed unrealised
    P&L per row + cross-reference to today's compare verdict.

    Off when Trading 212 isn't configured — returns
    {ok: true, enabled: false, positions: []} so the LLM can
    cleanly say "T212 isn't wired" without needing to handle a
    network error.

    Each position carries:
      ticker / yahooSymbol / instrumentName / currency / isin
      quantity / averagePricePaid / currentPrice
      unrealisedPct / unrealisedAbs / createdAt

    The yahooSymbol field bridges to the cached compare rows so
    follow-up calls can pull today's bucket / swing score for the
    same symbol via get_compare or evaluate_symbols.
    """
    try:
        data = _get("/api/integrations/trading212/positions")
    except ApiUnreachable as e:
        return _unreachable_envelope("get_portfolio", e)
    except Exception as e:  # noqa: BLE001
        return _err("get_portfolio", str(e))
    enabled = bool(data.get("enabled"))
    positions = data.get("positions") or []
    return {
        "_source": f"{_api_base()}/api/integrations/trading212/positions",
        "fetched_at": _now_iso(),
        "ok": True,
        "enabled": enabled,
        "message": data.get("message"),
        "positionCount": data.get("positionCount", len(positions)),
        "positions": positions,
        "fetchedAtUtc": data.get("fetchedAtUtc"),
    }


def get_portfolio_status() -> dict:
    """Trading 212 connection health probe — confirms the API key
    pair reaches the broker. Useful diagnostic before get_portfolio
    when positions look unexpected.

    Returns: {configured, mode (demo|live|disabled), reachable,
    authenticated, detail, rateLimitRemaining}.
    """
    try:
        data = _get("/api/integrations/trading212/status")
    except ApiUnreachable as e:
        return _unreachable_envelope("get_portfolio_status", e)
    except Exception as e:  # noqa: BLE001
        return _err("get_portfolio_status", str(e))
    return {
        "_source": f"{_api_base()}/api/integrations/trading212/status",
        "fetched_at": _now_iso(),
        "ok": True,
        **data,
    }


def get_portfolio_signals(horizon: str = "1y") -> dict:
    """Per-position BUY_MORE / HOLD / TRIM recommendation across the
    user's T212 portfolio.

    Combines `get_portfolio` (positions) with the cached compare
    payload (per-symbol bucket + swing score + market_state) and
    runs `analyse_holding` per position. The same engine the email
    digest and dashboard use, so all three surfaces hand out
    identical advice.

    Args:
        horizon: One of "6mo" / "1y" / "3y" / "5y". Picks the
            threshold profile — 6mo reacts fastest, 5y rides through
            short-term noise. Default "1y" matches the dashboard.

    Returns: list of recommendations with action, narrative, evidence,
    and (for BUY_MORE) the new average cost basis after an equal
    tranche.
    """
    from ..holdings import HORIZON_PROFILES, analyse_holding

    if horizon not in HORIZON_PROFILES:
        return _err(
            "get_portfolio_signals",
            f"unknown horizon {horizon!r}; valid: "
            f"{sorted(HORIZON_PROFILES.keys())}",
        )

    # Pull positions first — without them there's nothing to score.
    portfolio = get_portfolio()
    if not portfolio.get("ok") or not portfolio.get("enabled"):
        return {
            "_source": "get_portfolio_signals",
            "fetched_at": _now_iso(),
            "ok": True,
            "enabled": portfolio.get("enabled", False),
            "message": portfolio.get("message")
                or "Trading 212 not configured — set Trading212__Mode + ApiKey.",
            "horizon": horizon,
            "recommendations": [],
        }
    positions = portfolio.get("positions") or []
    if not positions:
        return {
            "_source": "get_portfolio_signals",
            "fetched_at": _now_iso(),
            "ok": True,
            "enabled": True,
            "horizon": horizon,
            "recommendations": [],
            "message": "No open positions in your T212 account.",
        }

    # Collect every cached compare row across universes — same logic
    # the dashboard uses. Best-rank wins per symbol. Fetched in
    # parallel because Claude Desktop's per-tool timeout doesn't
    # tolerate N serial round-trips when N gets to 5+ universes.
    from concurrent.futures import ThreadPoolExecutor, as_completed

    universes_envelope = list_universes()
    universes = universes_envelope.get("universes") or []
    universe_names = [
        (u.get("universe") if isinstance(u, dict) else u)
        for u in universes
    ]
    universe_names = [n for n in universe_names if n]

    def _fetch_one(name: str):
        try:
            return name, _get("/api/compare/latest", params={"universe": name})
        except Exception:  # noqa: BLE001
            return name, None

    verdict_by_symbol: dict[str, dict] = {}
    if universe_names:
        with ThreadPoolExecutor(max_workers=min(8, len(universe_names))) as ex:
            futures = [ex.submit(_fetch_one, n) for n in universe_names]
            for fut in as_completed(futures):
                _, payload_env = fut.result()
                rows = ((payload_env or {}).get("payload") or {}).get("rows") or []
                for r in rows:
                    sym = (r.get("symbol") or "").upper()
                    if not sym:
                        continue
                    existing = verdict_by_symbol.get(sym)
                    if not existing or (r.get("rank") or 1e9) < (existing.get("rank") or 1e9):
                        verdict_by_symbol[sym] = r

    recommendations = []
    for p in positions:
        yahoo = (p.get("yahooSymbol") or p.get("ticker") or "").upper()
        row = verdict_by_symbol.get(yahoo)
        rec = analyse_holding(p, row, horizon=horizon)
        recommendations.append({
            **rec.to_dict(),
            "yahooSymbol": yahoo,
            "ticker": p.get("ticker"),
            "instrumentName": p.get("instrumentName"),
            "currency": p.get("currency"),
            "quantity": p.get("quantity"),
            "averagePricePaid": p.get("averagePricePaid"),
            "currentPrice": p.get("currentPrice"),
            "unrealisedPct": p.get("unrealisedPct"),
            "unrealisedAbs": p.get("unrealisedAbs"),
            "today_bucket": (row or {}).get("bucket"),
            "today_swing_score": ((row or {}).get("swing_score") or {}).get("total"),
        })

    # Sort to match the dashboard / email digest ordering: TRIM →
    # BUY_MORE → HOLD, then by |P&L %| desc.
    priority = {"TRIM": 0, "BUY_MORE": 1, "HOLD": 2}
    recommendations.sort(
        key=lambda r: (
            priority.get(r["action"], 9),
            -abs(float(r.get("unrealisedPct") or 0.0)),
        ),
    )

    counts = {a: 0 for a in ("BUY_MORE", "HOLD", "TRIM")}
    for r in recommendations:
        counts[r["action"]] = counts.get(r["action"], 0) + 1

    return {
        "_source": "get_portfolio_signals",
        "fetched_at": _now_iso(),
        "ok": True,
        "enabled": True,
        "horizon": horizon,
        "counts": counts,
        "recommendations": recommendations,
    }


def get_hypothetical_return(
    symbol: str,
    from_date: str,
    to_date: str | None = None,
    quantity: float | None = None,
) -> dict:
    """Answers "if I'd bought ``symbol`` on ``from_date``, what would
    my return be as of ``to_date`` (default: today)?".

    Uses split-adjusted closes from the TradePro candles endpoint, so
    a stock that 4-for-1 split between the buy and sell dates produces
    the right number — total return reflects the position you'd
    actually hold today, not the headline price.

    Inputs:
        symbol     — ticker (e.g. AAPL, VOO, BARC.L, VWRP.L)
        from_date  — YYYY-MM-DD; if the market was closed that day,
                     the first trading day at or after this date is
                     used (and the response says so).
        to_date    — YYYY-MM-DD or None for "today's most recent close"
        quantity   — optional number of shares to also report a dollar
                     return on. Omit for percent-only.

    Returns: buy/sell prices, total return %, total dollar return (if
    quantity given), peak/trough between the two dates, max drawdown,
    annualised return (when the holding period is >= 30 days).

    Cite as ``tradepro://hypothetical/<symbol>/<from>/<to>``.
    """
    if not symbol or not from_date:
        return _err("get_hypothetical_return", "symbol and from_date are required")
    try:
        candles_resp = _get("/api/marketdata/candles", params={
            "symbol": symbol,
            "from": from_date,
            "to": to_date or _now_iso()[:10],
            "interval": "1d",
        })
    except ApiUnreachable as e:
        return _unreachable_envelope("get_hypothetical_return", e,
                                     symbol=symbol, from_date=from_date)
    except Exception as e:  # noqa: BLE001
        return _err("get_hypothetical_return", str(e),
                    symbol=symbol, from_date=from_date)

    candles = candles_resp.get("candles") or []
    if not candles:
        return _err("get_hypothetical_return",
                    f"no candles for {symbol} between {from_date} and {to_date or 'today'} "
                    f"— check the symbol spelling and the date range",
                    symbol=symbol, from_date=from_date, to_date=to_date)

    # Find first trading day on/after from_date, and last on/before to_date.
    # We use adjOrClose throughout (split + dividend adjusted) so the math
    # is total-return correct.
    def _close(c: dict) -> float | None:
        v = c.get("adjOrClose") or c.get("adjustedClose") or c.get("close")
        try:
            return float(v) if v is not None else None
        except (TypeError, ValueError):
            return None

    first = candles[0]
    last = candles[-1]
    buy_price = _close(first)
    sell_price = _close(last)
    if buy_price is None or sell_price is None or buy_price <= 0:
        return _err("get_hypothetical_return",
                    "candles present but no usable adjusted close found",
                    symbol=symbol)

    return_pct = round(((sell_price / buy_price) - 1.0) * 100, 3)

    # Holding-period stats: peak, trough, max drawdown from peak to
    # subsequent trough. Useful for "how much pain along the way" —
    # a +30% return with -25% mid-period drawdown is a different
    # experience than +30% in a straight line.
    closes = [_close(c) for c in candles if _close(c) is not None]
    peak_idx = 0
    peak = closes[0]
    max_dd_pct = 0.0
    for i, c in enumerate(closes):
        if c > peak:
            peak = c
            peak_idx = i
        elif peak > 0:
            dd = (c / peak - 1.0) * 100
            if dd < max_dd_pct:
                max_dd_pct = dd
    peak_val = max(closes)
    trough_val = min(closes)

    # Annualise when the window is long enough to be meaningful.
    from datetime import datetime
    fmt = "%Y-%m-%dT%H:%M:%S%z"
    first_ts = first.get("timestamp")
    last_ts = last.get("timestamp")
    days_held = None
    annualised_return_pct = None
    try:
        if first_ts and last_ts:
            d0 = datetime.fromisoformat(first_ts.replace("Z", "+00:00"))
            d1 = datetime.fromisoformat(last_ts.replace("Z", "+00:00"))
            days_held = max(1, (d1 - d0).days)
            if days_held >= 30:
                years = days_held / 365.25
                annualised_return_pct = round(
                    ((sell_price / buy_price) ** (1.0 / years) - 1.0) * 100, 3)
    except Exception:  # noqa: BLE001
        days_held = None

    qty_return = None
    if quantity is not None:
        try:
            qty = float(quantity)
            qty_return = round((sell_price - buy_price) * qty, 2)
        except (TypeError, ValueError):
            qty_return = None

    return {
        "_source": f"tradepro://hypothetical/{symbol}/{from_date}/{to_date or 'today'}",
        "fetched_at": _now_iso(),
        "ok": True,
        "symbol": symbol,
        "from_date": from_date,
        "to_date": to_date,
        "first_trading_day": first.get("timestamp"),
        "last_trading_day": last.get("timestamp"),
        "days_held": days_held,
        "buy_price": round(buy_price, 4),
        "sell_price": round(sell_price, 4),
        "return_pct": return_pct,
        "annualised_return_pct": annualised_return_pct,
        "peak_close": round(peak_val, 4),
        "trough_close": round(trough_val, 4),
        "max_drawdown_pct": round(max_dd_pct, 3),
        "quantity": quantity,
        "dollar_return": qty_return,
        "adjustment_note": (
            "Prices are split + dividend adjusted (adjOrClose). A 4-for-1 split "
            "between buy and sell dates is already baked in — return reflects "
            "the position you'd hold today, not the headline price."
        ),
    }


def get_horizon_signals(symbol: str) -> dict:
    """Three independent horizon verdicts (swing / long-term / passive)
    for a single symbol — TRADEPRO-SPEC-001 §6.3.

    Looks up the symbol's most-recent compare row across cached
    universes (best-rank wins, same as the dashboard) and runs
    `classify_horizons` on it. Returns each horizon's signal grade
    (BUY / WATCH / AVOID / N/A), 0-8 score, reasons list, optional
    entry note, plus the `range_pct` percentile.

    When the symbol isn't in any cached universe, falls back to a
    fresh on-demand evaluate via the existing `evaluate_symbols`
    pathway so the tool can still answer for one-offs.

    Target: returns in <2s per spec acceptance criterion.
    """
    if not symbol or not symbol.strip():
        return _err("get_horizon_signals", "symbol is required")
    sym_u = symbol.strip().upper()

    # Walk cached universes for the best-rank row. Reuses the same
    # logic get_portfolio_signals uses — single source of truth.
    from concurrent.futures import ThreadPoolExecutor, as_completed
    universes_envelope = list_universes()
    universes = universes_envelope.get("universes") or []
    universe_names = [
        (u.get("universe") if isinstance(u, dict) else u)
        for u in universes
    ]
    universe_names = [n for n in universe_names if n]

    def _fetch(name: str):
        try:
            return _get("/api/compare/latest", params={"universe": name})
        except Exception:  # noqa: BLE001
            return None

    best_row: dict | None = None
    best_rank = 1e9
    found_universe: str | None = None
    if universe_names:
        with ThreadPoolExecutor(max_workers=min(8, len(universe_names))) as ex:
            future_to_name = {
                ex.submit(_fetch, n): n for n in universe_names
            }
            for fut in as_completed(future_to_name):
                payload_env = fut.result()
                rows = ((payload_env or {}).get("payload") or {}).get("rows") or []
                for r in rows:
                    if (r.get("symbol") or "").upper() != sym_u:
                        continue
                    rank = r.get("rank") or 1e9
                    if rank < best_rank:
                        best_rank = rank
                        best_row = r
                        found_universe = future_to_name[fut]

    if best_row is None:
        return {
            "_source": "get_horizon_signals",
            "fetched_at": _now_iso(),
            "ok": True,
            "symbol": sym_u,
            "in_cache": False,
            "message": (
                f"{sym_u} isn't in any cached universe. Run "
                f"evaluate_symbols([\"{sym_u}\"]) for an ad-hoc verdict, "
                f"then re-call this tool."
            ),
            "horizons": None,
        }

    # Use the row's existing horizon_classification field when the
    # comparator already attached one — saves a recompute. Fall back
    # to live classification when running against an old cache.
    payload: dict
    if best_row.get("horizon_classification"):
        payload = best_row["horizon_classification"]
    else:
        from ..horizons import classify_horizons
        payload = classify_horizons(best_row).to_dict()

    return {
        "_source": "get_horizon_signals",
        "fetched_at": _now_iso(),
        "ok": True,
        "symbol": sym_u,
        "in_cache": True,
        "universe": found_universe,
        "rank_in_universe": best_row.get("rank"),
        "today_bucket": best_row.get("bucket"),
        "today_swing_score": (best_row.get("swing_score") or {}).get("total"),
        "horizons": payload,
    }


def search_t212_instruments(query: str, limit: int = 10) -> dict:
    """Search the cached Trading 212 instruments registry by ticker /
    short-name / full-name. Used to verify whether a symbol is
    actually tradeable in the user's T212 account before recommending
    it. Off when T212 isn't configured."""
    if not query or not query.strip():
        return _err("search_t212_instruments", "query is required")
    try:
        data = _get(
            "/api/integrations/trading212/instruments",
            params={"q": query.strip(), "limit": max(1, min(int(limit), 50))},
        )
    except ApiUnreachable as e:
        return _unreachable_envelope("search_t212_instruments", e, query=query)
    except Exception as e:  # noqa: BLE001
        return _err("search_t212_instruments", str(e), query=query)
    return {
        "_source": f"{_api_base()}/api/integrations/trading212/instruments",
        "fetched_at": _now_iso(),
        "ok": True,
        "query": query,
        **data,
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


def get_fundamentals(symbol: str) -> dict:
    """Fundamentals snapshot for a single ticker — expense ratio, AUM,
    dividend yield, top-10 holdings, sector weights, fund family,
    inception date, summary text. ETF-flavoured fields (distribution
    yield) and bond-flavoured fields (YTM, duration) populate when
    Yahoo has them. Pulls live; no caching beyond the request scope.

    Use this to answer the long-term-investing questions the BUY/WAIT
    classifier deliberately doesn't: 'is this fund expensive?', 'what
    am I actually exposed to?', 'is the dividend yield holding up?'.
    """
    if not symbol or not symbol.strip():
        return _err("get_fundamentals", "symbol is required")
    sym = symbol.strip().upper()
    try:
        from ..fundamentals import fetch_fundamentals
        f = fetch_fundamentals(sym)
    except Exception as e:  # noqa: BLE001
        return _err("get_fundamentals", str(e), symbol=sym)
    out = f.to_dict()
    out["_source"] = f"live://fundamentals/{sym}"
    return out


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
        # Apply the range veto here too — evaluate_symbols is the
        # ad-hoc analogue of the comparator pipeline, so its bucket
        # should match what compare emits as far as data allows. Full
        # sentiment / horizon demotion requires news + horizon
        # classification which we don't pull on the fast path; this
        # at least catches "BUY at the 52w high" cases.
        from ..compare import apply_horizon_and_range_demotion as _veto
        bucket, bucket_reason, _horizon_demoted = _veto(
            bucket=bucket, reason=bucket_reason,
            horizon_classification=None,
            range_pct=ms.range_position_pct,
        )
        # Coherence enforcement (BUG-002 fix). Reuse the same helper
        # compare.py uses so MCP output matches the comparator row
        # shape exactly: market_state.entry_signal == bucket, plus a
        # top-level `coherence` block with raw_entry_signal preserved
        # for diagnostics.
        from ..compare import enforce_coherence as _enforce_coherence
        row = {
            "symbol": sym,
            "market_state": ms.to_dict(),
        }
        # MCP fast path doesn't compute sentiment / horizon demotion,
        # so flag both as False; supersede_reason will fall through to
        # consensus_or_factor_fit if the bucket diverges from the raw
        # entry_signal here.
        _enforce_coherence(
            row, bucket=bucket, sentiment_demoted=False, horizon_demoted=False,
        )
        ms_dict = row["market_state"]
        coherence = row["coherence"]
        results.append({
            "_source": f"live://evaluate/{sym}",
            "symbol": sym,
            "ok": True,
            "bucket": bucket,
            "bucket_reason": bucket_reason,
            "long_count": long_count,
            "total_strategies": total,
            "market_state": ms_dict,
            "coherence": coherence,
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


def _post(path: str, json_body: dict | None = None, params: dict | None = None,
          timeout: float | None = None) -> dict:
    """POST to the TradePro API. Used by control-plane MCP tools
    (approve / reject paper orders, signal scans, hitrate runs)."""
    url = f"{_api_base()}{path}"
    try:
        resp = requests.post(
            url, params=params or {}, json=json_body or {},
            headers=_auth_headers(),
            timeout=timeout if timeout is not None else _default_timeout(),
        )
    except (requests.ConnectionError, requests.Timeout) as e:
        raise ApiUnreachable(_api_base(), e) from e
    resp.raise_for_status()
    if not resp.text:
        return {}
    try:
        return resp.json()
    except ValueError:
        return {"raw": resp.text}


def _put(path: str, json_body: dict, timeout: float | None = None) -> dict:
    url = f"{_api_base()}{path}"
    try:
        resp = requests.put(
            url, json=json_body,
            headers=_auth_headers(),
            timeout=timeout if timeout is not None else _default_timeout(),
        )
    except (requests.ConnectionError, requests.Timeout) as e:
        raise ApiUnreachable(_api_base(), e) from e
    resp.raise_for_status()
    return resp.json() if resp.text else {}


# ---------------------------------------------------------------------------
# Paper trading: pending orders, fills, snapshots, backtest reports
# ---------------------------------------------------------------------------

def get_pending_orders() -> dict:
    """Paper orders awaiting human approval (manual placement mode).
    Each row carries `orderId`, `symbol`, `side`, `quantity`,
    `barAtEmitClose`, `t212Ticker`, the strategy that emitted it,
    and `state` (Pending / Placed / Failed / Rejected).

    Use to answer "what trades is the system asking me to confirm?" —
    pair with approve_paper_order / reject_paper_order to act on them.

    Cite as `tradepro://paper/pending-orders`.
    """
    try:
        rows = _get("/api/paper/pending-orders/")
    except ApiUnreachable as e:
        return _unreachable_envelope("get_pending_orders", e)
    except Exception as e:  # noqa: BLE001
        return _err("get_pending_orders", str(e))
    items = rows if isinstance(rows, list) else (rows.get("items") if isinstance(rows, dict) else [])
    pending = [r for r in (items or []) if (r.get("state") or "").lower() == "pending"]
    return {
        "_source": "tradepro://paper/pending-orders",
        "fetched_at": _now_iso(),
        "ok": True,
        "total": len(items or []),
        "pending_count": len(pending),
        "orders": items or [],
    }


def approve_paper_order(order_id: str) -> dict:
    """Approve a Pending paper order — places the market order against
    Trading 212 using the backend's own T212 client and records the
    risk-decision event on the orders log. Returns the post-approval
    order row (`state` becomes Placed or Failed depending on T212).

    Destructive in the sense that it actually places a real order in
    the T212 account configured on the server (demo or live, depending
    on Trading212__Mode). Only call after the user has confirmed.

    Cite as `tradepro://paper/pending-orders/{order_id}/approve`.
    """
    if not order_id:
        return _err("approve_paper_order", "order_id is required")
    try:
        resp = _post(f"/api/paper/pending-orders/{order_id}/approve")
    except ApiUnreachable as e:
        return _unreachable_envelope("approve_paper_order", e, order_id=order_id)
    except Exception as e:  # noqa: BLE001
        return _err("approve_paper_order", str(e), order_id=order_id)
    return {
        "_source": f"tradepro://paper/pending-orders/{order_id}/approve",
        "fetched_at": _now_iso(),
        "ok": True,
        "order": resp,
    }


def reject_paper_order(order_id: str, reason: str | None = None) -> dict:
    """Reject a Pending paper order. Records `reject` on the orders
    log with the supplied reason. No T212 call is made. Use when the
    system emitted a trade you disagree with — the orders table keeps
    the trail so we can audit the rejection later.

    Cite as `tradepro://paper/pending-orders/{order_id}/reject`.
    """
    if not order_id:
        return _err("reject_paper_order", "order_id is required")
    params = {"reason": reason} if reason else None
    try:
        resp = _post(f"/api/paper/pending-orders/{order_id}/reject", params=params)
    except ApiUnreachable as e:
        return _unreachable_envelope("reject_paper_order", e, order_id=order_id)
    except Exception as e:  # noqa: BLE001
        return _err("reject_paper_order", str(e), order_id=order_id)
    return {
        "_source": f"tradepro://paper/pending-orders/{order_id}/reject",
        "fetched_at": _now_iso(),
        "ok": True,
        "order": resp,
    }


def list_orders(symbol: str | None = None, limit: int = 100) -> dict:
    """Most-recent orders from the event-sourced orders log. Filter
    by symbol or fetch the global feed. Each row carries the strategy,
    side, quantity, emit timestamp, and `decision_trace` — the
    auditable record of what data supported the order.

    Cite as `tradepro://orders` or `tradepro://orders/{symbol}`.
    """
    params: dict[str, Any] = {"limit": max(1, min(int(limit or 100), 500))}
    if symbol:
        params["symbol"] = symbol
    try:
        rows = _get("/api/orders/", params=params)
    except ApiUnreachable as e:
        return _unreachable_envelope("list_orders", e, symbol=symbol)
    except Exception as e:  # noqa: BLE001
        return _err("list_orders", str(e), symbol=symbol)
    return {
        "_source": f"tradepro://orders{'/'+symbol if symbol else ''}",
        "fetched_at": _now_iso(),
        "ok": True,
        "symbol": symbol,
        "count": len(rows) if isinstance(rows, list) else None,
        "orders": rows if isinstance(rows, list) else (rows.get("items") if isinstance(rows, dict) else []),
    }


def get_order(order_id: str) -> dict:
    """Single order + its fills, joined. Returns the order header
    (strategy, side, qty, decision_trace) and the fill list with
    actual broker prices. Use to drill into an order the system
    emitted and trace why it fired.

    Cite as `tradepro://orders/{order_id}`.
    """
    if not order_id:
        return _err("get_order", "order_id is required")
    try:
        payload = _get(f"/api/orders/{order_id}")
    except ApiUnreachable as e:
        return _unreachable_envelope("get_order", e, order_id=order_id)
    except Exception as e:  # noqa: BLE001
        return _err("get_order", str(e), order_id=order_id)
    return {
        "_source": f"tradepro://orders/{order_id}",
        "fetched_at": _now_iso(),
        "ok": True,
        **(payload if isinstance(payload, dict) else {"payload": payload}),
    }


def get_paper_snapshot(session_label: str | None = None) -> dict:
    """Latest paper-engine snapshot for one session (positions +
    recent fills + P&L), or the list of recent sessions when
    `session_label` is None. Powers the Live tab on the Paper page.

    Cite as `tradepro://paper/snapshots` or
    `tradepro://paper/snapshots/{session_label}`.
    """
    try:
        if not session_label:
            rows = _get("/api/paper/snapshots/")
            return {
                "_source": "tradepro://paper/snapshots",
                "fetched_at": _now_iso(),
                "ok": True,
                "count": len(rows) if isinstance(rows, list) else None,
                "sessions": rows,
            }
        payload = _get(f"/api/paper/snapshots/{session_label}")
        return {
            "_source": f"tradepro://paper/snapshots/{session_label}",
            "fetched_at": _now_iso(),
            "ok": True,
            "session_label": session_label,
            **(payload if isinstance(payload, dict) else {"payload": payload}),
        }
    except ApiUnreachable as e:
        return _unreachable_envelope("get_paper_snapshot", e, session_label=session_label)
    except Exception as e:  # noqa: BLE001
        return _err("get_paper_snapshot", str(e), session_label=session_label)


def get_paper_backtest_reports(report_id: str | None = None, limit: int = 50) -> dict:
    """When `report_id` is None, list the most-recent paper-trading
    backtest reports (newest first). When given, returns the full
    report payload (per-strategy results, equity curve, drawdown).

    The Backtest page uses these to compare strategies side-by-side
    on the same symbol+date range.

    Cite as `tradepro://paper/backtest/reports[/{report_id}]`.
    """
    try:
        if report_id:
            payload = _get(f"/api/paper/backtest/reports/{report_id}")
            return {
                "_source": f"tradepro://paper/backtest/reports/{report_id}",
                "fetched_at": _now_iso(),
                "ok": True,
                "report_id": report_id,
                **(payload if isinstance(payload, dict) else {"payload": payload}),
            }
        rows = _get("/api/paper/backtest/reports", params={"limit": int(limit or 50)})
        return {
            "_source": "tradepro://paper/backtest/reports",
            "fetched_at": _now_iso(),
            "ok": True,
            "count": len(rows) if isinstance(rows, list) else None,
            "reports": rows,
        }
    except ApiUnreachable as e:
        return _unreachable_envelope("get_paper_backtest_reports", e, report_id=report_id)
    except Exception as e:  # noqa: BLE001
        return _err("get_paper_backtest_reports", str(e), report_id=report_id)


def list_paper_strategies() -> dict:
    """Catalog of registered paper-trading strategies the Mac engine
    has pushed (`tradepro-paper-strategies-push`). 404-shaped envelope
    when the Mac hasn't pushed yet — UI shows a hint to run the push.

    Cite as `tradepro://paper/strategies`.
    """
    try:
        payload = _get("/api/paper/strategies/")
    except ApiUnreachable as e:
        return _unreachable_envelope("list_paper_strategies", e)
    except requests.HTTPError as e:
        if e.response is not None and e.response.status_code == 404:
            return {
                "_source": "tradepro://paper/strategies",
                "fetched_at": _now_iso(),
                "ok": True,
                "registered": False,
                "message": "Mac hasn't run tradepro-paper-strategies-push yet — catalog empty.",
                "strategies": [],
            }
        return _err("list_paper_strategies", str(e))
    except Exception as e:  # noqa: BLE001
        return _err("list_paper_strategies", str(e))
    return {
        "_source": "tradepro://paper/strategies",
        "fetched_at": _now_iso(),
        "ok": True,
        "registered": True,
        **(payload if isinstance(payload, dict) else {"payload": payload}),
    }


# ---------------------------------------------------------------------------
# Track-record validation: hitrate, signal scan, evaluate one signal
# ---------------------------------------------------------------------------

def get_hitrate(
    symbol: str,
    strategy: str,
    lookback_years: int = 5,
    horizon_days: int = 20,
) -> dict:
    """Historical hit-rate for one (symbol, strategy) — out of N past
    signal firings, how many would have made money over the next
    `horizon_days`? Answers "does this strategy actually work on this
    symbol?" with backtested evidence, not just current signal value.

    Cite as `tradepro://hitrate/{symbol}/{strategy}`.
    """
    if not symbol or not strategy:
        return _err("get_hitrate", "symbol and strategy are required")
    body = {
        "symbol": symbol,
        "strategy": strategy,
        "lookbackYears": int(lookback_years),
        "horizonDays": int(horizon_days),
    }
    try:
        payload = _post("/api/signals/hitrate", json_body=body)
    except ApiUnreachable as e:
        return _unreachable_envelope("get_hitrate", e, symbol=symbol, strategy=strategy)
    except Exception as e:  # noqa: BLE001
        return _err("get_hitrate", str(e), symbol=symbol, strategy=strategy)
    return {
        "_source": f"tradepro://hitrate/{symbol}/{strategy}",
        "fetched_at": _now_iso(),
        "ok": True,
        "symbol": symbol,
        "strategy": strategy,
        **(payload if isinstance(payload, dict) else {"payload": payload}),
    }


def evaluate_signal(
    symbol: str,
    strategy: str,
    lookback_years: int = 5,
) -> dict:
    """Run one strategy against one symbol right now and return the
    decision (BUY / HOLD / SELL + supporting indicators). Use when
    the user asks "what does RSI mean reversion say about AAPL?"
    or to verify the cache against a fresh compute.

    Cite as `tradepro://signals/{symbol}/{strategy}`.
    """
    if not symbol or not strategy:
        return _err("evaluate_signal", "symbol and strategy are required")
    body = {
        "symbol": symbol,
        "strategy": strategy,
        "lookbackYears": int(lookback_years),
    }
    try:
        payload = _post("/api/signals/evaluate", json_body=body)
    except ApiUnreachable as e:
        return _unreachable_envelope("evaluate_signal", e, symbol=symbol, strategy=strategy)
    except Exception as e:  # noqa: BLE001
        return _err("evaluate_signal", str(e), symbol=symbol, strategy=strategy)
    return {
        "_source": f"tradepro://signals/{symbol}/{strategy}",
        "fetched_at": _now_iso(),
        "ok": True,
        **(payload if isinstance(payload, dict) else {"payload": payload}),
    }


def run_signal_scan(
    strategy: str,
    universe: str | None = None,
    symbols_csv: str | None = None,
) -> dict:
    """Run one strategy across many symbols at once — either a whole
    universe (e.g. "uk-etfs") or a comma-separated symbol list. Use
    to find current BUY candidates ("which uk-etfs are firing
    bollinger_bounce today?").

    Cite as `tradepro://scan/{strategy}`.
    """
    if not strategy:
        return _err("run_signal_scan", "strategy is required")
    body: dict[str, Any] = {"strategy": strategy}
    if universe:
        body["universe"] = universe
    if symbols_csv:
        body["symbols"] = [s.strip() for s in symbols_csv.split(",") if s.strip()]
    try:
        payload = _post("/api/signals/scan", json_body=body)
    except ApiUnreachable as e:
        return _unreachable_envelope("run_signal_scan", e, strategy=strategy)
    except Exception as e:  # noqa: BLE001
        return _err("run_signal_scan", str(e), strategy=strategy)
    return {
        "_source": f"tradepro://scan/{strategy}",
        "fetched_at": _now_iso(),
        "ok": True,
        "strategy": strategy,
        **(payload if isinstance(payload, dict) else {"payload": payload}),
    }


# ---------------------------------------------------------------------------
# Event awareness: earnings, analyst recommendations, analyst upgrades
# ---------------------------------------------------------------------------

def get_earnings_calendar(symbol: str, days: int = 30) -> dict:
    """Upcoming earnings dates for `symbol` over the next `days`
    (default 30, max 90). Returns the empty-but-ok envelope when
    Finnhub isn't configured server-side. Use to flag "MSFT reports
    in 5 days — position-into-earnings volatility risk".

    Cite as `tradepro://finnhub/earnings/{symbol}`.
    """
    if not symbol:
        return _err("get_earnings_calendar", "symbol is required")
    try:
        payload = _get(
            "/api/integrations/finnhub/earnings-calendar",
            params={"symbol": symbol, "days": int(days)},
        )
    except ApiUnreachable as e:
        return _unreachable_envelope("get_earnings_calendar", e, symbol=symbol)
    except Exception as e:  # noqa: BLE001
        return _err("get_earnings_calendar", str(e), symbol=symbol)
    return {
        "_source": f"tradepro://finnhub/earnings/{symbol}",
        "fetched_at": _now_iso(),
        "ok": True,
        **(payload if isinstance(payload, dict) else {"payload": payload}),
    }


def get_analyst_recommendations(symbol: str) -> dict:
    """Monthly buy/hold/sell counts from sell-side analysts (last
    ~12 months). Includes a pre-computed `momChange` — positive
    means analysts are turning bullish month-over-month. Returns
    enabled=false when Finnhub isn't configured.

    Cite as `tradepro://finnhub/recommendations/{symbol}`.
    """
    if not symbol:
        return _err("get_analyst_recommendations", "symbol is required")
    try:
        payload = _get(
            "/api/integrations/finnhub/recommendations",
            params={"symbol": symbol},
        )
    except ApiUnreachable as e:
        return _unreachable_envelope("get_analyst_recommendations", e, symbol=symbol)
    except Exception as e:  # noqa: BLE001
        return _err("get_analyst_recommendations", str(e), symbol=symbol)
    return {
        "_source": f"tradepro://finnhub/recommendations/{symbol}",
        "fetched_at": _now_iso(),
        "ok": True,
        **(payload if isinstance(payload, dict) else {"payload": payload}),
    }


def get_analyst_upgrades(symbol: str, days: int = 30) -> dict:
    """Recent analyst upgrade/downgrade events for `symbol` over
    the last `days` (1-180, default 30). Includes summary counts
    (`upgradeCount`, `downgradeCount`, `netDelta`) so a caller
    can decide "are analysts piling in or fleeing?" in one read.

    Cite as `tradepro://finnhub/upgrades/{symbol}`.
    """
    if not symbol:
        return _err("get_analyst_upgrades", "symbol is required")
    try:
        payload = _get(
            "/api/integrations/finnhub/upgrades",
            params={"symbol": symbol, "days": int(days)},
        )
    except ApiUnreachable as e:
        return _unreachable_envelope("get_analyst_upgrades", e, symbol=symbol)
    except Exception as e:  # noqa: BLE001
        return _err("get_analyst_upgrades", str(e), symbol=symbol)
    return {
        "_source": f"tradepro://finnhub/upgrades/{symbol}",
        "fetched_at": _now_iso(),
        "ok": True,
        **(payload if isinstance(payload, dict) else {"payload": payload}),
    }


# ---------------------------------------------------------------------------
# Raw market data: candles
# ---------------------------------------------------------------------------

def get_candles(
    symbol: str,
    from_date: str,
    to_date: str | None = None,
    interval: str = "1d",
    provider: str | None = None,
) -> dict:
    """Raw OHLCV candles for one symbol between two dates. Default
    interval `1d` — for daily strategy work this is what you want.
    Use `get_hypothetical_return` instead if you just want "return
    if I'd bought on X" — this tool is for callers that need the
    bar-by-bar series.

    Cite as `tradepro://candles/{symbol}/{from}/{to}`.
    """
    if not symbol or not from_date:
        return _err("get_candles", "symbol and from_date are required")
    params: dict[str, Any] = {
        "symbol": symbol,
        "from": from_date,
        "to": to_date or _now_iso()[:10],
        "interval": interval or "1d",
    }
    if provider:
        params["provider"] = provider
    try:
        payload = _get("/api/marketdata/candles", params=params)
    except ApiUnreachable as e:
        return _unreachable_envelope("get_candles", e, symbol=symbol)
    except Exception as e:  # noqa: BLE001
        return _err("get_candles", str(e), symbol=symbol)
    candles = payload.get("candles") if isinstance(payload, dict) else None
    return {
        "_source": f"tradepro://candles/{symbol}/{from_date}/{to_date or 'today'}",
        "fetched_at": _now_iso(),
        "ok": True,
        "symbol": symbol,
        "from_date": from_date,
        "to_date": to_date,
        "interval": interval,
        "count": len(candles) if isinstance(candles, list) else None,
        "candles": candles or [],
    }


# ---------------------------------------------------------------------------
# Settings + control plane
# ---------------------------------------------------------------------------

def get_settings() -> dict:
    """Live application settings — sentiment thresholds, paper-trading
    placement mode (auto|manual). Use before recommending settings
    changes so you read the current values, not a stale assumption.

    Cite as `tradepro://settings`.
    """
    try:
        payload = _get("/api/settings")
    except ApiUnreachable as e:
        return _unreachable_envelope("get_settings", e)
    except Exception as e:  # noqa: BLE001
        return _err("get_settings", str(e))
    return {
        "_source": "tradepro://settings",
        "fetched_at": _now_iso(),
        "ok": True,
        **(payload if isinstance(payload, dict) else {"payload": payload}),
    }


def set_paper_placement_mode(mode: str) -> dict:
    """Flip paper-trading placement between `auto` (engine places
    orders directly) and `manual` (orders queue as pending for the
    user to approve). Read existing settings, swap the Paper block's
    placementMode, PUT back.

    Destructive: this changes how the Mac engine will behave on the
    NEXT run. Confirm with the user before flipping live.

    Cite as `tradepro://settings/paper/placementMode`.
    """
    mode = (mode or "").strip().lower()
    if mode not in ("auto", "manual"):
        return _err("set_paper_placement_mode", "mode must be 'auto' or 'manual'")
    try:
        current = _get("/api/settings")
        if not isinstance(current, dict):
            return _err("set_paper_placement_mode", "settings response was not an object")
        paper = dict(current.get("paper") or {})
        paper["placementMode"] = mode
        updated_body = {**current, "paper": paper}
        updated = _put("/api/settings", json_body=updated_body)
    except ApiUnreachable as e:
        return _unreachable_envelope("set_paper_placement_mode", e, mode=mode)
    except Exception as e:  # noqa: BLE001
        return _err("set_paper_placement_mode", str(e), mode=mode)
    return {
        "_source": "tradepro://settings/paper/placementMode",
        "fetched_at": _now_iso(),
        "ok": True,
        "mode": mode,
        "settings": updated,
    }


def list_watchlists() -> dict:
    """Names of every watchlist registered on the server. Use to
    discover what symbol groups exist before drilling into one
    with `get_watchlist`.

    Cite as `tradepro://watchlists`.
    """
    try:
        payload = _get("/api/watchlists/")
    except ApiUnreachable as e:
        return _unreachable_envelope("list_watchlists", e)
    except Exception as e:  # noqa: BLE001
        return _err("list_watchlists", str(e))
    return {
        "_source": "tradepro://watchlists",
        "fetched_at": _now_iso(),
        "ok": True,
        **(payload if isinstance(payload, dict) else {"payload": payload}),
    }


def get_watchlist(name: str) -> dict:
    """Members of one watchlist by name. 404-shaped envelope when the
    name doesn't exist — caller should pick a name from list_watchlists.

    Cite as `tradepro://watchlists/{name}`.
    """
    if not name:
        return _err("get_watchlist", "name is required")
    try:
        payload = _get(f"/api/watchlists/{name}")
    except ApiUnreachable as e:
        return _unreachable_envelope("get_watchlist", e, name=name)
    except requests.HTTPError as e:
        if e.response is not None and e.response.status_code == 404:
            return _err("get_watchlist",
                        f"no watchlist named '{name}' — call list_watchlists to see the available names",
                        name=name)
        return _err("get_watchlist", str(e), name=name)
    except Exception as e:  # noqa: BLE001
        return _err("get_watchlist", str(e), name=name)
    return {
        "_source": f"tradepro://watchlists/{name}",
        "fetched_at": _now_iso(),
        "ok": True,
        "name": name,
        **(payload if isinstance(payload, dict) else {"payload": payload}),
    }


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

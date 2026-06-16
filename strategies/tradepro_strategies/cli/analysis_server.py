"""Symbol Analysis Card sidecar — small FastAPI service that exposes
the Track 2 orchestrator over HTTP for the frontend.

  GET /symbol/{ticker}/analysis?universe={universe}&drawdown_pct={pct}

Returns the dict produced by
``core_portfolio.build_symbol_analysis_card`` — both technical and
fundamental lenses fused into one card with a
``primary_horizon_recommendation`` token.

Why a sidecar (same reasoning as ``extract_server.py``): the
orchestrator lives in Python (Quality / Valuation / Dividend /
Entry-Timing / long-term grade — all yfinance + numpy + pandas
work), not in .NET. Browser → Python sidecar directly during local
dev; production deployments route browser → .NET API → Python
sidecar so the .NET layer still does auth + rate-limit.

Default port 8002. Override:
    TRADEPRO_ANALYSIS_PORT=9002 uv run tradepro-analysis-server

The technical block is sourced from the .NET API's compare cache
when ``universe`` is supplied — same shape as
``mcp.tools.get_symbol_analysis``. When the .NET API can't be
reached, the card degrades gracefully to fundamental-only with a
warning in the envelope.

Stateless. No persistence — every request re-fetches.
"""
from __future__ import annotations

import logging
import os

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from ..core_portfolio import build_symbol_analysis_card

_log = logging.getLogger("tradepro.cli.analysis_server")


# Friendly label for the per-strategy horizon token the comparator attaches
# (factor_types.Horizon). This is the "is it a swing / short / long-term
# trade?" timeline the user asked for, surfaced verbatim from the same
# classification Decide uses — never a parallel definition.
_HORIZON_LABEL = {
    "intraday":    "Intraday",
    "swing":       "Swing (days–weeks)",
    "medium_term": "Short-term (weeks–months)",
    "long_term":   "Long-term (months–years)",
}


def _compute_canonical_verdict(symbol: str, lookback_years: int) -> dict:
    """Run the FULL comparator pipeline for ONE symbol, live, and return its
    canonical verdict.

    This is the single source of truth that kills cross-surface
    contradictions: Decide reads the comparator's cached batch output, and
    this runs the IDENTICAL ``compare()`` function for one ad-hoc symbol on
    demand. Same bucket, same demotions, same horizon — only fresher. No
    logic is re-implemented here; we just restrict the symbol list to one
    and lift the canonical row out of the payload.
    """
    from datetime import datetime, timedelta, timezone

    from ..compare import CompareConfig, StrategySpec, compare
    from ..strategies import available

    end = datetime.now(timezone.utc)
    start = end - timedelta(days=365 * max(int(lookback_years), 1))
    strategies = [StrategySpec(name=n) for n in available()]
    payload = compare([symbol], strategies, start, end, CompareConfig())

    rows = [r for r in (payload.get("rows") or [])
            if (r.get("symbol") or "").upper() == symbol.upper()]
    if not rows:
        errs = payload.get("errors") or []
        detail = next((e.get("error") for e in errs
                       if (e.get("symbol") or "").upper() == symbol.upper()), None)
        raise HTTPException(
            status_code=422,
            detail=f"no verdict for {symbol!r}"
                   + (f": {detail}" if detail else " (no price data?)"))

    # The comparator ranks rows; rank 1 is the best-fit strategy whose
    # market_state/horizon drives the symbol-level bucket. _attach_bucket_
    # and_rationale stamps the SAME bucket/conviction on every row for the
    # symbol, so any row carries the canonical verdict — pick the top-ranked.
    rows.sort(key=lambda r: r.get("rank", 1e9))
    best = rows[0]
    horizon = best.get("horizon")

    # Per-strategy attribution — the whole point per the user: "evaluating
    # different strategies for the symbol to decide buy/sell/hold". Each
    # strategy's own latest action + how it backtested + its horizon.
    attribution = [
        {
            "strategy":         r.get("strategy"),
            "label":            r.get("strategy_label") or r.get("strategy"),
            "action":           r.get("current_action"),     # BUY/SELL/HOLD on the latest bar
            "in_position":      r.get("in_position"),
            "horizon":          r.get("horizon"),
            "strategy_type":    r.get("strategy_type"),
            "excluded_for_fit": r.get("excluded_for_fit"),
            "sharpe":           (r.get("stats") or {}).get("sharpe"),
            "cagr_pct":         (r.get("stats") or {}).get("cagr_pct"),
            "total_return_pct": (r.get("stats") or {}).get("total_return_pct"),
        }
        for r in rows
    ]

    return {
        "symbol":             symbol.upper(),
        "fetched_at":         end.isoformat(),
        "bucket":             best.get("bucket"),            # BUY / WAIT / AVOID (canonical)
        "bucket_reason":      best.get("bucket_reason"),
        "conviction":         best.get("conviction"),
        "conviction_reason":  best.get("conviction_reason"),
        "horizon":            horizon,                       # swing / medium_term / long_term
        "horizon_label":      _HORIZON_LABEL.get(horizon, horizon),
        "long_count":         best.get("long_count"),
        "total":              best.get("total"),
        "earnings_suppressed": best.get("earnings_suppressed"),
        "news_context":       best.get("news_context"),
        "market_state":       best.get("market_state"),
        "strategies":         attribution,
        "_source":            f"live://canonical_verdict/{symbol.upper()}",
    }


def _api_base() -> str:
    return os.environ.get("TRADEPRO_API_URL", "http://localhost:5080").rstrip("/")


def _fetch_compare_row(symbol: str, universe: str) -> tuple[dict | None, str | None]:
    """Pull the best-Sharpe row for `symbol` from the .NET API's compare
    cache. Returns (row, error_message). Network failure is recorded
    as a warning, not raised — the card still renders fundamental-only.
    """
    try:
        import requests
        url = f"{_api_base()}/api/compare/latest"
        resp = requests.get(url, params={"universe": universe}, timeout=10.0)
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:  # noqa: BLE001
        return None, f"compare-row fetch failed: {e}"

    rows = (data.get("payload") or {}).get("rows") or []
    matches = [r for r in rows if (r.get("symbol") or "").upper() == symbol.upper()]
    if not matches:
        return None, f"symbol {symbol!r} not in universe {universe!r}"

    def _sharpe(r: dict) -> float:
        s = (r.get("stats") or {}).get("sharpe")
        try:
            return float(s) if s is not None else float("-inf")
        except (TypeError, ValueError):
            return float("-inf")

    matches.sort(key=_sharpe, reverse=True)
    return matches[0], None


def build_app() -> FastAPI:
    app = FastAPI(title="tradepro-analysis", version="0.1.0")

    # Allow the local dev frontend (vite default 5173) to call
    # directly without a CORS proxy. Production deployments route
    # through the .NET API so this is loose-by-design for dev only.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["GET", "OPTIONS"],
        allow_headers=["*"],
    )

    @app.get("/health")
    def health() -> dict:
        return {"status": "ok", "service": "tradepro-analysis"}

    @app.get("/symbol/{ticker}/verdict")
    def symbol_verdict(ticker: str, lookback_years: int = 5) -> dict:
        """CANONICAL multi-strategy verdict for one symbol, computed LIVE via
        the same ``compare()`` pipeline Decide uses — so Research and Decide
        can never contradict. Returns BUY/WAIT/AVOID + conviction + the
        swing/short/long timeline + per-strategy attribution. ~15-30s
        (5 backtests + market_state + news/sentiment per symbol)."""
        symbol = (ticker or "").strip().upper()
        if not symbol:
            raise HTTPException(status_code=400, detail="ticker is required")
        try:
            return _compute_canonical_verdict(symbol, lookback_years)
        except HTTPException:
            raise
        except Exception as e:  # noqa: BLE001
            raise HTTPException(status_code=500, detail=f"verdict failed: {e}")

    @app.get("/symbol/{ticker}/analysis")
    def symbol_analysis(
        ticker: str,
        universe: str | None = None,
        drawdown_pct: float | None = None,
        skip_long_term: bool = False,
    ) -> dict:
        symbol = (ticker or "").strip().upper()
        if not symbol:
            raise HTTPException(status_code=400, detail="ticker is required")

        compare_row: dict | None = None
        compare_warning: str | None = None
        if universe:
            compare_row, compare_warning = _fetch_compare_row(symbol, universe)

        try:
            card = build_symbol_analysis_card(
                symbol,
                compare_row=compare_row,
                drawdown_pct=drawdown_pct,
                skip_long_term=skip_long_term,
            )
        except Exception as e:  # noqa: BLE001
            raise HTTPException(status_code=500, detail=f"orchestrator failed: {e}")

        out = card.to_dict()
        out["_source"] = f"live://symbol_analysis/{symbol}"
        out["compare_row_source"] = (
            f"tradepro://compare/{universe}/best/{symbol}"
            if compare_row else None
        )
        if compare_warning:
            warnings = list(out.get("warnings") or [])
            warnings.append(compare_warning)
            out["warnings"] = warnings
        return out

    return app


# Module-level app so `uvicorn tradepro_strategies.cli.analysis_server:app`
# works without the factory.
app = build_app()


def main() -> None:
    import uvicorn
    port = int(os.environ.get("TRADEPRO_ANALYSIS_PORT", "8002"))
    host = os.environ.get("TRADEPRO_ANALYSIS_HOST", "0.0.0.0")
    uvicorn.run(app, host=host, port=port, log_level="info")


if __name__ == "__main__":
    main()

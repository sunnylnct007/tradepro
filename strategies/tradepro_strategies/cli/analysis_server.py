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

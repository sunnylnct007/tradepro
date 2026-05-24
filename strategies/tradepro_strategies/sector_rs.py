"""Relative strength of a symbol vs its sector ETF proxy.

Relative strength (RS) answers: "is this stock outperforming or
underperforming its own sector over the last 12 weeks?"

A stock that's +25% while its sector is +10% has RS = +15%. That's a
momentum standout — the market is choosing it over peers. A stock that's
+5% while its sector is +20% has RS = -15% — something is wrong with
this name specifically, not the sector.

RS is one of the cleanest, most robust alpha factors:
  - It cancels macro noise (broad market moves affect both).
  - It isolates idiosyncratic alpha.
  - It's computable purely from price — no fundamental data required.

Sector ETF map: a curated lookup covers the most-traded US names. Unknown
symbols fall back to SPY (broad market) which gives a valid RS signal,
just less precise than a sector ETF. The fallback is transparent — the
caller gets `sector_etf="SPY", fallback=True` in the result dict.

Returns
-------
dict with keys:
    symbol          — the input symbol (uppercased)
    sector_etf      — the comparison ETF used
    fallback        — True when no curated mapping was found
    symbol_12w_pct  — 12-week price return for the symbol
    etf_12w_pct     — 12-week price return for the sector ETF
    rs_12w_pct      — symbol_12w_pct - etf_12w_pct
    rs_score        — 0-10 factor score for COMPASS
    as_of           — ISO date of most recent bar used
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from .cache import ensure_cached

_log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Sector → ETF mapping
# ---------------------------------------------------------------------------

#: Maps Yahoo Finance sector labels (from Ticker.info["sector"]) to their
#: best liquid ETF proxy.  Keys are lowercase for case-insensitive lookup.
SECTOR_ETF: dict[str, str] = {
    "technology": "XLK",
    "semiconductors": "SOXX",
    "software": "IGV",
    "financial services": "XLF",
    "healthcare": "XLV",
    "energy": "XLE",
    "consumer cyclical": "XLY",
    "consumer defensive": "XLP",
    "industrials": "XLI",
    "basic materials": "XLB",
    "real estate": "XLRE",
    "utilities": "XLU",
    "communication services": "XLC",
    # UK / European broad fallback
    "uk": "EWU",
    "europe": "VGK",
    "asia": "VPL",
    # Broad default
    "broad_equity": "SPY",
}

#: Curated per-symbol override map — avoids an extra yfinance call for
#: the most commonly analysed names.  Symbols are uppercase.
SYMBOL_SECTOR_ETF: dict[str, str] = {
    # Semis
    "NVDA": "SOXX", "MU": "SOXX", "AMD": "SOXX", "INTC": "SOXX",
    "TSM": "SOXX", "ASML": "SOXX", "AVGO": "SOXX", "KLAC": "SOXX",
    "LRCX": "SOXX", "AMAT": "SOXX", "SOXX": "XLK",
    # Mega-cap tech
    "AAPL": "XLK", "MSFT": "XLK", "GOOG": "XLK", "GOOGL": "XLK",
    "META": "XLC", "AMZN": "XLY", "NFLX": "XLC",
    # AI / Cloud
    "PLTR": "XLK", "CRM": "XLK", "NOW": "XLK", "SNOW": "XLK",
    # Financials
    "JPM": "XLF", "BAC": "XLF", "GS": "XLF", "MS": "XLF", "V": "XLF",
    # Healthcare
    "JNJ": "XLV", "UNH": "XLV", "PFE": "XLV", "ABBV": "XLV",
    # Energy
    "XOM": "XLE", "CVX": "XLE", "COP": "XLE",
    # UK large-caps (London-listed)
    "HSBA.L": "EWU", "AZN.L": "EWU", "SHEL.L": "EWU", "RR.L": "EWU",
    "ULVR.L": "EWU", "BP.L": "EWU", "GSK.L": "EWU",
    # UK/global ETFs → benchmark against SPY (global proxy)
    "VUKE.L": "SPY", "VUSA.L": "SPY", "CSPX.L": "SPY",
    "VWRL.L": "SPY", "VWRP.L": "SPY", "SWLD.L": "SPY",
    # Crypto
    "BTC-USD": "BTC-USD",  # crypto is its own universe; RS vs self = 0
    "ETH-USD": "BTC-USD",  # ETH vs BTC is a meaningful cross
}

_LOOKBACK_WEEKS = 12
_LOOKBACK_DAYS = _LOOKBACK_WEEKS * 7 + 10   # buffer for weekends


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def get_sector_etf(symbol: str) -> tuple[str, bool]:
    """Return (sector_etf_ticker, is_fallback) for the given symbol.

    Priority order:
      1. Curated SYMBOL_SECTOR_ETF map (fastest, most accurate)
      2. yfinance Ticker.info["sector"] → SECTOR_ETF lookup
      3. "SPY" fallback (broad market)
    """
    sym = symbol.upper()
    if sym in SYMBOL_SECTOR_ETF:
        return SYMBOL_SECTOR_ETF[sym], False

    try:
        import yfinance as yf
        info = yf.Ticker(sym).info or {}
        sector_raw = (info.get("sector") or "").lower()
        if sector_raw in SECTOR_ETF:
            return SECTOR_ETF[sector_raw], False
        # Partial match — "healthcare" in "healthcare biotechnology"
        for key, etf in SECTOR_ETF.items():
            if key in sector_raw:
                return etf, False
    except Exception as exc:  # noqa: BLE001
        _log.debug("sector lookup via yfinance failed for %s: %s", sym, exc)

    return "SPY", True


def compute_sector_rs(symbol: str, *, provider: str = "yahoo") -> dict:
    """Compute 12-week relative strength of `symbol` vs its sector ETF.

    Fetches prices from the local Parquet cache (via ensure_cached).
    If either fetch fails, returns a neutral rs_score=5 with error info.
    """
    sym = symbol.upper()
    sector_etf, fallback = get_sector_etf(sym)

    # Same symbol as sector ETF (e.g. SPY vs SPY) → neutral
    if sym == sector_etf:
        return _neutral(sym, sector_etf, fallback, reason="symbol is its own sector ETF")

    end = datetime.now(timezone.utc)
    start = end - timedelta(days=_LOOKBACK_DAYS)

    sym_return = _price_return(sym, start, end, provider)
    etf_return = _price_return(sector_etf, start, end, provider)

    if sym_return is None or etf_return is None:
        return _neutral(
            sym, sector_etf, fallback,
            reason=f"price fetch failed (sym={sym_return}, etf={etf_return})",
        )

    rs = sym_return - etf_return
    score = _rs_to_score(rs)

    return {
        "symbol": sym,
        "sector_etf": sector_etf,
        "fallback": fallback,
        "symbol_12w_pct": round(sym_return, 3),
        "etf_12w_pct": round(etf_return, 3),
        "rs_12w_pct": round(rs, 3),
        "rs_score": score,
        "as_of": end.date().isoformat(),
        "error": None,
    }


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _price_return(symbol: str, start: datetime, end: datetime, provider: str) -> float | None:
    """12-week price return from cache. Returns None on any failure."""
    try:
        df = ensure_cached(provider, symbol, start, end)
    except Exception as exc:  # noqa: BLE001
        _log.debug("cache fetch failed for %s: %s", symbol, exc)
        return None

    if df is None or df.empty:
        return None

    col = "adj_close" if "adj_close" in df.columns else "close"
    series = df[col].dropna()
    if len(series) < 2:
        return None

    # Use bars approximately 12 weeks apart
    target_bars = _LOOKBACK_WEEKS * 5  # ~60 trading days
    idx_start = max(0, len(series) - target_bars - 1)
    price_start = float(series.iloc[idx_start])
    price_end = float(series.iloc[-1])

    if price_start <= 0:
        return None
    return (price_end / price_start - 1.0) * 100.0


def _rs_to_score(rs_pct: float) -> int:
    """Map relative strength (percentage points vs sector ETF) to 0-10 score."""
    if rs_pct >= 15:
        return 10
    if rs_pct >= 8:
        return 9
    if rs_pct >= 4:
        return 7
    if rs_pct >= 1:
        return 6
    if rs_pct >= -1:
        return 5   # inline with sector — neutral
    if rs_pct >= -4:
        return 4
    if rs_pct >= -8:
        return 3
    if rs_pct >= -15:
        return 2
    return 1


def _neutral(symbol: str, sector_etf: str, fallback: bool, reason: str = "") -> dict:
    return {
        "symbol": symbol,
        "sector_etf": sector_etf,
        "fallback": fallback,
        "symbol_12w_pct": None,
        "etf_12w_pct": None,
        "rs_12w_pct": None,
        "rs_score": 5,
        "as_of": datetime.now(timezone.utc).date().isoformat(),
        "error": reason or None,
    }


__all__ = ["get_sector_etf", "compute_sector_rs", "SYMBOL_SECTOR_ETF", "SECTOR_ETF"]

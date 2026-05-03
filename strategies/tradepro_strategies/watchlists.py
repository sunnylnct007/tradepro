"""Named watchlists. In Phase 2 these move to Firestore so they're editable
from the UI. For now, single source of truth between CLI + worker."""
from __future__ import annotations

WATCHLISTS: dict[str, list[str]] = {
    "uk": [
        "^FTSE", "^FTMC",
        "BARC.L", "LLOY.L", "HSBA.L", "SHEL.L",
        "AZN.L", "ULVR.L", "GSK.L", "BP.L",
    ],
    "uk_ftse100_sample": [
        "^FTSE",
        "BARC.L", "LLOY.L", "NWG.L", "HSBA.L", "STAN.L",
        "SHEL.L", "BP.L", "RIO.L", "GLEN.L", "AAL.L",
        "AZN.L", "GSK.L", "HLMA.L",
        "ULVR.L", "DGE.L", "RKT.L",
        "TSCO.L", "SBRY.L",
        "VOD.L", "BT-A.L",
    ],
    "us_megacap_sample": [
        "AAPL", "MSFT", "GOOGL", "AMZN", "META", "NVDA", "TSLA",
    ],
    # ---- ETF universes ---------------------------------------------------
    # LSE-listed UCITS ETFs — what a UK-resident investor can actually buy
    # in an ISA / SIPP. GBP-denominated except VUSA (USD); the comparator
    # should normalise before ranking across currencies.
    "etf_uk_core": [
        "VWRP.L",   # Vanguard FTSE All-World (acc)
        "VUSA.L",   # Vanguard S&P 500
        "VUKE.L",   # Vanguard FTSE 100
        "VMID.L",   # Vanguard FTSE 250
        "ISF.L",    # iShares Core FTSE 100
        "VEUR.L",   # Vanguard FTSE Developed Europe ex-UK
        "VJPN.L",   # Vanguard FTSE Japan
        "VFEM.L",   # Vanguard FTSE Emerging Markets
        "VAGP.L",   # Vanguard Global Aggregate Bond (GBP-hedged)
        "IGLN.L",   # iShares Physical Gold
    ],
    # US-listed core ETFs — broader/cheaper, but US tax + FX considerations
    # for a UK investor.
    "etf_us_core": [
        "VOO",      # Vanguard S&P 500
        "VTI",      # Vanguard Total US Stock Market
        "QQQ",      # Invesco Nasdaq 100
        "IWM",      # iShares Russell 2000 (small-cap)
        "EFA",      # iShares MSCI EAFE (developed ex-US)
        "EEM",      # iShares MSCI Emerging Markets
        "AGG",      # iShares Core US Aggregate Bond
        "TLT",      # iShares 20+ Year Treasury
        "GLD",      # SPDR Gold
    ],
    # US sector SPDRs — useful for "which sector is leading?" comparisons.
    "etf_us_sector": [
        "XLK",      # Technology
        "XLV",      # Health Care
        "XLF",      # Financials
        "XLE",      # Energy
        "XLY",      # Consumer Discretionary
        "XLP",      # Consumer Staples
        "XLI",      # Industrials
        "XLU",      # Utilities
        "XLB",      # Materials
        "XLRE",     # Real Estate
        "XLC",      # Communication Services
    ],
    # Single-factor ETFs (US-listed) — for testing factor-tilt strategies.
    "etf_factor": [
        "MTUM",     # Momentum
        "VLUE",     # Value
        "QUAL",     # Quality
        "USMV",     # Low Volatility
        "SIZE",     # Size (small-cap factor)
    ],
    # Macro / event-impact proxies — curated for DISPERSION, not for
    # ranking. The axis labels live in MACRO_PROXIES_BY_AXIS below so
    # callers (the get_returns tool, the analyse_event prompt, the
    # rationale layer) can cite each move with its axis instead of
    # treating the basket as an undifferentiated bag of tickers.
    "etf_macro_proxies": [
        "SPY", "QQQ", "EFA", "EEM",
        "TLT", "AGG", "GLD",
        "USO", "DBA",
        "XLE", "ITA", "XLU",
        "UUP", "FXY",
        "VIXY",
    ],
}


# Macro-basket axis labels. Keep this side-by-side with the watchlist
# so an edit there + here travels together. Axes are deliberately
# uncorrelated — that's what makes the basket useful for surfacing
# dispersion when the user asks 'what's the impact of <event>?'.
MACRO_PROXIES_BY_AXIS: dict[str, list[str]] = {
    "risk_on_equity":  ["SPY", "QQQ", "EFA", "EEM"],
    "risk_off_bonds":  ["TLT", "AGG"],
    "risk_off_metal":  ["GLD"],
    "commodity":       ["USO", "DBA"],
    "sector_event":    ["XLE", "ITA", "XLU"],
    "currency":        ["UUP", "FXY"],
    "volatility":      ["VIXY"],
}


def macro_axis_for(symbol: str) -> str | None:
    """Inverse of MACRO_PROXIES_BY_AXIS — returns the axis label for a
    macro-proxy symbol, or None if the symbol isn't part of the basket.
    Cheap O(N) but the basket is small."""
    sym = symbol.upper()
    for axis, members in MACRO_PROXIES_BY_AXIS.items():
        if sym in members:
            return axis
    return None


def _all_etfs() -> list[str]:
    """Union of every ETF universe — for one-shot 'compare everything' runs.
    Currencies mix (UK = GBP, US = USD), but ranking metrics like Sharpe,
    CAGR % and max-DD % are currency-neutral. Stamp duty differs by venue
    so the comparator should run with stamp_duty=0 over this universe and
    treat fees as a per-broker concern."""
    seen: list[str] = []
    deduped: list[str] = []
    for key in ("etf_uk_core", "etf_us_core", "etf_us_sector", "etf_factor",
                "etf_macro_proxies"):
        for s in WATCHLISTS[key]:
            if s not in seen:
                seen.append(s)
                deduped.append(s)
    return deduped


WATCHLISTS["etf_all"] = _all_etfs()


def resolve(name: str) -> list[str]:
    if name not in WATCHLISTS:
        raise ValueError(f"unknown watchlist '{name}'. Available: {list(WATCHLISTS)}")
    return WATCHLISTS[name]

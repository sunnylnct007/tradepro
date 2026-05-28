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
        # The household-name megacaps that drive most of the index moves.
        # Sized for "what's the magnificent N up to today" rather than
        # cross-section ranking — 12 instead of 7 picks up MU, AMD, AVGO
        # (semis), CRM (cloud), LLY (pharma) without crowding the screen.
        "AAPL", "MSFT", "GOOGL", "AMZN", "META", "NVDA", "TSLA",
        "AMD", "AVGO", "MU", "CRM", "LLY",
    ],
    # ---- US large-cap & sector deep-dives ------------------------------
    # Sample of the S&P 100 mega/large caps a UK-resident investor can buy
    # via Trading 212 — 40 names spanning the dominant sectors. Bigger
    # than us_megacap_sample (curated for headlines) and smaller than the
    # full S&P 100 (curated for breadth without the long tail).
    "us_sp100_sample": [
        # Tech / communication
        "AAPL", "MSFT", "GOOGL", "AMZN", "META", "NVDA", "TSLA", "AVGO",
        "ORCL", "ADBE", "CRM", "NFLX", "AMD", "INTC", "CSCO", "IBM",
        # Financials
        "BRK-B", "JPM", "BAC", "GS", "MS", "V", "MA", "AXP",
        # Healthcare
        "LLY", "JNJ", "UNH", "PFE", "ABBV", "MRK",
        # Consumer
        "WMT", "COST", "KO", "PEP", "MCD", "NKE", "DIS", "PG",
        # Industrials / energy
        "BA", "CAT", "XOM",
    ],
    # Semiconductor sector — the names a momentum or thematic tilt is
    # likely to ride together. Curated to include the foundry (TSM) and
    # design houses (NVDA, AMD, AVGO, MU), the equipment makers (ASML,
    # AMAT, LRCX, KLAC), plus the analog/auto chips (TXN, ADI).
    "us_semis": [
        "NVDA", "AMD", "AVGO", "MU", "INTC",
        "TSM", "ASML", "AMAT", "LRCX", "KLAC",
        "TXN", "ADI", "QCOM",
        "SOXX",   # iShares Semiconductor ETF — basket reference
    ],
    # Cloud / SaaS / AI growth names — the high-multiple growth tilt.
    # PLTR + SNOW + ANET cover the AI infrastructure narrative; CRM /
    # NOW / ORCL the established SaaS leaders; SHOP / MELI the e-commerce
    # plays; UBER / DASH the consumer-tech ones.
    "us_growth_tech": [
        "PLTR", "SNOW", "ANET", "NOW", "CRM", "ORCL",
        "SHOP", "MELI", "UBER", "DASH",
        "NET", "CRWD", "DDOG", "MDB",
    ],
    # ---- International equities ----------------------------------------
    # Asia / Pacific majors — Yahoo carries Tokyo (suffix .T) and Hong
    # Kong (suffix .HK) without extra config. ASX (Australia) uses .AX.
    # Sample sized for "what does Asia look like overnight?" rather than
    # cross-section ranking.
    "asia_majors": [
        "^N225",       # Nikkei 225 index
        "^HSI",        # Hang Seng index
        # Japan — global brands a UK retail account is likely to know
        "7203.T",      # Toyota
        "6758.T",      # Sony
        "9984.T",      # SoftBank
        "8306.T",      # Mitsubishi UFJ Financial
        "6861.T",      # Keyence
        "9983.T",      # Fast Retailing (Uniqlo)
        # Hong Kong — China megacap
        "0700.HK",     # Tencent
        "9988.HK",     # Alibaba HK
        "3690.HK",     # Meituan
        "1810.HK",     # Xiaomi
        # Australia — the ASX heavyweights
        "BHP.AX",      # BHP
        "CBA.AX",      # Commonwealth Bank
    ],
    # European equities (ex-UK) — Euronext (.PA, .AS, .BR), Frankfurt
    # (.DE), Madrid (.MC). UK names live in the existing uk_* lists.
    "europe_majors": [
        "^GDAXI",      # DAX index
        "^STOXX",      # Stoxx 600 index
        "MC.PA",       # LVMH
        "OR.PA",       # L'Oréal
        "AIR.PA",      # Airbus
        "SAN.PA",      # Sanofi
        "TTE.PA",      # TotalEnergies
        "ASML.AS",     # ASML (Amsterdam)
        "INGA.AS",     # ING Groep
        "SAP.DE",      # SAP
        "SIE.DE",      # Siemens
        "ALV.DE",      # Allianz
        "ITX.MC",      # Inditex (Zara)
        "NESN.SW",     # Nestlé (Swiss exchange)
        "ROG.SW",      # Roche
        "NOVN.SW",     # Novartis
    ],
    # ---- Crypto majors -------------------------------------------------
    # Via Yahoo's <SYMBOL>-USD pairs — same continuous-contract style as
    # commodities. BTC + ETH + SOL cover most of the market cap; the
    # rest are top-volume L1s a UK retail account could buy via a
    # crypto-enabled broker (T212 supports BTC, ETH, others).
    "crypto_majors": [
        "BTC-USD", "ETH-USD", "SOL-USD",
        "BNB-USD", "XRP-USD", "ADA-USD",
        "AVAX-USD", "DOT-USD", "LINK-USD",
        "MATIC-USD",
    ],
    # ---- Broader commodities -------------------------------------------
    # Extend energy_commodities with metals + agri so the comparator
    # can answer "how's commodities doing overall?" not just oil/gas.
    # All continuous-contract proxies via Yahoo (=F suffix).
    "commodities_broad": [
        "NG=F",        # Natural Gas
        "BZ=F",        # Brent Crude
        "CL=F",        # WTI Crude
        "GC=F",        # Gold
        "SI=F",        # Silver
        "HG=F",        # Copper
        "PL=F",        # Platinum
        "PA=F",        # Palladium
        "ZC=F",        # Corn
        "ZW=F",        # Wheat
        "ZS=F",        # Soybeans
        "KC=F",        # Coffee
    ],
    # ---- ETF universes ---------------------------------------------------
    # LSE-listed UCITS ETFs — what a UK-resident investor can actually buy
    # in an ISA / SIPP. GBP-denominated except VUSA (USD); the comparator
    # should normalise before ranking across currencies.
    "etf_uk_core": [
        "VWRP.L",   # Vanguard FTSE All-World (acc) — global, accumulating
        "VWRL.L",   # Vanguard FTSE All-World (dist) — same fund, dist class. Probably the most-held LSE ETF on Trading 212.
        "VUSA.L",   # Vanguard S&P 500 (USD-denominated, dist)
        "CSPX.L",   # iShares Core S&P 500 (USD-denominated, acc) — common alt to VUSA
        "SWDA.L",   # iShares Core MSCI World (USD, acc) — large-cap dev-world index
        "HMWO.L",   # HSBC MSCI World — cheaper rival to SWDA
        "SWLD.L",   # SPDR MSCI World — third major MSCI World option
        "VUKE.L",   # Vanguard FTSE 100
        "VMID.L",   # Vanguard FTSE 250
        "ISF.L",    # iShares Core FTSE 100
        "IUKD.L",   # iShares UK Dividend
        "IGLT.L",   # iShares Core UK Gilts
        "VEUR.L",   # Vanguard FTSE Developed Europe ex-UK
        "VJPN.L",   # Vanguard FTSE Japan
        "VFEM.L",   # Vanguard FTSE Emerging Markets
        "EIMI.L",   # iShares Core MSCI Emerging Markets IMI
        "VAGP.L",   # Vanguard Global Aggregate Bond (GBP-hedged)
        "IGLN.L",   # iShares Physical Gold
        "INRG.L",   # iShares Global Clean Energy — popular thematic
    ],
    # US-listed core ETFs — broader/cheaper, but US tax + FX considerations
    # for a UK investor.
    "etf_us_core": [
        "VOO",      # Vanguard S&P 500
        "IVV",      # iShares Core S&P 500 — major alternative to VOO
        "VTI",      # Vanguard Total US Stock Market
        "VXUS",     # Vanguard Total International Stock — ex-US broad
        "QQQ",      # Invesco Nasdaq 100
        "IWM",      # iShares Russell 2000 (small-cap)
        "SCHD",     # Schwab US Dividend Equity — popular high-dividend
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
    # Energy commodity futures, continuous-contract proxies via Yahoo.
    # TTF / NBP are intentionally excluded — Yahoo doesn't carry them
    # and Alpha Vantage's free tier doesn't either; needs a paid feed
    # (ICE Endex, EEX). Add when we wire that in. For now: Henry Hub
    # nat gas (NG=F), Brent (BZ=F), WTI (CL=F) cover the energy
    # complex's main movers with full daily history.
    "energy_commodities": [
        "NG=F",     # Natural Gas (Henry Hub) continuous
        "BZ=F",     # Brent Crude continuous
        "CL=F",     # WTI Crude continuous
    ],
}


# Per-watchlist metadata — provider override, default strategy
# parameter tweaks, etc. The CLI / comparator falls back to defaults
# (yahoo provider, standard ichimoku 9/26/52) for any watchlist that
# isn't listed here. Keeps `provider="yahoo"` as the global default
# so swapping a single universe to Alpha Vantage later is a one-line
# config change with no plumbing rewrite.
WATCHLIST_META: dict[str, dict] = {
    "energy_commodities": {
        # Yahoo today; flip to "alphavantage" (or another provider)
        # without touching the comparator once we wire that fetcher in.
        "provider": "yahoo",
        # Energy futures whip around faster than equities — keeping
        # defaults for now but the override mechanism is ready.
        "ichimoku_periods": {"tenkan": 9, "kijun": 26, "senkou_b": 52},
    },
}


def meta_for(name: str) -> dict:
    """Return the metadata dict for a watchlist, or {} when none is
    declared. Cheap empty-dict default so callers can do
    `meta_for(u).get("provider", cfg.provider)` without a None check."""
    return WATCHLIST_META.get(name, {})


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

"""Coarse, static sector classifier for the equity universe.

WHY a static dict (not yfinance.info["sector"]): the concentration cap in
IchimokuEquityStrategy.max_per_sector runs on every NEW entry, in the live
trading loop. A network/`info` lookup there would be slow, flaky, and
non-deterministic — exactly what the trader's "config-driven, no hardcoding…
but read it from a table we maintain + auditable" rule wants avoided. This
table IS that maintained, auditable config: one place, plain data, reviewable
in a diff, deterministic in tests.

Buckets are intentionally COARSE (a risk-concentration lens, not GICS):

    tech        — mega-cap platform/hardware (AAPL, MSFT, GOOGL, META…)
    semis       — semiconductors + equipment (NVDA, AMD, AVGO, MU, MPWR…)
    software    — application / cloud / SaaS / networking (CRM, NOW, SNOW, CIEN…)
    financials  — banks, payments, asset managers (JPM, V, GS, BLK…)
    healthcare  — pharma, biotech, managed care (JNJ, LLY, UNH…)
    energy      — oil & gas (XOM, CVX, COP…)
    consumer    — discretionary + staples + retail (AMZN, HD, KO, WMT…)
    industrials — capital goods, transport, defence (BA, CAT, GE, UPS…)
    other       — everything unmapped (the safe default; comms/telecom/utils/
                  materials/gold land here so an unknown name never silently
                  shares a cap bucket with a mapped one)

Coverage: every name in quant_engine.config large_50 + GLD, plus the
high-beta tech/semis/software names the daemon actually trades that the spec
called out (AVGO, NOW, SNOW, CIEN, MPWR, IBM, MU, INTC, TSM, ASML, KLAC,
LRCX, AMAT, PLTR, …). Unknown tickers fall through to "other".

Kept separate from sector_rs.SYMBOL_SECTOR_ETF (which maps to comparison ETFs,
a different concern) so this concentration lens is independently auditable and
reusable.
"""
from __future__ import annotations

#: ticker -> coarse sector bucket. Bare US tickers (no broker suffix); the
#: classifier strips a "_US_EQ"-style suffix before lookup so it works on the
#: daemon's broker-qualified symbols too.
SECTOR_MAP: dict[str, str] = {
    # ── tech: mega-cap platforms & hardware ──────────────────────────
    "AAPL": "tech",
    "MSFT": "tech",
    "GOOGL": "tech",
    "GOOG": "tech",
    "META": "tech",
    "IBM": "tech",
    "CSCO": "tech",  # networking hardware — grouped with tech infra
    "HPQ": "tech",
    "DELL": "tech",
    # ── semis: semiconductors + equipment ────────────────────────────
    "NVDA": "semis",
    "AMD": "semis",
    "AVGO": "semis",
    "MU": "semis",
    "INTC": "semis",
    "TSM": "semis",
    "ASML": "semis",
    "KLAC": "semis",
    "LRCX": "semis",
    "AMAT": "semis",
    "MPWR": "semis",
    "QCOM": "semis",
    "TXN": "semis",
    "ON": "semis",
    "ADI": "semis",
    # ── software: application / cloud / SaaS / networking ─────────────
    "CRM": "software",
    "ORCL": "software",
    "ADBE": "software",
    "NOW": "software",
    "SNOW": "software",
    "PLTR": "software",
    "CIEN": "software",  # optical networking — grouped with software/cloud infra
    "PANW": "software",
    "CRWD": "software",
    "DDOG": "software",
    "NET": "software",
    "MDB": "software",
    "ZS": "software",
    # ── financials: banks, payments, asset managers ──────────────────
    "JPM": "financials",
    "BAC": "financials",
    "WFC": "financials",
    "GS": "financials",
    "MS": "financials",
    "V": "financials",
    "MA": "financials",
    "AXP": "financials",
    "BLK": "financials",
    # ── healthcare: pharma, biotech, managed care ────────────────────
    "JNJ": "healthcare",
    "UNH": "healthcare",
    "PFE": "healthcare",
    "ABBV": "healthcare",
    "LLY": "healthcare",
    "MRK": "healthcare",
    # ── energy: oil & gas ────────────────────────────────────────────
    "XOM": "energy",
    "CVX": "energy",
    "COP": "energy",
    # ── consumer: discretionary + staples + retail ───────────────────
    "AMZN": "consumer",
    "TSLA": "consumer",
    "HD": "consumer",
    "NKE": "consumer",
    "MCD": "consumer",
    "SBUX": "consumer",
    "WMT": "consumer",
    "PG": "consumer",
    "KO": "consumer",
    "PEP": "consumer",
    "COST": "consumer",
    "DIS": "consumer",
    # ── industrials: capital goods, transport, defence ───────────────
    "BA": "industrials",
    "CAT": "industrials",
    "GE": "industrials",
    "HON": "industrials",
    "UPS": "industrials",
    # ── other: comms/telecom, utilities, materials, gold ─────────────
    # Mapped EXPLICITLY (not just left to the default) so the bucket is
    # documented and the coverage test can assert it.
    "NFLX": "other",   # streaming / comms-services
    "T": "other",      # telecom
    "VZ": "other",     # telecom
    "LIN": "other",    # materials (industrial gases)
    "NEE": "other",    # utilities
    "GLD": "other",    # gold ETF (the trader's gold sleeve)
}

#: The canonical default bucket for any ticker not in SECTOR_MAP.
DEFAULT_SECTOR = "other"


def sector_for(symbol: str) -> str:
    """Return the coarse sector bucket for `symbol`, or "other" if unmapped.

    Tolerates broker-qualified symbols (e.g. "AAPL_US_EQ", "AAPL.L") by
    stripping a trailing "_..."/".." suffix before lookup, so it works on
    both bare tickers and the daemon's broker-suffixed forms. Always returns
    a non-empty bucket — never raises, never returns None.
    """
    if not symbol:
        return DEFAULT_SECTOR
    bare = symbol.upper()
    # Strip a broker suffix: "AAPL_US_EQ" -> "AAPL", "AAPL.L" -> "AAPL".
    for sep in ("_", "."):
        if sep in bare:
            bare = bare.split(sep, 1)[0]
    return SECTOR_MAP.get(bare, DEFAULT_SECTOR)


__all__ = ["SECTOR_MAP", "DEFAULT_SECTOR", "sector_for"]

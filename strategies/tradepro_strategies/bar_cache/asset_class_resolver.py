"""Asset-class resolver — pure-function ticker → asset_class.

Backtest CLIs (paper / quant / equity-pipeline / slippage-sweep)
need to know which BarStore plugin to invoke for each symbol. The
choice should be invisible to the operator — they pass tickers
and get the right plugin per symbol, every time.

The resolver is rule-based on the Yahoo-style ticker (which the
strategies + universes already speak) plus a small hand-curated
ETF override list. No DB call, no network. Deterministic.

Rules (in order — first match wins)
-----------------------------------
1. ``=X`` suffix  → ``fx_spot``           (e.g. ``EURUSD=X``)
2. ``-USD`` /
   ``-USDT`` /
   ``-BTC`` /
   ``-USDC`` /
   ``-EUR`` suffix → ``crypto``           (e.g. ``BTC-USD``)
3. ``.L`` suffix  → ``uk_equity``         (e.g. ``BARC.L``)
4. Symbol in known-ETF set → ``us_etf``
5. Bare alphanumeric ticker → ``us_equity`` (the default)

When the input is empty / whitespace / not a string, we surface
``unknown`` rather than guess. The caller can decide whether
that's a hard error (preflight will refuse) or a warning-only
soft-fall.

The ETF list is curated — every commonly-traded US ETF the trader
actually uses (SPY/QQQ/IWM/GLD/XLF/...). It's intentionally NOT
exhaustive: the moment we add a new ETF to a strategy we add it
here too. False negatives (ETF mis-classed as us_equity) cost
only the wrong asset_class label — the bars themselves are
identical between us_etf and us_equity so the backtest result is
unaffected.
"""
from __future__ import annotations

from typing import Final

# Plugin names — must match the registered ``AssetClassPlugin.name``
# strings in ``bar_cache/asset_classes/``. Centralised here so a
# rename catches every call site via this module.
ASSET_CLASS_FX_SPOT: Final[str] = "fx_spot"
ASSET_CLASS_CRYPTO: Final[str] = "crypto"
ASSET_CLASS_UK_EQUITY: Final[str] = "uk_equity"
ASSET_CLASS_US_ETF: Final[str] = "us_etf"
ASSET_CLASS_US_EQUITY: Final[str] = "us_equity"
ASSET_CLASS_UNKNOWN: Final[str] = "unknown"


# Yahoo crypto pair quote suffixes. Generous list to cover the
# common pairs the trader's universe might surface — yfinance
# typically uses -USD for the front cross + -USDT / -USDC for the
# stablecoin variants.
_CRYPTO_SUFFIXES: Final[tuple[str, ...]] = (
    "-USD", "-USDT", "-USDC", "-EUR", "-GBP", "-BTC", "-ETH",
)


# US ETF override set. Strategy-tradable US ETFs that we either
# benchmark against or hold as a sleeve. The trader's quant spec
# (docs/main 4.py) covers most of this — keep in sync when the
# QuantEngineConfig.large_50 / cfg.gold_tickers / benchmark
# references change.
#
# Future improvement: read from `data_assumptions` or a dedicated
# `etf_universe` table so the .NET admin UI can edit without a
# redeploy. Hardcoded list ships first — DB lookup is opt-in
# once an operator demands editability.
_US_ETF_OVERRIDES: Final[frozenset[str]] = frozenset({
    # Broad-market + sector benchmarks
    "SPY", "QQQ", "IWM", "DIA", "VTI", "VOO", "RSP",
    # Volatility / yield
    "TLT", "IEF", "SHY", "HYG", "LQD", "AGG", "BND",
    # Sectors (XL-prefixed Select Sector SPDRs)
    "XLF", "XLE", "XLK", "XLY", "XLP", "XLI", "XLU", "XLV", "XLB", "XLRE", "XLC",
    # Commodities + precious metals
    "GLD", "GLDM", "SLV", "USO", "UNG", "PALL", "PPLT",
    # International + ex-US
    "EFA", "EEM", "VXUS", "VEA", "VWO", "IEFA", "IEMG", "ACWI",
    # Thematic / popular
    "ARKK", "ARKG", "ARKQ", "ICLN", "TAN", "SOXX", "SMH", "XBI",
    # Currency / FX ETFs (kept as us_etf — they're equity-style
    # listings of FX baskets, not direct FX)
    "UUP", "FXE", "FXY",
})


def resolve_asset_class(ticker: str | None) -> str:
    """Return the bar_cache asset_class plugin name for ``ticker``.

    Pure function — no I/O. Returns ``unknown`` rather than guess
    when the input is empty or non-string. Caller decides whether
    that's fatal."""
    if not isinstance(ticker, str):
        return ASSET_CLASS_UNKNOWN
    t = ticker.strip().upper()
    if not t:
        return ASSET_CLASS_UNKNOWN

    # 1. FX — Yahoo encodes pairs with the ``=X`` sentinel.
    if t.endswith("=X"):
        return ASSET_CLASS_FX_SPOT

    # 2. Crypto — quote-currency suffix. Check after FX because some
    # FX cross encodings (e.g. ``EURGBP=X``) include ``-`` in
    # extremely rare cases; the ``=X`` rule has already matched
    # those above.
    for suffix in _CRYPTO_SUFFIXES:
        if t.endswith(suffix):
            return ASSET_CLASS_CRYPTO

    # 3. LSE-listed UK equity. ``.L`` is the Yahoo standard suffix
    # for London Stock Exchange (e.g. ``BARC.L``, ``VOD.L``). Also
    # treat ``.AS`` / ``.PA`` / ``.DE`` /etc. as UK-equity FALLBACK
    # for now (their calendars differ but the schema is identical);
    # operator can override per-call until we ship a EU plugin.
    if t.endswith(".L"):
        return ASSET_CLASS_UK_EQUITY

    # 4. Known US ETF override.
    if t in _US_ETF_OVERRIDES:
        return ASSET_CLASS_US_ETF

    # 5. Default — assume US single-name equity.
    return ASSET_CLASS_US_EQUITY


def resolve_many(tickers: list[str]) -> dict[str, str]:
    """Resolve a list of tickers in one call. Returns a dict keyed
    by the ORIGINAL ticker (not upper-cased) so the caller can
    round-trip without losing case sensitivity.

    Used by the multi-symbol backtest CLIs (quant / equity-pipeline)
    to build the per-symbol asset_class map for the preflight
    sweep."""
    return {t: resolve_asset_class(t) for t in tickers}


__all__ = [
    "ASSET_CLASS_FX_SPOT",
    "ASSET_CLASS_CRYPTO",
    "ASSET_CLASS_UK_EQUITY",
    "ASSET_CLASS_US_ETF",
    "ASSET_CLASS_US_EQUITY",
    "ASSET_CLASS_UNKNOWN",
    "resolve_asset_class",
    "resolve_many",
]

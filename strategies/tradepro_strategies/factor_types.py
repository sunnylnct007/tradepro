"""Instrument factor-type classification + strategy-fit rules.

Single source of truth for the question "is this strategy structurally
appropriate for this instrument?". Used by the consensus engine in
compare.py to exclude or down-weight strategy votes that would
otherwise produce internally-valid-but-tactically-useless signals.

The canonical example: RSI mean-reversion on iShares MTUM (the
momentum-factor ETF) almost always says SELL because MTUM is *designed*
to hold assets with elevated RSI. The strategy and the instrument are
philosophically incompatible — see STRATEGIES.md "The instrument-
strategy fit problem".

This lives in Python (not the DB) for two reasons:
1. The compare engine runs on the Mac and needs synchronous lookup.
2. Classification rarely changes — ETFs don't switch factor families.
   When it does change, edit this file + redeploy the strategies
   package; no API/DB roundtrip needed.

Future Phase D2: when we add a UI for users to override classifications
per symbol, move the data into the symbol_metadata table; this file
becomes the seed.
"""
from __future__ import annotations

from typing import Literal


# Factor types we recognise. A symbol that doesn't appear in
# SYMBOL_FACTOR_TYPES below gets FactorType.UNCLASSIFIED, which is
# treated as "no exclusions apply" — i.e. every strategy votes.
FactorType = Literal[
    "momentum",       # MTUM-class: holds trending assets, elevated RSI is by design
    "value",          # VLUE: low PE / cheap fundamentals
    "quality",        # QUAL: high ROE + low debt
    "low_vol",        # USMV: tight std-dev band by construction
    "size",           # SIZE: small-cap tilt
    "growth",         # high-growth tech
    "broad_equity",   # SPY/VTI/VOO: market-weighted, no factor tilt
    "broad_sector",   # XLK/XLF: sector concentration but no factor tilt within
    "country",        # VEUR/VFEM/VJPN: country/region exposure
    "bond",           # AGG/TLT/IGLT/VAGP: fixed income
    "commodity",      # GLD/IGLN/CL=F: real assets
    "currency_pair",  # FX
    "crypto",         # BTC-USD etc — high vol, broken mean-reversion semantics
    "single_stock",   # individual equity (default for non-ETFs we don't classify)
    "unclassified",
]


# Strategy names mirror StrategySignals.cs + the @register_strategy
# decorators in paper/strategies/. Keep this list in sync — adding a
# strategy means picking which factor types it suits.
STRATEGIES = (
    "buy_and_hold",
    "sma_crossover",
    "rsi_mean_reversion",
    "macd_signal_cross",
    "donchian_breakout",
    "ichimoku_cloud",
    "bollinger_bounce",
)


# Strategies that should NOT vote on each factor type. An empty list
# means every strategy is appropriate. The semantics:
#
#   - Mean-reversion (rsi_mean_reversion, bollinger_bounce) is wrong
#     for **momentum**: elevated RSI / above upper band is the asset
#     doing what it's designed to do.
#   - Breakouts (donchian_breakout, ichimoku_cloud) struggle on
#     **low_vol** and **bond** instruments: there's nothing to break
#     out of. The signal fires rarely and tends to be a false start.
#   - Trend-following (sma_crossover, macd_signal_cross, donchian,
#     ichimoku) is wrong for **value** when applied alone: value
#     plays often look like "downtrends" until the catalyst lands.
#     We don't exclude here — value investors still want trend
#     confirmation — but the consensus weight should be lower.
#     (Future Phase 9: regime-weighted consensus handles this.)
#   - Buy-and-hold is structurally compatible with everything
#     (it's the null model).
INCOMPATIBLE_STRATEGIES: dict[str, tuple[str, ...]] = {
    "momentum":     ("rsi_mean_reversion", "bollinger_bounce"),
    "value":        (),     # trend strategies are slow but not *wrong* on value
    "quality":      (),     # quality ETFs are broadly diversified — no exclusions
    "low_vol":      ("donchian_breakout", "ichimoku_cloud"),
    "size":         (),     # small-cap can trend or revert; let consensus decide
    "growth":       (),     # broad fit
    "broad_equity": (),     # the bread-and-butter case; everything fits
    "broad_sector": (),     # XLK can trend or chop; let consensus decide
    "country":      (),     # broad
    "bond":         ("donchian_breakout", "rsi_mean_reversion"),
                            # bond ETFs are mean-reverting but the RSI-MR signal
                            # fires on a different timescale than yield moves
    "commodity":    (),     # broad fit; commodities can trend or chop
    "currency_pair":(),     # broad fit; FX often mean-reverts
    "crypto":       ("rsi_mean_reversion", "bollinger_bounce"),
                            # crypto vol breaks mean-reversion thresholds; the
                            # bands keep "oversold" reading without the actual
                            # reversion that mean-reversion strategies require
    "single_stock": (),     # depends on the stock; default to broad fit
    "unclassified": (),
}


# The factor classification per symbol. Sourced from the watchlist
# definitions in Watchlists.cs + the ETF prospectus / fund family.
#
# Heuristic for new entries:
#   - ETF with "Momentum" / "MSCI Momentum" in the name → momentum
#   - ETF with "Value" / "MSCI Value"                  → value
#   - ETF with "Min Vol" / "Low Volatility"            → low_vol
#   - ETF with "Quality" / "MSCI Quality"              → quality
#   - ETF tracking S&P 500 / Russell 3000 / FTSE 100   → broad_equity
#   - SPDR sector ETFs (XL*)                           → broad_sector
#   - Aggregate bond / treasury ETFs                   → bond
#   - Gold / commodity futures                         → commodity
#   - X-USD                                            → crypto
SYMBOL_FACTOR_TYPES: dict[str, str] = {
    # ── etf_factor ────────────────────────────────────────────────
    "MTUM": "momentum",
    "VLUE": "value",
    "QUAL": "quality",
    "USMV": "low_vol",
    "SIZE": "size",
    # ── etf_us_core ──────────────────────────────────────────────
    "VOO":  "broad_equity",
    "IVV":  "broad_equity",
    "VTI":  "broad_equity",
    "VXUS": "broad_equity",
    "QQQ":  "broad_equity",   # tech-heavy but market-cap weighted
    "IWM":  "broad_equity",
    "SCHD": "value",          # dividend tilt — closer to value than broad
    "EFA":  "country",
    "EEM":  "country",
    "AGG":  "bond",
    "TLT":  "bond",
    "GLD":  "commodity",
    # ── etf_us_sector ────────────────────────────────────────────
    "XLK":  "broad_sector",
    "XLV":  "broad_sector",
    "XLF":  "broad_sector",
    "XLE":  "broad_sector",
    "XLY":  "broad_sector",
    "XLP":  "broad_sector",
    "XLI":  "broad_sector",
    "XLU":  "low_vol",       # utilities behave low-vol; tag accordingly
    "XLB":  "broad_sector",
    "XLRE": "broad_sector",
    "XLC":  "broad_sector",
    # ── etf_uk_core ──────────────────────────────────────────────
    "VWRP.L": "broad_equity",
    "VWRL.L": "broad_equity",
    "VUSA.L": "broad_equity",
    "CSPX.L": "broad_equity",
    "SWDA.L": "broad_equity",
    "HMWO.L": "broad_equity",
    "SWLD.L": "broad_equity",
    "VUKE.L": "broad_equity",
    "VMID.L": "broad_equity",
    "ISF.L":  "broad_equity",
    "IUKD.L": "value",        # UK dividend tilt
    "IGLT.L": "bond",
    "VEUR.L": "country",
    "VJPN.L": "country",
    "VFEM.L": "country",
    "EIMI.L": "country",
    "VAGP.L": "bond",
    "IGLN.L": "commodity",
    "INRG.L": "broad_sector",
    # ── crypto_majors ────────────────────────────────────────────
    "BTC-USD":   "crypto",
    "ETH-USD":   "crypto",
    "SOL-USD":   "crypto",
    "BNB-USD":   "crypto",
    "XRP-USD":   "crypto",
    "ADA-USD":   "crypto",
    "AVAX-USD":  "crypto",
    "DOT-USD":   "crypto",
    "LINK-USD":  "crypto",
    "MATIC-USD": "crypto",
    # ── commodities_broad ────────────────────────────────────────
    "NG=F":   "commodity",
    "BZ=F":   "commodity",
    "CL=F":   "commodity",
    "GC=F":   "commodity",
    "SI=F":   "commodity",
    "HG=F":   "commodity",
    "PL=F":   "commodity",
    "PA=F":   "commodity",
    "ZC=F":   "commodity",
    "ZW=F":   "commodity",
    "ZS=F":   "commodity",
    "KC=F":   "commodity",
}


def factor_type_for(symbol: str) -> str:
    """Return the factor classification for ``symbol``. Falls back to
    ``"single_stock"`` for unrecognised symbols that look like equity
    tickers (no special suffix), else ``"unclassified"``."""
    if not symbol:
        return "unclassified"
    if symbol in SYMBOL_FACTOR_TYPES:
        return SYMBOL_FACTOR_TYPES[symbol]
    # Heuristic for the long tail of single-stock equities not in the
    # explicit table. Anything looking like a plain ticker is a single
    # stock; anything with a suffix we don't know is unclassified.
    if "=" in symbol or "-USD" in symbol:
        return "unclassified"
    return "single_stock"


def is_compatible(strategy: str, symbol: str) -> bool:
    """``False`` when this strategy should be excluded from the
    consensus for this symbol. Used by ``compare.py`` to filter
    incompatible votes before counting majority-long."""
    ft = factor_type_for(symbol)
    return strategy not in INCOMPATIBLE_STRATEGIES.get(ft, ())


def incompatible_strategies_for(symbol: str) -> tuple[str, ...]:
    """All strategies that should NOT vote on this symbol. Returned
    as a tuple so the compare engine can include it in the per-row
    payload for the UI's "X strategies excluded" banner."""
    return INCOMPATIBLE_STRATEGIES.get(factor_type_for(symbol), ())


__all__ = [
    "FactorType",
    "factor_type_for",
    "is_compatible",
    "incompatible_strategies_for",
    "STRATEGIES",
    "SYMBOL_FACTOR_TYPES",
    "INCOMPATIBLE_STRATEGIES",
]

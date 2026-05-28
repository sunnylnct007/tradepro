"""Per-symbol fee resolution.

UK Stamp Duty Reserve Tax (SDRT) is 0.5% on the buy notional for
LSE main-market shares only. UCITS ETFs are exempt; AIM-listed
shares are exempt; non-UK securities don't pay UK SDRT at all.
Applying a flat 0.5% across a mixed run silently penalises
high-turnover strategies on ETFs and produces the wrong Sharpe
ranking — exactly the bug a user shouldn't have to remember.

This module is the single source of truth: hand it a symbol, it
hands back the rate the backtest should use. Use stamp_duty_for_symbol
where you'd previously hand-coded the rate.
"""
from __future__ import annotations

from typing import Iterable

# Cached on first call. Kept module-level (not in WATCHLISTS) so
# fees.py never circular-imports.
_ETF_CACHE: set[str] | None = None


def _known_etf_tickers() -> set[str]:
    """Union of every etf_* watchlist plus the macro proxies. The
    naming convention 'etf_*' is the contract — adding a new ETF
    universe automatically registers its members here."""
    global _ETF_CACHE
    if _ETF_CACHE is not None:
        return _ETF_CACHE
    from .watchlists import WATCHLISTS  # local import — avoids cycle
    out: set[str] = set()
    for name, syms in WATCHLISTS.items():
        if name.startswith("etf_"):
            out.update(s.upper() for s in syms)
    _ETF_CACHE = out
    return out


def is_known_etf(symbol: str) -> bool:
    return symbol.upper().strip() in _known_etf_tickers()


def stamp_duty_for_symbol(symbol: str) -> float:
    """Return the SDRT rate that applies to a buy of this symbol.

    Rules:
      - Known UCITS ETF (any etf_* watchlist member) → 0.0
      - LSE main-market share (.L suffix, not in ETF set) → 0.005
      - Anything else (US, EU, crypto, AIM with non-.L suffix) → 0.0

    Caveat: AIM-listed shares trade with .L suffix too and are SDRT-
    exempt, so this conservatively applies 0.5% to all unknown .L
    tickers. If you're researching an AIM name, pass --stamp-duty 0
    explicitly to override.
    """
    sym = symbol.upper().strip()
    if is_known_etf(sym):
        return 0.0
    if sym.endswith(".L"):
        return 0.005
    return 0.0


def stamp_duty_summary(symbols: Iterable[str]) -> dict:
    """Group a basket by stamp-duty rate. Used at run start to print
    a clear banner ("Stamp duty: 10 ETFs at 0%, 3 shares at 0.5%")
    so the user can see what the engine is about to apply."""
    by_rate: dict[float, list[str]] = {}
    for s in symbols:
        rate = stamp_duty_for_symbol(s)
        by_rate.setdefault(rate, []).append(s.upper())
    return {
        "groups": [
            {"rate_pct": rate * 100.0, "count": len(syms), "symbols": sorted(syms)}
            for rate, syms in sorted(by_rate.items())
        ],
        "total": sum(len(s) for s in by_rate.values()),
    }

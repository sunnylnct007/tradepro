"""Corporate-action ticker renames — canonical (current) ticker resolution.

When a company changes its ticker (a corporate action — e.g. L Brands ``LB``
→ Bath & Body Works ``BBWI`` in 2021, or Facebook ``FB`` → Meta ``META`` in
2022) the broker often keeps reporting an EXISTING position under the OLD
instrument code long after the strategy universe, signal, and price data have
all moved to the NEW ticker.

If the position map is keyed by the old ticker while the signal evaluates the
new one, two live-trading bugs follow (both observed on the T212 control):

  1. The "already long?" guard is BLIND — the held position lives under ``LB``
     but the signal loop checks ``BBWI`` → reads flat → re-emits the entry
     every cycle. Only OMS idempotency (409 on the repeated approve) prevents
     duplicate fills, which is a safety net doing the guard's job — fragile.
  2. The old ticker has no CURRENT price data (``LB`` is delisted on the data
     provider) → the held name can never be priced → never evaluated for exit
     → a stuck orphan that can't be sold.

Canonicalising every symbol to its CURRENT ticker at the boundaries (broker
position parsing, universe union) collapses both identities into one so the
guard, pricing, and exit all agree.

The map is small and slow-moving (renames are rare). It is overridable via the
``TRADEPRO_TICKER_RENAMES`` env var (a JSON object of OLD→NEW, e.g.
``{"LB": "BBWI"}``) so ops can register a new corporate action without a code
change. Longer term this should be sourced from the broker_ticker_map /
a corporate-actions table (config-driven, no hardcoding).
"""
from __future__ import annotations

import json
import logging
import os

log = logging.getLogger("tradepro.ticker_renames")

# Built-in known corporate-action renames (OLD ticker -> CURRENT ticker).
# Keep upper-case, single-hop (point straight at the current ticker).
_BUILTIN_RENAMES: dict[str, str] = {
    "LB": "BBWI",   # L Brands -> Bath & Body Works (Aug 2021)
    "FB": "META",   # Facebook -> Meta Platforms (Jun 2022)
}


def _load_overrides() -> dict[str, str]:
    raw = os.environ.get("TRADEPRO_TICKER_RENAMES")
    if not raw:
        return {}
    try:
        obj = json.loads(raw)
        return {str(k).strip().upper(): str(v).strip().upper()
                for k, v in obj.items()}
    except Exception as exc:  # noqa: BLE001
        log.warning("ignoring malformed TRADEPRO_TICKER_RENAMES=%r: %s", raw, exc)
        return {}


def ticker_renames() -> dict[str, str]:
    """Effective OLD→CURRENT rename map (built-in, with env overrides on top)."""
    return {**_BUILTIN_RENAMES, **_load_overrides()}


def canonical_ticker(symbol: str) -> str:
    """Resolve a bare ``symbol`` to its CURRENT ticker.

    Unmapped symbols pass through unchanged. Single-hop only (the map points
    directly at the current ticker — no rename chains). Empty/whitespace input
    is returned as-is so callers don't have to guard it.
    """
    if not symbol:
        return symbol
    s = symbol.strip().upper()
    return ticker_renames().get(s, s)

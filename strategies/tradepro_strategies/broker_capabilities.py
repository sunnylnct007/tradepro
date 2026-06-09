"""Central, config-driven broker capability map.

One source of truth for "what order types / behaviours does each broker support",
so strategies don't hardcode broker quirks. Today it carries MOO (Market-On-Open)
support, which decides how a MOO-intent strategy (ichimoku_equity) places at the
open:

  • Broker supports a NATIVE MOO / opening-auction order (IBKR MOO/OPG, Alpaca
    OPG): the order can be submitted pre-market — it queues and fills in the
    opening auction. No market-hours gate needed (and a truer open fill).

  • Broker does NOT (T212 — plain market orders, RTH only): the strategy must
    HOLD emission until the venue opens and then place a market order ("gate +
    market-at-open" fallback), or the order is rejected pre-market and the daemon
    re-emits it every run (the cancel-loop).

NOTE: flipping a broker to supports_moo=True also requires its adapter to place
an actual MOO/OPG order type (the placement side); until that's wired, leave it
False so the gate fallback keeps it correct.
"""
from __future__ import annotations

# broker key (matches --broker) → supports native MOO/opening-auction order
_SUPPORTS_MOO: dict[str, bool] = {
    "ibkr": False,      # capable (MOO/OPG) — flip to True once the IBKR adapter places MOO
    "alpaca": False,    # capable (OPG) — same caveat
    "t212": False,      # market orders, RTH only — needs the gate fallback
    "ig": False,        # CFD/24h, no US opening auction
    "yfinance": False,  # sim
    "replay": False,    # sim
    "stub_live": False,
}


def supports_moo(broker: str | None) -> bool:
    """True if this broker has a native MOO/opening-auction order type, so a
    MOO-intent strategy may emit pre-market without the market-hours gate."""
    return _SUPPORTS_MOO.get((broker or "").strip().lower(), False)

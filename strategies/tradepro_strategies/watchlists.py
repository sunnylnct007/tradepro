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
}


def resolve(name: str) -> list[str]:
    if name not in WATCHLISTS:
        raise ValueError(f"unknown watchlist '{name}'. Available: {list(WATCHLISTS)}")
    return WATCHLISTS[name]

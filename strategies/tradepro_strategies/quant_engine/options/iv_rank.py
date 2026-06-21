"""IV-Rank fetch — the vega-edge gate's fuel (BRD §5.3, §9.2).

IV-Rank = where today's implied vol sits in its trailing 52-week range (0–100).
The wheel only sells premium when IV-Rank > 30 (premium rich vs the name's own
history). IBKR serves the 52w IV history via reqHistoricalData
whatToShow="OPTION_IMPLIED_VOLATILITY" on the underlying — this is HISTORICAL
data (available outside market hours), so it can be harvested any time.

NO FALSE POSITIVES: on any failure (no connection, no IV history, too few
points) this returns available=False → the risk engine BLOCKS (we never sell
premium on an IV-Rank we couldn't compute).

Verified live 2026-06-21 against KO/F/T/INTC/MO/XOM/JNJ/PFE — PFE correctly
came back below the 30 gate.
"""
from __future__ import annotations

import os
from dataclasses import dataclass

from .black_scholes import implied_vol_rank


@dataclass(frozen=True)
class IvRankResult:
    symbol: str
    available: bool          # False → risk engine treats as could-not-compute → BLOCK
    iv: float | None = None          # current implied vol (fraction, e.g. 0.19)
    iv_rank: float | None = None     # 0–100
    low_52w: float | None = None
    high_52w: float | None = None
    days: int = 0
    reason: str = ""


def iv_rank_from_history(symbol: str, iv_history: list[float]) -> IvRankResult:
    """Pure: compute IV-Rank from a daily IV series (newest last). Lets the
    harvest/cache path reuse the exact contract without a live connection."""
    hist = [float(v) for v in iv_history if v is not None and v > 0]
    if len(hist) < 2:
        return IvRankResult(symbol, available=False, days=len(hist),
                            reason="insufficient IV history (<2 points)")
    ivr = implied_vol_rank(hist[-1], hist)
    if ivr is None:
        return IvRankResult(symbol, available=False, days=len(hist),
                            reason="IV-Rank not computable")
    return IvRankResult(
        symbol, available=True, iv=hist[-1], iv_rank=ivr,
        low_52w=min(hist), high_52w=max(hist), days=len(hist),
        reason="ok")


def fetch_iv_rank(symbol: str, ib=None, lookback: str = "1 Y") -> IvRankResult:
    """Live: pull `lookback` of daily OPTION_IMPLIED_VOLATILITY for `symbol`
    via IBKR and compute IV-Rank. Pass an existing connected `ib` to batch many
    symbols on one connection (preferred — avoids per-symbol connects that
    contend with the gateway). Fails closed → available=False."""
    try:
        import ib_insync
    except Exception as e:  # noqa: BLE001
        return IvRankResult(symbol, available=False, reason=f"ib_insync unavailable: {e}")

    own = ib is None
    if own:
        host = os.environ.get("TRADEPRO_IBKR_HOST", "127.0.0.1")
        port = int(os.environ.get("TRADEPRO_IBKR_PORT", "7500"))
        cid = int(os.environ.get("TRADEPRO_IBKR_DATA_CLIENT_ID", "96"))
        ib = ib_insync.IB()
        try:
            ib.connect(host, port, clientId=cid, timeout=20)
        except Exception as e:  # noqa: BLE001 — connection contention → fail closed
            return IvRankResult(symbol, available=False, reason=f"IBKR connect failed: {e}")
    try:
        c = ib_insync.Stock(symbol, "SMART", "USD")
        ib.qualifyContracts(c)
        bars = ib.reqHistoricalData(
            c, endDateTime="", durationStr=lookback, barSizeSetting="1 day",
            whatToShow="OPTION_IMPLIED_VOLATILITY", useRTH=True, formatDate=1, timeout=30)
        return iv_rank_from_history(symbol, [b.close for b in bars])
    except Exception as e:  # noqa: BLE001
        return IvRankResult(symbol, available=False, reason=f"IV fetch failed: {e}")
    finally:
        if own:
            try:
                ib.disconnect()
            except Exception:  # noqa: BLE001
                pass

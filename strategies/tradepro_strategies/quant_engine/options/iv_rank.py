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
    iv_rank: float | None = None     # 0–100; None in bridge mode (window too shallow)
    low_52w: float | None = None
    high_52w: float | None = None
    days: int = 0
    reason: str = ""
    # OAuth-path extras (fetch_iv_rank_web): 30d historic vol + the IV/HV
    # ratio — the variance-risk-premium bridge gate the risk engine uses
    # while our own IV-history dataset is still accumulating toward a
    # trustworthy rank window. None when the snapshot didn't serve HV.
    hv30: float | None = None
    iv_hv_ratio: float | None = None


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


def _pct_to_frac(v) -> float | None:
    """IBKR snapshot vol fields arrive as display strings ('25.381%').
    Returns the fraction (0.25381) or None — never a fabricated 0."""
    if v is None:
        return None
    try:
        s = str(v).strip().rstrip("%")
        f = float(s)
    except (TypeError, ValueError):
        return None
    # Field served as a percent display string → scale. A bare fraction
    # (0 < v < 3 with no '%') is already what we want; vol >300% is garbage.
    frac = f / 100.0 if (isinstance(v, str) and "%" in v) or f > 3.0 else f
    return frac if 0.0 < frac <= 5.0 else None


def fetch_iv_rank_web(
    symbol: str,
    api_base: str | None = None,
    api_token: str | None = None,
    *,
    min_window_days: int = 60,
    warmup_retries: int = 3,
    _http=None,        # test hook: object with .get(url, headers=, timeout=) / .post(...)
    _sleep=None,       # test hook: replaces time.sleep between warm-up retries
) -> IvRankResult:
    """OAuth-only IV metrics (no local Gateway — owner architecture decision
    2026-08-09): current IV + HV30 from the backend's IBKR Web API quote
    endpoint (snapshot fields 7283/7631, warm-up retry — the first snapshot
    for a cold conid returns empty, verified live), today's IV upserted into
    the backend's options_iv_daily dataset, and IV-Rank computed from OUR
    accumulated series once the window is ≥ min_window_days.

    Until the window matures: available=True with iv_rank=None and
    iv_hv_ratio set — the risk engine's BRIDGE gate (IV vs realised vol,
    the variance risk premium) takes over, with the shallow window named in
    `reason`. NO FALSE POSITIVES: no IV from the snapshot → available=False.

    NOTE: snapshot field 7282 is AVERAGE VOLUME ('8.98M'), not IV rank —
    verified live 2026-08-09 against CVX/AAPL; do not "simplify" this back
    to a single snapshot call.
    """
    import time as _time

    sym = symbol.strip().upper()
    sleep = _sleep or _time.sleep
    try:
        if _http is None:
            import requests as _http_mod
            http = _http_mod
        else:
            http = _http
        if api_base is None:
            from ...cli.push_to_api import load_credentials
            api_base, api_token = load_credentials()
        base = (api_base or "").rstrip("/")
        headers = {"Authorization": f"Bearer {api_token}"} if api_token else {}

        # 1) Current IV + HV — warm-up retry (first snapshot is often empty,
        # and fields warm INDEPENDENTLY: IV can land an attempt before HV).
        # HV comes from field 7284 (verified live 2026-08-09: 7284='27.205%'
        # for CVX while 7631 never arrived on this gateway); 7631 kept as a
        # secondary in case IBKR serves it elsewhere.
        iv = hv30 = None
        for attempt in range(1, warmup_retries + 1):
            r = http.get(
                f"{base}/api/integrations/ibkr/quote?symbol={sym}&fields=31,7283,7284,7631",
                headers=headers, timeout=30)
            snap = (r.json() or {}).get("snapshot") or {}
            iv = _pct_to_frac(snap.get("7283")) or iv
            hv30 = _pct_to_frac(snap.get("7284")) or _pct_to_frac(snap.get("7631")) or hv30
            if iv is not None and hv30 is not None:
                break
            if attempt < warmup_retries:
                sleep(1.5)
        if iv is None:
            return IvRankResult(
                sym, available=False,
                reason="IBKR Web quote served no IV (field 7283) after "
                       f"{warmup_retries} warm-up attempts — cannot assess the vega edge")

        # 2) Upsert today's row into the dataset (best-effort — a failed write
        # must not kill the screen, but it IS logged: the dataset is how the
        # rank window matures).
        try:
            http.post(f"{base}/api/options/iv-daily",
                      json={"rows": [{"symbol": sym, "tradeDate": None,
                                      "iv": iv, "hv30": hv30,
                                      "source": "ibkr_web_7283"}]},
                      headers=headers, timeout=15)
        except Exception as e:  # noqa: BLE001
            import logging as _logging
            _logging.getLogger("tradepro.options.iv_rank").warning(
                "%s: iv-daily upsert failed (rank window won't grow today): %s", sym, e)

        # 3) Accumulated series → rank when the window is honest.
        days = 0
        try:
            hr = http.get(f"{base}/api/options/iv-daily/{sym}", headers=headers, timeout=15)
            series = [row.get("iv") for row in ((hr.json() or {}).get("series") or [])]
            series = [v for v in series if v is not None and v > 0]
            days = len(series)
        except Exception:  # noqa: BLE001
            series = []

        ratio = round(iv / hv30, 3) if (hv30 and hv30 > 0) else None
        if days >= min_window_days:
            ranked = iv_rank_from_history(sym, series)
            if ranked.available:
                return IvRankResult(
                    sym, available=True, iv=iv, iv_rank=ranked.iv_rank,
                    low_52w=ranked.low_52w, high_52w=ranked.high_52w, days=days,
                    hv30=hv30, iv_hv_ratio=ratio,
                    reason=f"rank over own {days}d dataset (52w at 252d)")
        return IvRankResult(
            sym, available=True, iv=iv, iv_rank=None, days=days,
            hv30=hv30, iv_hv_ratio=ratio,
            reason=f"IV-rank window {days}d < {min_window_days}d — IV/HV bridge mode"
                   + ("" if ratio is not None else " (HV30 dark too — no bridge either)"))
    except Exception as e:  # noqa: BLE001 — fail closed, visibly
        return IvRankResult(sym, available=False, reason=f"web IV fetch failed: {e}")


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

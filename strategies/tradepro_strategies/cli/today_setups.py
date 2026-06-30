"""tradepro-today-setups — rank the universe by ENTRY QUALITY for today.

The dashboard "Today's Setups" scanner. For each symbol in a universe it computes
the Ichimoku trend signal + range/risk (52w range position, distance-to-kijun in
ATR, %over-200SMA) and classifies the ENTRY QUALITY — fixing the original
dashboard's flaw of a flat binary BUY light with no risk/early-vs-late context:

  ⭐ CONSIDER  — LONG (above cloud + bullish TK) AND pulled back near the kijun
                 (good risk-entry, like ANET) and not at the 52w high.
  ⚠  EXTENDED  — LONG but stretched (98th-pctile / far above kijun) — valid trend,
                 but you're chasing; wait for a pullback (like KO).
  ·  excluded  — not LONG (below cloud / rolling over) — no entry (like VNET/JEF).

It never emits a confident BUY — it surfaces "LONG + here's the risk; you decide"
(systematic signal, discretionary entry). Earnings proximity is NOT known here
(catalyst gap) — flagged as such.

    uv run tradepro-today-setups --universe large_50 --json
    uv run tradepro-today-setups --universe large_50 --push
"""
from __future__ import annotations

import argparse
import datetime as _dt
import glob
import logging
import os

log = logging.getLogger("tradepro.today_setups")

# Cloud position + BUY/WAIT/AVOID verdict + garbage-guard all come from the
# canonical market_state() engine (so the scanner can't contradict
# get_market_state). The only thing computed locally is the kijun-distance
# entry-quality ranking (standard 26-period kijun, matching the chart).
_MIN_BARS = 200          # need 200 for the SMA200/range context


def _load_daily(cache_dir: str, sym: str):
    import pandas as pd

    files = sorted(glob.glob(f"{cache_dir}/{sym}/1d/*.parquet"))
    if not files:
        return None
    df = pd.concat([pd.read_parquet(f) for f in files])
    df.columns = [c.lower() for c in df.columns]
    if not isinstance(df.index, pd.DatetimeIndex):
        for c in ("date", "timestamp", "time", "datetime"):
            if c in df.columns:
                df = df.set_index(pd.to_datetime(df[c]))
                break
    df.index = pd.to_datetime(df.index)
    try:
        df.index = df.index.tz_localize(None)
    except (TypeError, AttributeError):
        pass
    return df[~df.index.duplicated(keep="last")].sort_index()


def _setup_for(df) -> dict | None:
    import pandas as pd
    from ..market_state import market_state  # the SAME engine get_market_state uses

    if df is None or "close" not in df.columns or len(df) < _MIN_BARS:
        return None
    # AUTHORITATIVE state from the canonical engine — so the scanner CANNOT
    # contradict get_market_state (cloud position + BUY/WAIT/AVOID verdict), and
    # it inherits the engine's isolated-spike garbage guard (the HON fix, free).
    ms = market_state("_", df)
    if ms.last_price is None:
        return None                                   # no usable data (garbage-only) — skip
    c = float(ms.last_price)
    cloud = ms.ichimoku_cloud_position                # ABOVE / INSIDE / BELOW / None (canonical)
    sig = (ms.entry_signal or "").upper()             # BUY / HOLD / WAIT / AVOID (canonical verdict)
    mom3 = ms.momentum_3m_pct

    # Local entry-quality geometry — the scanner's value-add ON TOP of the
    # canonical verdict. Standard 26-period kijun (matches the chart).
    hi, lo, cl = df["high"], df["low"], df["close"]
    kj = float((hi.tail(26).max() + lo.tail(26).min()) / 2)
    w = cl.tail(252)
    rng_pctile = float((c - w.min()) / (w.max() - w.min()) * 100) if w.max() > w.min() else 50.0
    tr = pd.concat([hi - lo, (hi - cl.shift()).abs(), (lo - cl.shift()).abs()], axis=1).max(axis=1)
    atr = float(tr.tail(14).mean())
    atr_pct = ms.atr_14_pct if getattr(ms, "atr_14_pct", None) is not None else (atr / c * 100 if c else None)
    dist_to_kijun_pct = (c / kj - 1) * 100 if kj else None
    dist_atr = (c - kj) / atr if atr > 0 else None

    # The hard veto the canonical engine LACKS: a pullback to kijun after a +150%
    # run is NOT the same risk as after +15%. Top-of-range AND/OR parabolic = chase.
    extreme = (rng_pctile >= 90
               or (rng_pctile >= 80 and mom3 is not None and mom3 > 60)
               or (mom3 is not None and mom3 > 100)
               or (dist_atr is not None and dist_atr > 3))

    if cloud != "ABOVE":
        cls = "excluded"        # not in an uptrend per the canonical cloud (PG-inside / GSL-below)
    elif sig in ("WAIT", "AVOID"):
        cls = "excluded"        # the engine ITSELF vetoes it (JPM = WAIT at the highs)
    elif extreme:
        cls = "extended"        # chasing — top of range / parabolic / far above kijun
    elif dist_atr is None or dist_atr < 0:
        cls = "weak"            # above cloud but below kijun — support breaking
    elif dist_atr <= 2:
        cls = "consider"        # uptrend + engine-OK + not extended + at/above kijun support
    else:
        cls = "hold"            # uptrend but mid-zone (2-3 ATR above kijun)
    return {
        "close": round(c, 2), "classification": cls,
        "cloud": cloud, "signal": sig,
        "momentum_3m_pct": round(mom3, 0) if mom3 is not None else None,
        "range_pctile": round(rng_pctile, 0), "pct_off_high": round((c / w.max() - 1) * 100, 1),
        "atr_pct": round(atr_pct, 1) if atr_pct else None,
        "kijun": round(kj, 2),
        "dist_to_kijun_pct": round(dist_to_kijun_pct, 1) if dist_to_kijun_pct is not None else None,
        "dist_atr": round(dist_atr, 1) if dist_atr is not None else None,
        "stop8": round(c * 0.92, 2),
    }


def _why(s: dict) -> str:
    cls = s["classification"]
    if cls == "consider":
        return (f"engine: {s['signal']}, above cloud, pulled back to kijun ${s['kijun']} "
                f"({s['dist_atr']} ATR) — entry; {s['range_pctile']:.0f}th pctile, 3m {s['momentum_3m_pct']}%, "
                f"ATR {s['atr_pct']}%/day (size for it), stop below kijun.")
    if cls == "extended":
        return (f"above cloud but EXTENDED ({s['range_pctile']:.0f}th pctile, 3m {s['momentum_3m_pct']}%) — "
                f"chasing a big run; wait for a deeper pullback toward kijun ${s['kijun']}.")
    if cls == "hold":
        return f"above cloud, mid-zone ({s['dist_atr']} ATR above kijun) — no edge entering here."
    if cls == "weak":
        return f"above cloud but BELOW kijun ({s['dist_atr']} ATR) — support breaking, not an entry."
    return f"engine: {s.get('signal','-')} / cloud {s.get('cloud','-')} — no long entry."


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    p = argparse.ArgumentParser(prog="tradepro-today-setups")
    p.add_argument("--universe", default="large_50", help="universe name (from the API)")
    p.add_argument("--api-base", default=None)
    p.add_argument("--cache-dir", default=os.path.expanduser("~/.tradepro/bar_cache/us_etf"))
    p.add_argument("--top", type=int, default=12, help="max setups to keep (after excluded dropped)")
    p.add_argument("--json", action="store_true")
    p.add_argument("--push", action="store_true")
    args = p.parse_args()

    import requests

    from . import push_to_api as _pta
    base, token = args.api_base, None
    if not base:
        base, token = _pta.load_credentials()
    base = base.rstrip("/")
    headers = {"Authorization": f"Bearer {token}"} if token else {}

    syms = [s["ticker"] for s in requests.get(f"{base}/api/universes/{args.universe}",
            headers=headers, timeout=20).json().get("symbols", []) if s.get("effective", True)]

    rows, missing = [], []
    for sym in syms:
        s = _setup_for(_load_daily(args.cache_dir, sym))
        if s is None:
            missing.append(sym); continue
        s["symbol"] = sym
        s["why"] = _why(s)
        rows.append(s)

    # Rank only the actionable (consider → extended → hold), consider closest to
    # kijun first. weak/suspect/excluded are COUNTED but never shown/starred.
    order = {"consider": 0, "extended": 1, "hold": 2}
    actionable = [r for r in rows if r["classification"] in order]
    actionable.sort(key=lambda r: (order[r["classification"]], r.get("dist_atr") if r.get("dist_atr") is not None else 99))
    for i, r in enumerate(actionable):
        r["rank"] = i + 1

    def n(cls): return sum(r["classification"] == cls for r in rows)
    artifact = {
        "kind": "today_setups", "universe": args.universe,
        "as_of_utc": _dt.datetime.now(_dt.UTC).isoformat(),
        "counts": {"consider": n("consider"), "extended": n("extended"), "hold": n("hold"),
                   "weak": n("weak"), "suspect": n("suspect"), "excluded": n("excluded"), "scanned": len(rows)},
        "setups": actionable[: args.top],
        "excluded_symbols": [r["symbol"] for r in rows if r["classification"] == "excluded"],
        "data_suspect": [r["symbol"] for r in rows if r["classification"] == "suspect"],
        "missing": missing,
        "note": ("Ichimoku 9/26/52 (matches the chart). consider = LONG + at/above kijun support; "
                 "weak/suspect/excluded not shown. Earnings not checked (catalyst gap). Discretionary entry."),
    }

    for r in artifact["setups"]:
        log.info("%-2s %-9s %-8s %6.2f  %s", {"consider": "⭐", "extended": "⚠", "hold": "·"}.get(r["classification"], " "),
                 r["symbol"], r["classification"], r["close"], r["why"])
    log.info("counts: %s | suspect: %s | missing: %d", artifact["counts"], artifact["data_suspect"], len(missing))

    if args.json:
        import json
        print(json.dumps(artifact, indent=2))
    if args.push:
        if not token:
            _, token = _pta.load_credentials()
        _pta.push("today-setups", {"universe": args.universe, "label": "latest",
                  "uploaded_by": os.uname().nodename, "artifact": artifact}, base, token)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

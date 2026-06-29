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

# Ichimoku periods — match the clone (project_focus_one_strategy_ichimoku).
_T, _K, _B, _D = 5, 32, 50, 32
_MIN_BARS = 200  # need 200 for the SMA200/range context


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

    if df is None or "close" not in df.columns or len(df) < _MIN_BARS:
        return None
    hi, lo, cl = df["high"], df["low"], df["close"]
    c = float(cl.iloc[-1])
    tenkan = (hi.rolling(_T).max() + lo.rolling(_T).min()) / 2
    kijun = (hi.rolling(_K).max() + lo.rolling(_K).min()) / 2
    sa = ((tenkan + kijun) / 2).shift(_D)
    sb = ((hi.rolling(_B).max() + lo.rolling(_B).min()) / 2).shift(_D)
    cloud_top = pd.concat([sa, sb], axis=1).max(axis=1).iloc[-1]
    kj = float(kijun.iloc[-1]); tk = float(tenkan.iloc[-1])
    long_ = bool(c > cloud_top and tk > kj)
    sma200 = float(cl.tail(200).mean())
    w = cl.tail(252)
    rng_pctile = float((c - w.min()) / (w.max() - w.min()) * 100) if w.max() > w.min() else 50.0
    tr = pd.concat([hi - lo, (hi - cl.shift()).abs(), (lo - cl.shift()).abs()], axis=1).max(axis=1)
    atr = float(tr.tail(14).mean())
    atr_pct = atr / c * 100 if c else None
    dist_to_kijun_pct = (c / kj - 1) * 100 if kj else None
    dist_atr = (c - kj) / atr if atr > 0 else None  # how many ATR above kijun support

    # Classify entry quality.
    if not long_:
        cls = "excluded"
    elif rng_pctile >= 90 or (dist_atr is not None and dist_atr > 3):
        cls = "extended"
    elif dist_atr is not None and dist_atr <= 2 and rng_pctile < 90:
        cls = "consider"
    else:
        cls = "hold"  # LONG but middling — neither a great entry nor a chase
    return {
        "close": round(c, 2), "long": long_, "classification": cls,
        "range_pctile": round(rng_pctile, 0), "pct_off_high": round((c / w.max() - 1) * 100, 1),
        "pct_over_200sma": round((c / sma200 - 1) * 100, 1),
        "atr_pct": round(atr_pct, 1) if atr_pct else None,
        "kijun": round(kj, 2), "dist_to_kijun_pct": round(dist_to_kijun_pct, 1) if dist_to_kijun_pct is not None else None,
        "dist_atr": round(dist_atr, 1) if dist_atr is not None else None,
        "stop8": round(c * 0.92, 2),
    }


def _why(s: dict) -> str:
    if s["classification"] == "consider":
        return (f"LONG, pulled back near kijun ${s['kijun']} ({s['dist_atr']} ATR above) — good risk-entry; "
                f"{s['range_pctile']:.0f}th pctile, ATR {s['atr_pct']}%/day, 8% stop ${s['stop8']}.")
    if s["classification"] == "extended":
        return (f"LONG but extended ({s['range_pctile']:.0f}th pctile, {s['dist_atr']} ATR above kijun) — "
                f"valid trend, you're chasing; wait for a pullback toward ${s['kijun']}.")
    if s["classification"] == "hold":
        return f"LONG, mid-zone ({s['range_pctile']:.0f}th pctile, {s['dist_atr']} ATR above kijun) — no edge in entering here."
    return "below cloud / rolling over — no long signal."


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

    # Rank: consider (closest to kijun first) → extended → hold; excluded dropped.
    order = {"consider": 0, "extended": 1, "hold": 2}
    actionable = [r for r in rows if r["classification"] in order]
    actionable.sort(key=lambda r: (order[r["classification"]], r.get("dist_atr") if r.get("dist_atr") is not None else 99))
    for i, r in enumerate(actionable):
        r["rank"] = i + 1
    excluded = [r["symbol"] for r in rows if r["classification"] == "excluded"]

    artifact = {
        "kind": "today_setups", "universe": args.universe,
        "as_of_utc": _dt.datetime.now(_dt.UTC).isoformat(),
        "counts": {"consider": sum(r["classification"] == "consider" for r in rows),
                   "extended": sum(r["classification"] == "extended" for r in rows),
                   "excluded": len(excluded), "scanned": len(rows)},
        "setups": actionable[: args.top],
        "excluded_symbols": excluded,
        "missing": missing,
        "note": "earnings proximity not checked (catalyst gap); systematic signal — discretionary entry.",
    }

    for r in artifact["setups"]:
        log.info("%-2s %-9s %-8s %6.2f  %s", {"consider": "⭐", "extended": "⚠", "hold": "·"}.get(r["classification"], " "),
                 r["symbol"], r["classification"], r["close"], r["why"])
    log.info("counts: %s | missing: %d", artifact["counts"], len(missing))

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

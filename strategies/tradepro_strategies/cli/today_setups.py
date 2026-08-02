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

from ..formatting import ordinal_suffix

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
    vol_ratio = ms.volume_ratio_20d                   # 20d vol ratio — <0.8 = thin move, low conviction
    boll = ms.bollinger_position                      # AT_UPPER (%B≥1) = overextended above the band

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
    # Falling-knife guard input: how far below the recent 10-day high we sit. A
    # "pullback to kijun" that arrived via a sharp multi-day REVERSAL (ENTG −18%,
    # CAT −10% in 2 days) is a knife slicing DOWN through support, NOT a healthy
    # consolidation onto it (ANET −6%). This is a RISK guard (don't call a crash a
    # dip), not an alpha filter — it maps to the boundary a human reviewer drew.
    recent_high = float(cl.tail(10).max())
    off_10d_high_pct = (c / recent_high - 1) * 100 if recent_high else 0.0
    mom10 = ms.momentum_10d_pct

    # The hard veto the canonical engine LACKS: a pullback to kijun after a +150%
    # run is NOT the same risk as after +15%. Top-of-range AND/OR parabolic = chase.
    extreme = (rng_pctile >= 90
               or (rng_pctile >= 80 and mom3 is not None and mom3 > 60)
               or (mom3 is not None and mom3 > 100)
               or (dist_atr is not None and dist_atr > 3)
               or boll == "AT_UPPER")                 # %B≥1: above the upper Bollinger band — overextended (V case)
    # Thin volume: <0.8x 20d is a CONVICTION reducer (flag, keep the ⭐ — ZBRA/UPS).
    # But EXTREME thinness (<0.3x, e.g. BLK's 0.02x) is no participation at all —
    # levels fail on light selling and fills are bad both ways, so it must NOT wear
    # a clean ⭐. That's a de-star, not just a flag (no false confidence).
    thin_vol = vol_ratio is not None and vol_ratio < 0.8
    very_thin = vol_ratio is not None and vol_ratio < 0.3

    # 200-day SMA — the PRIMARY-TREND filter the cloud alone doesn't enforce. A name
    # can sit ABOVE the Ichimoku cloud yet BELOW its 200d SMA (RH/TSLA both did):
    # that's a recovering dip, not a reclaimed uptrend. Don't star those. None when
    # we lack 200 bars — then fail-open (don't fabricate a gate on thin history).
    sma200 = float(cl.rolling(200).mean().iloc[-1]) if len(cl) >= 200 else None
    above_200sma = (sma200 is None) or (c >= sma200)
    pct_vs_200sma = ((c / sma200 - 1) * 100) if sma200 else None

    if cloud != "ABOVE":
        cls = "excluded"        # not in an uptrend per the canonical cloud (PG-inside / GSL-below)
    elif sig in ("WAIT", "AVOID"):
        cls = "excluded"        # the engine ITSELF vetoes it (JPM = WAIT at the highs)
    elif sig == "HOLD":
        cls = "extended"        # engine DEMOTED BUY→HOLD (≥88th pctile, near highs) — it
                                # won't buy here, so the scanner must not star it either
                                # (coherence with market_state; the CAT/ENTG-at-highs case)
    elif extreme:
        cls = "extended"        # chasing — top of range / parabolic / far above kijun
    elif off_10d_high_pct < -8.0:
        cls = "reversal"        # sharp drop THROUGH the kijun — falling knife, not support
    elif not above_200sma:
        cls = "below_trend"     # above cloud but BELOW the 200d SMA — primary trend not
                                # reclaimed (RH/TSLA): a recovering dip, never a clean ⭐
    elif dist_atr is None or dist_atr < 0:
        cls = "weak"            # above cloud but below kijun — support breaking
    elif dist_atr <= 1.0 and not very_thin:
        cls = "consider"        # GENUINELY at the kijun (≤1 ATR) + real participation.
                                # A pullback entry means price is ON the base line, not
                                # 1.2 ATR above it (BLK) — that's chasing the bounce, not
                                # buying the dip. Extreme-thin is de-starred here too.
    else:
        cls = "hold"            # above kijun but not a pullback entry (1-3 ATR), OR at the
                                # kijun but too thin to trust — constructive, no clean edge
    return {
        "close": round(c, 2), "classification": cls,
        "cloud": cloud, "signal": sig,
        "momentum_3m_pct": round(mom3, 0) if mom3 is not None else None,
        "range_pctile": round(rng_pctile, 0), "pct_off_high": round((c / w.max() - 1) * 100, 1),
        "atr_pct": round(atr_pct, 1) if atr_pct else None,
        "kijun": round(kj, 2),
        "dist_to_kijun_pct": round(dist_to_kijun_pct, 1) if dist_to_kijun_pct is not None else None,
        "dist_atr": round(dist_atr, 1) if dist_atr is not None else None,
        "off_10d_high_pct": round(off_10d_high_pct, 1),
        "momentum_10d_pct": round(mom10, 0) if mom10 is not None else None,
        "sma200": round(sma200, 2) if sma200 is not None else None,
        "above_200sma": bool(above_200sma),
        "pct_vs_200sma": round(pct_vs_200sma, 1) if pct_vs_200sma is not None else None,
        "as_of": ms.as_of,
        "stop8": round(c * 0.92, 2),
        "volume_ratio": round(vol_ratio, 2) if vol_ratio is not None else None,
        "thin_volume": bool(thin_vol),
        "very_thin": bool(very_thin),
        "bollinger": boll,
    }


def _why(s: dict) -> str:
    cls = s["classification"]
    if cls == "consider":
        vol = f"{s['volume_ratio']}x vol" + (" ⚠ THIN (low conviction)" if s.get("thin_volume") else "")
        # dist_atr ≤ 1.0 here (band tightened), so "at the kijun" is now TRUE, not a
        # stale-pullback narrative stitched onto a price that already ran.
        return (f"engine: {s['signal']}, above cloud, at the kijun ${s['kijun']} "
                f"({s['dist_atr']} ATR above, {s['off_10d_high_pct']}% off 10d high — support hold, not a knife); "
                f"{s['range_pctile']:.0f}{ordinal_suffix(s['range_pctile'])} pctile, 3m {s['momentum_3m_pct']}% / 10d {s['momentum_10d_pct']}%, "
                f"ATR {s['atr_pct']}%/day, {vol}; stop below kijun.")
    if cls == "earnings":
        return (f"clean pullback-to-kijun setup BUT reports earnings in {s.get('earnings_days')}d "
                f"({s.get('earnings_date')}) — a binary print can gap it through the ${s['kijun']} stop. "
                f"WATCH, don't enter before the report.")
    if cls == "reversal":
        return (f"at kijun ${s['kijun']} but via a SHARP DROP ({s['off_10d_high_pct']}% off the 10d high, "
                f"10d {s['momentum_10d_pct']}%) — a falling knife slicing through support, NOT an entry. "
                f"Wait for it to base.")
    if cls == "extended":
        return (f"above cloud but EXTENDED ({s['range_pctile']:.0f}{ordinal_suffix(s['range_pctile'])} pctile, 3m {s['momentum_3m_pct']}%) — "
                f"chasing a big run; wait for a deeper pullback toward kijun ${s['kijun']}.")
    if cls == "below_trend":
        return (f"above the cloud BUT below the 200-day SMA ${s.get('sma200')} "
                f"({s.get('pct_vs_200sma')}%) — primary trend not reclaimed, a recovering dip not a "
                f"confirmed uptrend. WATCH for a 200-day reclaim before entry.")
    if cls == "hold":
        if s.get("very_thin"):
            return (f"above cloud, near the kijun BUT only {s['volume_ratio']}x volume — too thin to "
                    f"trust (levels fail on light selling, bad fills both ways); no clean entry.")
        return (f"above cloud but {s['dist_atr']} ATR above the kijun — not a pullback entry "
                f"(you'd be buying the bounce, not the base); no edge here. Wait for a dip to kijun ${s['kijun']}.")
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

    # A 'consider' name that reports earnings inside the swing hold is NOT a clean
    # swing buy — a binary print can gap it through the stop. Downgrade those to
    # 'earnings' (shown, not hidden, so the trader sees WHY it's a skip). Only the
    # few 'consider' names are checked (limits the per-symbol earnings fetch).
    from ..earnings import fetch_upcoming_earnings, earnings_calendar_enabled
    # A swing hold routinely runs 4–6 WEEKS, so any earnings print within ~5 weeks
    # can gap the position through its stop mid-hold. 25d was too tight — it let
    # ZBRA (26d) and HIMS (25d) slip through as clean ⭐ when both report inside a
    # normal hold. The scan is also a daily EOD snapshot, so a name 26d out at scan
    # time is <25d by the time you act — another reason to gate wider than the hold.
    EARNINGS_GATE_DAYS = 35  # ≈5 weeks / ~25 trading days — covers a full swing hold
    # Probe the source ONCE: with Finnhub live, a per-symbol "no earnings" result
    # means earnings-CLEAR (leave the ⭐), NOT unverified. Only flag unverified when
    # the source itself is down — else a name whose next print is just beyond the
    # window (e.g. RH, ~61d out) gets falsely tainted.
    earnings_live = earnings_calendar_enabled(base)

    rows, missing = [], []
    for sym in syms:
        s = _setup_for(_load_daily(args.cache_dir, sym))
        if s is None:
            missing.append(sym); continue
        s["symbol"] = sym
        if s["classification"] == "consider":
            try:
                ev = fetch_upcoming_earnings(sym, base, days=EARNINGS_GATE_DAYS + 10) or {}
                du = ev.get("days_until")
                if du is not None and du <= EARNINGS_GATE_DAYS:
                    s["classification"] = "earnings"
                    s["earnings_days"] = du
                    s["earnings_date"] = ev.get("date")
                elif du is None and not earnings_live:
                    # Source down/unreachable — we genuinely can't confirm it's
                    # clear, so FLAG it (fail-loud) rather than presenting an
                    # unverified ⭐ as earnings-safe.
                    s["earnings_unverified"] = True
                # else: du is None AND Finnhub live → verified CLEAR (no print in
                # the window) → leave the clean ⭐, no false 'unverified' taint.
            except Exception as _e:  # noqa: BLE001 — an earnings hiccup must not block the scan, but SURFACE it
                s["earnings_unverified"] = True
        s["why"] = _why(s)
        rows.append(s)

    # Staleness guard: never surface a name whose bar didn't refresh with the rest
    # of the universe (the ENTG-06-29 case a reviewer caught). Demote any symbol a
    # full session behind the freshest bar out of the actionable tiers.
    newest = max((r["as_of"][:10] for r in rows if r.get("as_of")), default=None)
    if newest:
        for r in rows:
            asof = (r.get("as_of") or "")[:10]
            if asof and asof < newest and r["classification"] in ("consider", "earnings", "extended", "below_trend", "hold", "reversal"):
                r["stale"] = True
                r["classification"] = "excluded"

    # Rank the actionable. consider (real entries) first, then the WARNINGS —
    # reversal (falling knife) and extended (chasing) are SHOWN, not hidden, so the
    # trader sees why a tempting name is a skip. weak/suspect/excluded stay hidden.
    order = {"consider": 0, "earnings": 1, "reversal": 2, "extended": 3, "below_trend": 4, "hold": 5}
    actionable = [r for r in rows if r["classification"] in order]
    actionable.sort(key=lambda r: (order[r["classification"]], r.get("dist_atr") if r.get("dist_atr") is not None else 99))
    for i, r in enumerate(actionable):
        r["rank"] = i + 1

    def n(cls): return sum(r["classification"] == cls for r in rows)
    artifact = {
        "kind": "today_setups", "universe": args.universe,
        "as_of_utc": _dt.datetime.now(_dt.UTC).isoformat(),
        "counts": {"consider": n("consider"), "earnings": n("earnings"), "reversal": n("reversal"),
                   "extended": n("extended"), "below_trend": n("below_trend"), "hold": n("hold"),
                   "weak": n("weak"), "suspect": n("suspect"),
                   "excluded": n("excluded"), "scanned": len(rows)},
        "setups": actionable[: args.top],
        "excluded_symbols": [r["symbol"] for r in rows if r["classification"] == "excluded"],
        "data_suspect": [r["symbol"] for r in rows if r["classification"] == "suspect"],
        "missing": missing,
        "note": ("Ichimoku 9/26/52 (matches the chart). consider = LONG + at/above kijun support; "
                 "weak/suspect/excluded not shown. Earnings not checked (catalyst gap). Discretionary entry."),
    }

    for r in artifact["setups"]:
        log.info("%-2s %-9s %-8s %6.2f  %s", {"consider": "⭐", "earnings": "📅", "reversal": "🔪", "extended": "⚠", "below_trend": "📉", "hold": "·"}.get(r["classification"], " "),
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
        try:
            from ..run_log import log_run
            _c = artifact.get("counts", {})
            log_run("today-setups", "price_load", "ok", base=base, token=token,
                    summary=f"{args.universe}: {_c.get('consider',0)} consider, {_c.get('reversal',0)} reversal")
        except Exception:  # noqa: BLE001
            pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

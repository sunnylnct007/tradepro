"""Post-earnings put candidates — the daily screen.

WHAT THIS IS
------------
Names that reported in the last few sessions, dropped >= 8% on the print, and
sit in a market that is above its own 200-day average. For each: the strike,
the collateral, and the vol-scaled size.

TWO LAYERS, AND THAT IS THE POINT
---------------------------------
The wheel screen conflates "is this a setup?" with "what does the option cost?"
— so on 28 Aug thirty rows read "Pricing carried from the last priced screen"
and the whole board looked empty when the chain was merely dark. The owner
named the risk before this was written: *"will this again get impacted with
missing option data download"*.

    LAYER 1 — the SETUP. Report date, the drop, SPY vs its 200-SMA, the target
    strike, the size. Needs bars and an earnings date. NO option data. It
    cannot be blocked by a dark chain, a contended market-data session, or a
    closed market.

    LAYER 2 — the PRICE. Premium, open interest, spread, yield. Best-effort.
    When the chain is unavailable the row still SHOWS, carrying the strike and
    an explicit "premium unavailable" rather than vanishing.

So a dark chain costs you the yield estimate, never the candidate.

EVIDENCE + LIMITS: see `signals/post_earnings_put.py` and
POST_EARNINGS_PUT_GATES_V1.md. V2 passed all eight pre-registered gates; the
verdict on record is PAPER FORWARD TEST at small size, NOT funded. This screen
publishes eligibility, not advice.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import glob
import json
import logging
import math
import os
import statistics as st

from ..signals.post_earnings_put import (
    DROP_PCT, DTE_TARGET, MAX_REPORT_AGE, OTM_PCT, TREND_WINDOW,
    market_ok, qualifies, size_factor, strike_for,
)

log = logging.getLogger("tradepro.post_earnings_puts")
STORE = os.path.expanduser("~/.tradepro/bar_cache/us_etf")


def _bars(sym: str):
    import pandas as pd
    parts = sorted(glob.glob(f"{STORE}/{sym}/1d/*.parquet"))
    if not parts:
        return None, None
    # Enough partitions to cover a 200-SESSION average. Partitions are MONTHLY
    # and a month holds ~21 sessions, so 200 sessions needs ~10 months. This
    # read 4 and every symbol then failed the `len(c) < TREND_WINDOW + 5`
    # check, so the screen reported "0 recent reporters" and a CLOSED market
    # gate on data it actually held. 15 gives a full year plus headroom.
    df = pd.concat([pd.read_parquet(p) for p in parts[-15:]])
    idx = pd.to_datetime(df["ts"] if "ts" in df.columns else df.index, utc=True)
    df = df.assign(_i=idx).drop_duplicates("_i").set_index("_i").sort_index()
    return ([float(x) for x in df["close"].tolist()],
            [str(x)[:10] for x in df.index])


def _annual_vol(closes, i, window=60):
    lo = max(1, i - window)
    r = [closes[k] / closes[k - 1] - 1 for k in range(lo, i + 1) if closes[k - 1] > 0]
    if len(r) < 20:
        return None
    sd = st.pstdev(r)
    return sd * math.sqrt(252) if sd > 0 else None


def _recent_reports(api_base: str) -> dict[str, str]:
    """symbol -> most recent report date, from the CENTRAL store.

    The central calendar holds one event per symbol, which is a forward
    calendar and useless for backtesting — but exactly right HERE, because the
    screen only cares about the most recent report.
    """
    out: dict[str, str] = {}
    from ..earnings import _calendar_store_events, _store_is_authoritative
    from ..universe import harvest_symbols
    today = _dt.date.today().isoformat()
    for sym in harvest_symbols(STORE):
        try:
            data = _calendar_store_events(sym, api_base) or {}
            if not _store_is_authoritative(data.get("store")):
                continue
            past = sorted(str(e["report_date"])[:10]
                          for e in (data.get("events") or [])
                          if e.get("report_date") and str(e["report_date"])[:10] <= today)
            if past:
                out[sym] = past[-1]
        except Exception:  # noqa: BLE001 — one symbol must not kill the screen
            continue
    return out


def scan(api_base: str) -> tuple[list[dict], list[dict], dict]:
    """Returns (candidates, near_misses, market)."""
    spy_c, spy_d = _bars("SPY")
    market: dict = {"ok": None, "reason": "SPY bars unavailable"}
    if spy_c and len(spy_c) > TREND_WINDOW:
        sma = sum(spy_c[-TREND_WINDOW:]) / TREND_WINDOW
        ok = market_ok(spy_c[-1], sma)
        market = {
            "ok": ok, "spy_close": round(spy_c[-1], 2), "spy_sma200": round(sma, 2),
            "pct_above": round(100 * (spy_c[-1] / sma - 1), 2), "as_of": spy_d[-1],
            "reason": ("SPY above its 200-day average" if ok else
                       "SPY BELOW its 200-day average — the regime gate is CLOSED"),
        }

    reports = _recent_reports(api_base)
    cands: list[dict] = []
    near: list[dict] = []

    for sym, rdate in reports.items():
        c, d = _bars(sym)
        if not c or len(c) < TREND_WINDOW + 5:
            continue
        try:
            i = d.index(rdate)
        except ValueError:
            continue
        j = i + 1                                   # the session AFTER the print
        if j >= len(c):
            continue                                # reaction not printed yet
        age = len(c) - 1 - j
        if age > MAX_REPORT_AGE:
            continue
        move = c[j] / c[j - 1] - 1 if c[j - 1] > 0 else None
        vol = _annual_vol(c, len(c) - 1)
        spot = c[-1]
        row = {
            "symbol": sym, "report_date": rdate, "sessions_since": age,
            "report_move_pct": round(100 * move, 2) if move is not None else None,
            "spot": round(spot, 2),
            "strike": strike_for(spot),
            "otm_pct": round(100 * OTM_PCT, 1),
            "dte_target": DTE_TARGET,
            "annual_vol_pct": round(100 * vol, 1) if vol else None,
            "size_factor": round(size_factor(vol), 2),
            "collateral_usd": round(strike_for(spot) * 100 * size_factor(vol), 0),
        }
        if not qualifies(move):
            row["why_not"] = (f"fell {100 * move:.1f}% on the report, needs "
                              f"{100 * DROP_PCT:.0f}%") if move is not None \
                else "report-day move unavailable"
            near.append(row)
            continue
        if market.get("ok") is not True:
            row["why_not"] = market["reason"]
            near.append(row)
            continue
        cands.append(row)

    cands.sort(key=lambda r: r.get("report_move_pct") or 0)
    near.sort(key=lambda r: r.get("report_move_pct") or 0)
    return cands, near, market


def main() -> int:
    ap = argparse.ArgumentParser(prog="tradepro-post-earnings-puts")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--api-base", default=None)
    args = ap.parse_args()
    logging.basicConfig(level=logging.WARNING,
                        format="%(asctime)s %(levelname)s %(message)s")

    base = args.api_base
    if base is None:
        from .push_to_api import load_credentials
        base, _ = load_credentials()

    cands, near, market = scan(base)
    art = {
        "kind": "post_earnings_puts",
        "as_of_utc": _dt.datetime.now(_dt.UTC).isoformat(),
        "market": market,
        "rule": {
            "entry": f"report-day drop <= {100 * DROP_PCT:.0f}%, within "
                     f"{MAX_REPORT_AGE} sessions, SPY above its 200-SMA",
            "strike": f"{100 * OTM_PCT:.0f}% OTM", "dte": DTE_TARGET,
        },
        "evidence": {
            "gates_file": "POST_EARNINGS_PUT_GATES_V1.md",
            "v2_trades": 229, "v2_win_pct": 89.5, "v2_mean_pct": 1.29,
            "v2_p5_pct": -4.72, "v2_worst_pct": -23.40,
            "null_mean_pct": -0.15,
            "verdict": "PAPER FORWARD TEST at small size — NOT FUNDED",
            "limits": [
                "Earnings history begins ~Oct 2020 — one regime only.",
                "W6 (2022 not a losing year) passed on NINE events.",
                "Worst single trade after filtering and sizing: -23.4%.",
            ],
        },
        "evaluated": len(cands) + len(near),
        "candidates": cands,
        "near_misses": near[:10],
    }

    if args.json:
        print(json.dumps(art, indent=1))
        return 0

    m = market
    print(f"post-earnings puts — {art['as_of_utc'][:16]}Z")
    print(f"  MARKET GATE: {'OPEN' if m.get('ok') else 'CLOSED'} — {m['reason']}")
    if m.get("spy_close"):
        print(f"    SPY {m['spy_close']} vs 200-SMA {m['spy_sma200']} "
              f"({m['pct_above']:+.2f}%) as of {m['as_of']}")
    print(f"  scanned {art['evaluated']} recent reporters · {len(cands)} candidate(s)\n")

    if cands:
        print(f"  {'sym':<7}{'reported':<12}{'move':>8}{'spot':>10}{'strike':>10}"
              f"{'vol':>7}{'size':>7}{'collateral':>12}")
        for r in cands:
            print(f"  {r['symbol']:<7}{r['report_date']:<12}{r['report_move_pct']:>7.1f}%"
                  f"{r['spot']:>10.2f}{r['strike']:>10.2f}"
                  f"{(r['annual_vol_pct'] or 0):>6.0f}%{r['size_factor']:>7.2f}"
                  f"{r['collateral_usd']:>11,.0f}")
        print("\n  Strike and size come from BARS only — no option data needed, so a")
        print("  dark chain cannot hide a setup. Premium/OI/spread are a separate")
        print("  best-effort layer and are not required to see the candidate.")
    else:
        print("  none today.")
        if near:
            print(f"\n  CLOSEST — recent reporters that did not qualify:")
            print(f"  {'sym':<7}{'reported':<12}{'move':>8}   why not")
            for r in near[:8]:
                mv = f"{r['report_move_pct']:>7.1f}%" if r['report_move_pct'] is not None else "      —"
                print(f"  {r['symbol']:<7}{r['report_date']:<12}{mv}   {r['why_not']}")

    print(f"\n  [{art['evidence']['verdict']}]")
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())

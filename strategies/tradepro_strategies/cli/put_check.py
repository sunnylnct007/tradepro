"""Is THIS name a sensible put-sell right now? — numbers, not a verdict.

Owner, 30 Aug 2026: *"i want to be able to see if Google is right option put
selling or not ... u shd be able to suport me witg numbers"*.

The wheel board answers "which of 82 names clears my gates". This answers a
different question, and the one actually asked: **you have picked a name — what
do the numbers say about selling a put on it?**

Every line is a measurement or a named gap. No score, no BUY/SELL, no
recommendation. A tool that ranks a name invites the rank to be trusted, and the
judgement here is the owner's.

THE HISTORICAL BLOCK IS THE POINT. Everything else describes today; the outcome
table says what actually happened the last N times you could have sold this put
on this name — expired worthless, assigned, and how far in the money. That is
computed from BARS, so it needs no option chain and cannot be blocked by a dark
feed. It is also honest about what it cannot model: no premium is assumed, so
"assigned" counts even when the premium would have covered the loss.
"""
from __future__ import annotations

import argparse
import glob
import json
import math
import os
import statistics as st

BASE = os.path.expanduser("~/.tradepro/bar_cache/us_etf")
R = os.path.expanduser("~/.tradepro/research")


def _load(sym: str):
    fs = sorted(glob.glob(f"{BASE}/{sym}/1d/*.parquet"))
    if not fs:
        return None
    import pandas as pd
    try:
        df = pd.concat([pd.read_parquet(f) for f in fs]).sort_index()
    except Exception:
        return None
    return df[~df.index.duplicated(keep="last")]


def _sma(c, i, n):
    return sum(c[i - n + 1:i + 1]) / n


def historical_put_outcomes(c, low, dates, otm_pct: float, dte: int):
    """What happened the last time you sold this put, every time.

    Sells an `otm_pct` OTM put every `dte` sessions and records the outcome at
    expiry. Uses the session LOW across the holding period for the
    'touched' count — a put can be assigned early, and an outcome measured on
    the close alone would understate assignment risk.
    """
    out = []
    step = max(5, dte // 2)          # overlapping samples, not one per expiry
    i = 200
    while i < len(c) - dte - 1:
        strike = c[i] * (1 - otm_pct)
        window_lows = low[i + 1:i + dte + 1]
        window_close = c[i + dte]
        if not window_lows:
            i += step
            continue
        out.append({
            "date": dates[i], "spot": c[i], "strike": round(strike, 2),
            "expiry_close": window_close,
            "itm_at_expiry": window_close < strike,
            "touched": min(window_lows) < strike,
            "worst_pct": round(100 * (min(window_lows) / strike - 1), 2),
        })
        i += step
    return out


def describe(sym: str, otm_pct: float, dte: int) -> None:
    from ..signals.mean_reversion import TREND_WINDOW
    print(f"\n{'=' * 70}\n{sym} — put-sell check   ({otm_pct:.0%} OTM, {dte} sessions)")
    df = _load(sym)
    if df is None:
        print("  NO BARS. Nothing here can be judged — harvest it first.")
        return
    c = df["close"].tolist(); low = df["low"].tolist()
    dates = [str(x)[:10] for x in df.index]
    if len(c) < TREND_WINDOW + dte + 20:
        print(f"  ONLY {len(c)} bars — too thin to judge a {dte}-session hold.")
        return
    i = len(c) - 1
    import datetime as _dt
    age = (_dt.date.today() - _dt.date.fromisoformat(dates[i])).days
    if age > 5:
        print(f"  ✗ STALE — last bar {dates[i]} ({age}d old). Re-harvest first.")

    spot = c[i]
    sma200 = _sma(c, i, TREND_WINDOW)
    hi52 = max(c[max(0, i - 252):i + 1])
    print(f"\n  WOULD YOU WANT TO OWN IT?   (a short put is an obligation to buy)")
    print(f"    spot {spot:.2f}   200-SMA {sma200:.2f}   {'ABOVE' if spot > sma200 else 'BELOW — downtrend'}")
    print(f"    {100 * (spot / hi52 - 1):+.1f}% from its 52-week high ({hi52:.2f})")
    print(f"    strike if sold now: {spot * (1 - otm_pct):.2f}"
          f"   collateral {spot * (1 - otm_pct) * 100:,.0f}")

    # ── VOL: is the premium rich or thin? ────────────────────────────────
    rets = [math.log(c[k] / c[k - 1]) for k in range(i - 29, i + 1) if c[k - 1] > 0]
    hv30 = st.pstdev(rets) * math.sqrt(252) if len(rets) > 5 else None
    print(f"\n  IS THE PREMIUM WORTH IT?")
    print(f"    realised vol (30d): {100 * hv30:.1f}%" if hv30 else "    realised vol: n/a")
    try:
        from ..quant_engine.options.iv_rank import fetch_iv_rank_web
        ivr = fetch_iv_rank_web(sym)
        if ivr.available and ivr.iv:
            ratio = (ivr.iv / hv30) if hv30 else None
            print(f"    implied vol (IBKR): {100 * ivr.iv:.1f}%"
                  + (f"   IV/HV {ratio:.2f}" if ratio else ""))
            if ratio:
                print("      " + ("IV ABOVE realised — you are paid more than the stock has moved"
                                  if ratio > 1 else
                                  "IV BELOW realised — you are paid LESS than the stock has moved"))
            print(f"    IV-rank: {ivr.iv_rank if ivr.iv_rank is not None else 'not yet'}"
                  f"   ({ivr.reason or ''})")
        else:
            print(f"    implied vol: unavailable ({ivr.reason if ivr else 'no result'})")
    except Exception as exc:  # noqa: BLE001
        print(f"    implied vol: lookup failed ({str(exc)[:70]})")

    # ── EARNINGS ─────────────────────────────────────────────────────────
    try:
        ev = (json.load(open(f"{R}/earnings_history.json")) or {}).get(sym) or []
    except Exception:
        ev = []
    print(f"\n  EARNINGS")
    if not ev:
        print("    none held — proximity CANNOT be checked for this name.")
    else:
        today = dates[i]
        nxt = [x for x in ev if x > today]
        past = [x for x in ev if x <= today]
        print(f"    last {past[-1] if past else '?'}   next {nxt[0] if nxt else '?'}")
        if nxt:
            d = (_dt.date.fromisoformat(nxt[0]) - _dt.date.fromisoformat(today)).days
            if d <= dte:
                print(f"    ⚠ reports in {d}d — INSIDE a {dte}-session hold. "
                      "IV-crush and gap risk both land on you.")

    # ── FUNDAMENTALS ─────────────────────────────────────────────────────
    try:
        f = (json.load(open(f"{R}/fundamentals.json")) or {}).get(sym) or {}
        info = f.get("info") or {}
    except Exception:
        info = {}
    if info.get("trailingPE") or info.get("returnOnEquity"):
        pe = info.get("trailingPE"); roe = info.get("returnOnEquity")
        print(f"\n  THE COMPANY (current snapshot, not point-in-time)")
        print(f"    P/E {pe if pe is None else round(pe, 1)}"
              f"   fwd {info.get('forwardPE') and round(info['forwardPE'], 1)}"
              f"   ROE {roe is not None and f'{100 * roe:.1f}%' or 'n/a'}")

    # ── TENOR COMPARISON — weekly vs monthly ─────────────────────────────
    #
    # Owner, 30 Aug: *"u shd be able to split them in mnthly and weekly DTE"* and
    # *"i had a good success in selling MRVL in a weekly period once"*.
    #
    # Tenor changes the trade, not just the timing. A weekly put gives the stock
    # a fifth of the time to reach the strike, so it is assigned far less often —
    # but you collect far less premium each time and must repeat it ~4x as often
    # to earn the same, paying the spread every time. This table shows the RISK
    # half honestly; the premium half needs a live chain, which we cannot reach
    # for a 10% OTM strike yet, so it is NOT guessed here.
    print(f"\n  BY TENOR — same {otm_pct:.0%} OTM strike, different hold")
    print(f"    {'sessions':<10}{'samples':>9}{'worthless':>11}{'assigned':>10}"
          f"{'touched':>9}{'worst':>9}")
    for d in (5, 10, 21, 30, 45):
        rows = historical_put_outcomes(c, low, dates, otm_pct, d)
        if len(rows) < 20:
            continue
        itm = sum(1 for r in rows if r["itm_at_expiry"])
        tch = sum(1 for r in rows if r["touched"])
        wst = min(r["worst_pct"] for r in rows)
        label = {5: "5 (weekly)", 10: "10 (2wk)", 21: "21 (monthly)",
                 30: "30", 45: "45"}.get(d, str(d))
        print(f"    {label:<10}{len(rows):>9}{100 * (1 - itm / len(rows)):>10.0f}%"
              f"{100 * itm / len(rows):>9.0f}%{100 * tch / len(rows):>8.0f}%"
              f"{wst:>8.1f}%")
    print("    A shorter hold is assigned less often because the stock has less")
    print("    time to fall — NOT because it is safer per unit of premium. The")
    print("    premium comparison needs a live chain and is not guessed here.")

    # ── THE HISTORY — what actually happened ─────────────────────────────
    res = historical_put_outcomes(c, low, dates, otm_pct, dte)
    print(f"\n  WHAT HAPPENED THE LAST {len(res)} TIMES ({dates[200]} → {dates[-1]})")
    if not res:
        print("    not enough history.")
        return
    itm = [r for r in res if r["itm_at_expiry"]]
    touched = [r for r in res if r["touched"]]
    print(f"    expired worthless (kept the premium): {100 * (1 - len(itm) / len(res)):.0f}%"
          f"   ({len(res) - len(itm)}/{len(res)})")
    print(f"    ASSIGNED at expiry:                   {100 * len(itm) / len(res):.0f}%"
          f"   ({len(itm)}/{len(res)})")
    print(f"    touched the strike at any point:      {100 * len(touched) / len(res):.0f}%"
          f"   ({len(touched)}/{len(res)})")
    if itm:
        worst = min(r["worst_pct"] for r in res)
        avg_itm = sum(100 * (r['expiry_close'] / r['strike'] - 1) for r in itm) / len(itm)
        print(f"    when assigned, average {avg_itm:.1f}% below the strike at expiry")
        print(f"    worst drawdown below the strike:  {worst:.1f}%")
    print("\n    NO PREMIUM IS ASSUMED. 'Assigned' counts even where the premium")
    print("    would have covered the loss — so this is the risk BEFORE income.")

    print("\n  NOT KNOWN HERE: live option premium (the chain resolves by month and")
    print("  cannot reach a 10% OTM strike — see SHARED_CONTEXT), sector, guidance.")


def main() -> int:
    ap = argparse.ArgumentParser(description="Is this name a sensible put-sell? Numbers only.")
    ap.add_argument("symbols", nargs="+")
    ap.add_argument("--otm", type=float, default=0.10, help="OTM fraction (default 0.10)")
    ap.add_argument("--dte", type=int, default=30, help="sessions held (default 30)")
    args = ap.parse_args()
    for s in args.symbols:
        describe(s.strip().upper(), args.otm, args.dte)
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

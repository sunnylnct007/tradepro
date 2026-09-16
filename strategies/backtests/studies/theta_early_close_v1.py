"""THETA_EARLY_CLOSE_GATES_V1 — measured on our OWN stored chains.

Gates frozen in THETA_EARLY_CLOSE_GATES_V1.md before this ran.

Sell at the BID on day 0, buy back at the ASK on day N: the pessimistic side
of both spreads, which is the cost a Black-Scholes answer cannot see. Nothing
here is simulated — every price is one we captured.
"""
import collections
import statistics as st
import sys

import requests

sys.path.insert(0, ".")
from tradepro_strategies.cli.push_to_api import load_credentials  # noqa: E402

SYMS = ["IWM", "NVDA", "MU", "GDX", "SLV", "DELL", "XLU", "GS", "TSLA", "XLE",
        "AAPL", "MSFT", "AMD", "F", "PFE", "KO", "T", "XOM", "CVX", "WMT"]


def load():
    base, token = load_credentials(); base = base.rstrip("/")
    H = {"Authorization": f"Bearer {token}"}
    tracks = collections.defaultdict(dict)
    for s in SYMS:
        try:
            r = requests.get(f"{base}/api/options/quotes-daily/{s}?days=90",
                             headers=H, timeout=60)
            qs = r.json().get("quotes") or []
        except Exception:  # noqa: BLE001
            continue
        for q in qs:
            if q.get("right") != "P":
                continue
            b, a = q.get("bid"), q.get("ask")
            if b is None or a is None or float(b) <= 0 or float(a) <= 0:
                continue
            k = (s, str(q["expiry"])[:10], float(q["strike"]))
            tracks[k][str(q["capture_date"])[:10]] = (float(b), float(a),
                                                      q.get("delta"))
    return tracks


def main():
    tracks = load()
    print(f"contracts with usable two-sided quotes: {len(tracks)}")

    # (holding sessions) -> list of (symbol, net annualised %, dte_at_entry)
    by_n = collections.defaultdict(list)
    for (sym, exp, strike), obs in tracks.items():
        days = sorted(obs)
        if len(days) < 2:
            continue
        d0 = days[0]
        bid0, ask0, _ = obs[d0]
        mid0 = (bid0 + ask0) / 2
        if mid0 <= 0.05:          # sub-nickel options: spread dominates, not tradable
            continue
        import datetime as dt
        e = dt.date.fromisoformat(exp)
        dte0 = (e - dt.date.fromisoformat(d0)).days
        if dte0 <= 0:
            continue
        for dN in days[1:]:
            n = (dt.date.fromisoformat(dN) - dt.date.fromisoformat(d0)).days
            if n <= 0:
                continue
            _, askN, _ = obs[dN]
            # SELL at bid, BUY BACK at ask — both spreads paid.
            pnl = bid0 - askN
            # Return on the collateral actually tied up: cash-secured = strike.
            ann = 100 * (pnl / strike) * (365 / n)
            by_n[n].append((sym, ann, dte0, 100 * pnl / mid0))
    print()
    print(f"{'hold':>5}{'n':>7}{'mean ann%':>12}{'median ann%':>13}"
          f"{'% profitable':>14}{'mean % of premium kept':>24}")
    for n in sorted(by_n):
        rows = by_n[n]
        if len(rows) < 30:
            continue
        anns = [r[1] for r in rows]
        keeps = [r[3] for r in rows]
        win = 100 * sum(1 for a in anns if a > 0) / len(anns)
        print(f"{n:>5}{len(rows):>7}{st.fmean(anns):>+11.1f}%"
              f"{st.median(anns):>+12.1f}%{win:>13.0f}%{st.fmean(keeps):>+23.1f}%")
    return by_n


if __name__ == "__main__":
    main()

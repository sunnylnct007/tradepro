"""QUIVER_CONGRESS_GATES_V1 — does following disclosed congressional buying work?

Bars frozen in QUIVER_CONGRESS_GATES_V1.md before this ran. Entry is the close
of the DISCLOSURE date (ReportDate), never the transaction date — the latter
is information we could not have had. Placebo: the same number of entries on
random dates in the same symbols over the same decade, which controls for
these being large caps in a rising market.
"""
import datetime as dt
import random
import statistics as st
import sys

import numpy as np
import requests

sys.path.insert(0, ".")
from tradepro_strategies.universe import universe_symbols  # noqa: E402
from tradepro_strategies.cli.build_universe import _load  # noqa: E402
from tradepro_strategies.quiver import _token  # noqa: E402

HORIZONS = (21, 63, 126)
SPLIT = "2021-01-01"
random.seed(20260915)          # deterministic placebo


def _fwd(closes, i, h):
    j = min(i + h, len(closes) - 1)
    if j <= i or closes[i] <= 0:
        return None
    return 100 * (closes[j] / closes[i] - 1)


def main():
    tok = _token()
    if not tok:
        print("no quiver token")
        return 1
    H = {"Authorization": f"Token {tok}"}

    syms = sorted(universe_symbols(strict=False))
    bars, spy = {}, None
    try:
        d = _load("SPY")
        spy = ({str(x)[:10]: i for i, x in enumerate(d.index)}, d["close"].tolist())
    except Exception:  # noqa: BLE001
        pass

    buys = []           # (sym, disclosure_date)
    fetched = 0
    for s in syms:
        try:
            r = requests.get(
                f"https://api.quiverquant.com/beta/historical/congresstrading/{s}",
                headers=H, timeout=20)
            if r.status_code != 200:
                continue
            rows = r.json()
            if not isinstance(rows, list):
                continue
            fetched += 1
            for x in rows:
                if "purchase" not in str(x.get("Transaction", "")).lower():
                    continue
                rd = str(x.get("ReportDate") or "")[:10]
                if len(rd) == 10:
                    buys.append((s, rd))
        except Exception:  # noqa: BLE001
            continue
    print(f"symbols with congress history: {fetched}/{len(syms)}  ·  disclosed purchases: {len(buys)}")

    for s in {b[0] for b in buys}:
        try:
            d = _load(s)
            if d is None or len(d) < 300:
                continue
            bars[s] = ({str(x)[:10]: i for i, x in enumerate(d.index)},
                       d["close"].tolist())
        except Exception:  # noqa: BLE001
            continue

    real = {h: [] for h in HORIZONS}
    real_ex = {h: [] for h in HORIZONS}
    dates_by_sym = {}
    for sym, rd in buys:
        b = bars.get(sym)
        if not b:
            continue
        idx, closes = b
        i = idx.get(rd)
        if i is None:                       # not a session — take the next one
            later = [v for k, v in idx.items() if k >= rd]
            if not later:
                continue
            i = min(later)
        dates_by_sym.setdefault(sym, []).append(i)
        for h in HORIZONS:
            v = _fwd(closes, i, h)
            if v is None:
                continue
            real[h].append((rd, v))
            if spy:
                sidx, sclose = spy
                si = sidx.get(rd)
                if si is not None:
                    sv = _fwd(sclose, si, h)
                    if sv is not None:
                        real_ex[h].append((rd, v - sv))

    # PLACEBO — same symbols, same count, random dates.
    plac = {h: [] for h in HORIZONS}
    plac_ex = {h: [] for h in HORIZONS}
    for sym, idxs in dates_by_sym.items():
        idx, closes = bars[sym]
        inv = {v: k for k, v in idx.items()}
        lo, hi = 250, len(closes) - max(HORIZONS) - 1
        if hi <= lo:
            continue
        for _ in idxs:
            i = random.randint(lo, hi)
            for h in HORIZONS:
                v = _fwd(closes, i, h)
                if v is None:
                    continue
                plac[h].append((inv.get(i, ""), v))
                if spy:
                    sidx, sclose = spy
                    si = sidx.get(inv.get(i, ""))
                    if si is not None:
                        sv = _fwd(sclose, si, h)
                        if sv is not None:
                            plac_ex[h].append((inv.get(i, ""), v - sv))

    def stats(pairs):
        a = np.array([v for _, v in pairs]) if pairs else np.array([])
        if a.size == 0:
            return None
        return {"n": int(a.size), "mean": float(a.mean()),
                "median": float(np.median(a))}

    print(f"\n{'horizon':>8}{'n':>7}{'congress mean':>15}{'placebo mean':>14}"
          f"{'edge':>8}{'cong med':>10}{'plac med':>10}")
    results = {}
    for h in HORIZONS:
        r, p = stats(real[h]), stats(plac[h])
        if not r or not p:
            continue
        edge = r["mean"] - p["mean"]
        results[h] = (r, p, edge)
        print(f"{h:>8}{r['n']:>7}{r['mean']:>+14.2f}%{p['mean']:>+13.2f}%"
              f"{edge:>+7.2f}{r['median']:>+9.2f}%{p['median']:>+9.2f}%")

    print("\nSPY-RELATIVE (excess over SPY at the same dates)")
    ex_results = {}
    for h in HORIZONS:
        r, p = stats(real_ex[h]), stats(plac_ex[h])
        if not r or not p:
            continue
        ex_results[h] = r["mean"] - p["mean"]
        print(f"{h:>8}{r['n']:>7}{r['mean']:>+14.2f}%{p['mean']:>+13.2f}%"
              f"{r['mean']-p['mean']:>+7.2f}")

    print("\nERA SPLIT (congress mean by period)")
    for h in HORIZONS:
        pre = [v for d_, v in real[h] if d_ < SPLIT]
        post = [v for d_, v in real[h] if d_ >= SPLIT]
        pre_p = [v for d_, v in plac[h] if d_ < SPLIT]
        post_p = [v for d_, v in plac[h] if d_ >= SPLIT]
        if pre and post:
            print(f"{h:>8}  pre-2021 {st.fmean(pre):+6.2f}% (n={len(pre)}, "
                  f"placebo {st.fmean(pre_p):+6.2f}%)  ·  post-2021 "
                  f"{st.fmean(post):+6.2f}% (n={len(post)}, placebo "
                  f"{st.fmean(post_p):+6.2f}%)")

    print("\n— gates (frozen in QUIVER_CONGRESS_GATES_V1.md) —")
    for h in HORIZONS:
        if h not in results:
            continue
        r, p, edge = results[h]
        g0 = r["n"] >= 500
        g1 = edge >= 0.50
        g3 = ex_results.get(h, -99) >= 0.50
        g4 = r["median"] > p["median"]
        print(f"{h:>4}s  G0 n>=500 {'PASS' if g0 else 'FAIL'} ({r['n']})  "
              f"G1 edge>=0.50 {'PASS' if g1 else 'FAIL'} ({edge:+.2f})  "
              f"G3 spy-rel {'PASS' if g3 else 'FAIL'} ({ex_results.get(h, float('nan')):+.2f})  "
              f"G4 median {'PASS' if g4 else 'FAIL'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

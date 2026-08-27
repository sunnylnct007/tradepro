"""Two candidate improvements to Swing, graded against the live rule.

Gates and predictions pre-registered in SWING_IMPROVE_GATES_V1.md (9a065b7)
BEFORE this ran.

  1. loosen the trigger 2.5 -> 2.25 sigma  (more trades, lower average)
  2. skip entries when SPY is below its own 200-day  (fewer trades, higher
     average — and the below-200 trades are still POSITIVE, so the cost is real)

S5 exists because those fail in opposite directions and mean-per-trade alone
would reward the wrong one.
"""
from __future__ import annotations

import statistics as st

from tradepro_strategies.cli.build_universe import _load
from tradepro_strategies.signals import mean_reversion as mr
from tradepro_strategies.universe import poison_check, universe_symbols


def sma(x, i, n):
    return sum(x[i - n + 1:i + 1]) / n


def spy_regime() -> dict[str, bool]:
    """date -> is SPY above its own 200-day close average."""
    d = _load("SPY")
    c = d["close"].tolist()
    dt = [str(x)[:10] for x in d.index]
    out = {}
    for i in range(mr.TREND_WINDOW, len(c)):
        out[dt[i]] = c[i] > sma(c, i, mr.TREND_WINDOW)
    return out


def run(sigma: float, regime: dict | None):
    res = []
    for si, sym in enumerate(universe_symbols()):
        d = _load(sym)
        if d is None or len(d) < 300:
            continue
        c = d["close"].tolist(); h = d["high"].tolist()
        l = d["low"].tolist(); o = d["open"].tolist()
        dt = [str(x)[:10] for x in d.index]
        if not poison_check(c, d["volume"].tolist() if "volume" in d else None)[0]:
            continue
        i = 210
        while i < len(c) - 1:
            w = c[i - mr.BB_WINDOW + 1:i + 1]
            sd = st.pstdev(w); m = sum(w) / mr.BB_WINDOW
            fires = (sd > 0 and c[i] < m - sigma * sd
                     and c[i] > sma(c, i, mr.TREND_WINDOW))
            if regime is not None and fires and not regime.get(dt[i], True):
                fires = False                      # market below its 200-day
            if not fires:
                i += 1; continue
            e = c[i]; stop = e * (1 - mr.STOP_PCT); out = None
            for j in range(i + 1, min(len(c), i + mr.MAX_HOLD + 1)):
                t = sma(c, j, mr.BB_WINDOW)
                if l[j] <= stop: out = (100 * (min(stop, o[j]) / e - 1), j); break
                if h[j] >= t: out = (100 * (max(t, o[j]) / e - 1), j); break
            if out is None:
                j = min(len(c) - 1, i + mr.MAX_HOLD); out = (100 * (c[j] / e - 1), j)
            res.append({"sym": sym, "si": si, "date": dt[i], "pct": out[0]})
            i = out[1] + 1
    return res


def stats(rs):
    a = [r["pct"] for r in rs]
    return {"n": len(a), "win": 100 * sum(1 for x in a if x > 0) / len(a),
            "mean": st.mean(a), "worst": min(a), "total": sum(a)}


if __name__ == "__main__":
    reg = spy_regime()
    base = run(mr.SIGMA, None)
    cands = {
        "LIVE  2.50σ": base,
        "C1    2.25σ": run(2.25, None),
        "C2    regime": run(mr.SIGMA, reg),
        "C1+C2 both": run(2.25, reg),
    }
    print(f"{'variant':<14}{'n':>7}{'win%':>7}{'mean%':>8}{'worst%':>9}{'TOTAL%':>10}")
    for k, v in cands.items():
        s = stats(v)
        print(f"{k:<14}{s['n']:>7}{s['win']:>6.1f}%{s['mean']:>7.2f}%"
              f"{s['worst']:>8.1f}%{s['total']:>9.0f}%")

    b = stats(base)
    mid = sorted(r["date"] for r in base)[len(base) // 2]
    print(f"\n{'two-split — mean/trade vs LIVE':<44}")
    print(f"{'variant':<14}{'time 1st':>11}{'time 2nd':>11}{'sym even':>11}{'sym odd':>11}")
    for k, v in cands.items():
        if k.startswith("LIVE"):
            continue
        row = f"{k:<14}"
        for cname, pred in (("time 1st", lambda r: r["date"] < mid),
                            ("time 2nd", lambda r: r["date"] >= mid),
                            ("sym even", lambda r: r["si"] % 2 == 0),
                            ("sym odd", lambda r: r["si"] % 2 == 1)):
            bb = stats([r for r in base if pred(r)])["mean"]
            cc = stats([r for r in v if pred(r)])["mean"]
            row += f"{cc - bb:>+10.2f}%"
        print(row)

    print("\nGATES vs the live rule")
    for k, v in cands.items():
        if k.startswith("LIVE"):
            continue
        s = stats(v)
        print(f"  {k}")
        print(f"    S1 mean beats live          {'PASS' if s['mean'] > b['mean'] else 'FAIL'}"
              f"  ({s['mean']:+.2f}% vs {b['mean']:+.2f}%)")
        print(f"    S4 worst within 2pt         {'PASS' if s['worst'] >= b['worst'] - 2 else 'FAIL'}"
              f"  ({s['worst']:.1f}% vs {b['worst']:.1f}%)")
        print(f"    S5 total not lower          {'PASS' if s['total'] >= b['total'] else 'FAIL'}"
              f"  ({s['total']:.0f}% vs {b['total']:.0f}%)")

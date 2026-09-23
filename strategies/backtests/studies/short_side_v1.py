"""SHORT_SIDE_GATES_V1 — the exact mirrors of the two rules that passed long.

Gates frozen in SHORT_SIDE_GATES_V1.md before this ran.

Nothing is re-tuned. Each mirror uses the LONG rule's own parameters, inverted,
so a difference in result is a difference in the market and not in the fitting.
Same universe, same window, same fill assumptions as swing_out_of_sample_v1 and
mom_v3, so the two sides are comparable.
"""
import datetime as dt
import statistics as st
import sys

import numpy as np

sys.path.insert(0, ".")
from backtests.studies.swing_out_of_sample_v1 import _load_many, _sma  # noqa: E402
from tradepro_strategies.signals.mean_reversion import (  # noqa: E402
    BB_WINDOW as W, MAX_HOLD, SIGMA, STOP_PCT, TREND_WINDOW)
from tradepro_strategies.universe import universe_symbols  # noqa: E402

MAX_DAY_MOVE = 0.35
TRAIL_PCT = 0.08
MOM_MAX_HOLD = 60
G_MIN_N, G1_WIN, G3_HOLD, G4_TAIL, G5_WORST = 1000, 45.0, 40, 35.0, -25.0


def short_mean_reversion(data: dict) -> list[dict]:
    """Fade a spike: +2.25σ ABOVE the mean while BELOW the 200-day."""
    out = []
    for sym, (c, h, l, o, d) in sorted(data.items()):
        n = len(c)
        i = 210
        while i < n - 1:
            m = _sma(c, i, W)
            sd = st.pstdev(c[i - W + 1:i + 1])
            # mirror: above the band, below the trend floor
            if not (sd > 0 and c[i] < _sma(c, i, TREND_WINDOW)):
                i += 1
                continue
            if (c[i] - m) / sd < SIGMA:
                i += 1
                continue
            entry = c[i]
            stop = entry * (1 + STOP_PCT)        # a short stops OUT on a rise
            res = None
            for j in range(i + 1, min(n, i + MAX_HOLD + 1)):
                if c[j - 1] <= 0 or abs(c[j] / c[j - 1] - 1) > MAX_DAY_MOVE:
                    res = "corrupt"
                    break
                tgt = _sma(c, j, W)              # cover at the mean
                if h[j] >= stop:                 # gap-through fills at the open
                    fill = max(stop, o[j])
                    res = (100 * (entry / fill - 1), j - i)
                    break
                if l[j] <= tgt:
                    fill = min(tgt, o[j])
                    res = (100 * (entry / fill - 1), j - i)
                    break
            if res == "corrupt":
                i = j + 1
                continue
            if res is None:
                j = min(n - 1, i + MAX_HOLD)
                res = (100 * (entry / c[j] - 1), j - i)
            out.append({"ret": res[0], "date": d[i], "sym": sym, "bars": res[1]})
            i += max(1, res[1]) + 1
    return out


def short_momentum(data: dict) -> list[dict]:
    """Rally to the 10-SMA inside an established DOWNtrend."""
    out = []
    for sym, (c, h, l, o, d) in sorted(data.items()):
        n = len(c)
        i = 210
        while i < n - 1:
            s10, s20, s50 = _sma(c, i, 10), _sma(c, i, 20), _sma(c, i, 50)
            s200 = _sma(c, i, TREND_WINDOW)
            prev10 = _sma(c, i - 1, 10)
            # mirror of the long: downtrend, below the 20, rallied back to the 10
            if not (s20 < s50 and c[i] < s20 and c[i] < s200
                    and c[i] >= s10 and c[i - 1] < prev10):
                i += 1
                continue
            entry = c[i]
            hard = entry * (1 + STOP_PCT)
            trough = entry
            res = None
            for j in range(i + 1, min(n, i + MOM_MAX_HOLD + 1)):
                if c[j - 1] <= 0 or abs(c[j] / c[j - 1] - 1) > MAX_DAY_MOVE:
                    res = "corrupt"
                    break
                if h[j] >= hard:
                    res = (100 * (entry / max(hard, o[j]) - 1), j - i)
                    break
                trough = min(trough, l[j])
                trail = trough * (1 + TRAIL_PCT)   # trails UP from the low
                if h[j] >= trail:
                    res = (100 * (entry / max(trail, o[j]) - 1), j - i)
                    break
            if res == "corrupt":
                i = j + 1
                continue
            if res is None:
                j = min(n - 1, i + MOM_MAX_HOLD)
                res = (100 * (entry / c[j] - 1), j - i)
            out.append({"ret": res[0], "date": d[i], "sym": sym, "bars": res[1]})
            i += max(1, res[1]) + 1
    return out


def report(name: str, trades: list[dict]) -> dict | None:
    if not trades:
        print(f"\n{name}: NO TRADES")
        return None
    a = np.array([t["ret"] for t in trades])
    bars = [t["bars"] for t in trades]
    dates = sorted(t["date"] for t in trades)
    mid = dates[len(dates) // 2]
    early = np.array([t["ret"] for t in trades if t["date"] < mid])
    late = np.array([t["ret"] for t in trades if t["date"] >= mid])
    profit = a[a > 0].sum()
    top1 = np.sort(a)[-max(1, len(a) // 100):].sum()
    r = {"n": int(a.size), "win": 100 * float((a > 0).mean()),
         "mean": float(a.mean()), "hold": int(st.median(bars)),
         "tail": 100 * float(top1 / profit) if profit > 0 else 999.0,
         "worst": float(a.min()),
         "early": float(early.mean()) if early.size else 0.0,
         "late": float(late.mean()) if late.size else 0.0}
    print(f"\n{name}")
    print(f"  n={r['n']}  win={r['win']:.1f}%  mean={r['mean']:+.2f}%  "
          f"hold={r['hold']}b  top1%={r['tail']:.0f}%  worst={r['worst']:+.1f}%")
    print(f"  halves: early {r['early']:+.2f}%  late {r['late']:+.2f}%   "
          f"({dates[0]} → {dates[-1]})")
    return r


def gates(name: str, r: dict) -> bool:
    checks = (
        (f"V0 n>={G_MIN_N}", r["n"] >= G_MIN_N, r["n"]),
        (f"G1 win>={G1_WIN}%", r["win"] >= G1_WIN, f"{r['win']:.1f}%"),
        ("G2 mean>0", r["mean"] > 0, f"{r['mean']:+.2f}%"),
        (f"G3 hold<={G3_HOLD}b", r["hold"] <= G3_HOLD, f"{r['hold']}b"),
        (f"G4 top1%<={G4_TAIL}%", r["tail"] <= G4_TAIL, f"{r['tail']:.0f}%"),
        (f"G5 worst>={G5_WORST}%", r["worst"] >= G5_WORST, f"{r['worst']:+.1f}%"),
        ("G6 both halves +", r["early"] > 0 and r["late"] > 0,
         f"{r['early']:+.2f}/{r['late']:+.2f}"),
    )
    print(f"\n  — gates: {name} —")
    for label, ok, got in checks:
        print(f"    {label:20s} {'PASS' if ok else 'FAIL'}  ({got})")
    return all(c[1] for c in checks)


def main() -> int:
    syms = sorted({s.upper() for s in universe_symbols(strict=False)})
    data = _load_many(syms, "universe")
    results = {}
    for name, fn in (("SHORT MEAN REVERSION (fade the spike)", short_mean_reversion),
                     ("SHORT MOMENTUM (ride the downtrend)", short_momentum)):
        r = report(name, fn(data))
        if r:
            results[name] = (r, gates(name, r))
    print("\n" + "=" * 66)
    for name, (r, ok) in results.items():
        print(f"  {name:42s} {'ALL PASS' if ok else 'FAILS'}")
    print("\nLong side, for comparison (same harness family):")
    print("  Swing    +0.90%/trade  71% win   worst -32.6%")
    print("  Momentum +1.53%/trade  47% win   worst -14.7%")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

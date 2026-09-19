"""SWING_OUT_OF_SAMPLE_GATES_V1 — does the rule work on names it never saw?

Gates frozen in SWING_OUT_OF_SAMPLE_GATES_V1.md before this ran.

The bar store holds 1,005 symbols; every swing study to date screened the same
244. The σ-band, the hold length and the trend floor were all chosen on those
244. This runs the SAME rule — constants imported, never restated — over the
symbols that had no vote in any of those decisions.

The trade loop is lifted from sigma_band_v1.py deliberately, line for line:
same fill-at-open slippage, same corrupt-bar guard, same non-overlap rule. If
this study and that one disagree it must be because the SYMBOLS differ, not
because the harness does.
"""
import os
import statistics as st
import sys

import numpy as np

sys.path.insert(0, ".")
from tradepro_strategies.cli.build_universe import _load  # noqa: E402
from tradepro_strategies.signals.mean_reversion import (  # noqa: E402
    BB_WINDOW as WINDOW, MAX_HOLD, SIGMA, STOP_PCT, TREND_WINDOW)
from tradepro_strategies.universe import (  # noqa: E402
    MIN_DOLLAR_VOLUME, MIN_PRICE, poison_check, universe_symbols)

MAX_DAY_MOVE = 0.35
STORE = os.path.expanduser("~/.tradepro/bar_cache/us_etf")

# In-sample result this is measured against (SWING_SIGMA_BAND_GATES_V1).
IN_SAMPLE = {"n": 1660, "win": 70.4, "mean": 0.87, "worst": -19.6}

# Gates, frozen in the doc.
G0_MIN_N, G1_MIN_MEAN, G2_MIN_WIN, G3_WORST_FLOOR = 1000, 0.40, 60.0, -25.0


def _sma(c, i, n):
    return sum(c[i - n + 1:i + 1]) / n


def _tradable(c, v) -> bool:
    """The SAME screen the live universe applies — not a new threshold.

    An edge that exists only in names too thin to fill is not an edge.
    """
    if c[-1] < MIN_PRICE:
        return False
    if v is None:
        return False
    recent = [c[k] * v[k] for k in range(max(0, len(c) - 60), len(c)) if v[k]]
    return bool(recent) and st.median(recent) >= MIN_DOLLAR_VOLUME


def _run(data: dict) -> list[dict]:
    trades = []
    for sym, (c, h, l, o, d) in sorted(data.items()):
        n = len(c)
        i = 210
        while i < n - 1:
            m = _sma(c, i, WINDOW)
            sd = st.pstdev(c[i - WINDOW + 1:i + 1])
            if not (sd > 0 and c[i] > _sma(c, i, TREND_WINDOW)):
                i += 1
                continue
            sig = (c[i] - m) / sd
            if sig > -SIGMA:                      # the LIVE entry, imported
                i += 1
                continue
            entry, stop = c[i], c[i] * (1 - STOP_PCT)
            out = None
            for j in range(i + 1, min(n, i + MAX_HOLD + 1)):
                if c[j - 1] <= 0 or abs(c[j] / c[j - 1] - 1) > MAX_DAY_MOVE:
                    out = "corrupt"
                    break
                tgt = _sma(c, j, WINDOW)
                fill_s, fill_t = min(stop, o[j]), max(tgt, o[j])
                if l[j] <= stop:
                    out = (100 * (fill_s / entry - 1), j - i)
                    break
                if h[j] >= tgt:
                    out = (100 * (fill_t / entry - 1), j - i)
                    break
            if out == "corrupt":
                i = j + 1
                continue
            if out is None:
                j = min(n - 1, i + MAX_HOLD)
                out = (100 * (c[j] / entry - 1), j - i)
            trades.append({"ret": out[0], "date": d[i], "sym": sym})
            i += max(1, out[1]) + 1
    return trades


def _load_many(syms, label):
    data, skipped = {}, 0
    for sym in syms:
        try:
            df = _load(sym)
            if df is None or len(df) < 260:
                skipped += 1
                continue
            c = df["close"].tolist()
            v = df["volume"].tolist() if "volume" in df.columns else None
            if not poison_check(c, v)[0] or not _tradable(c, v):
                skipped += 1
                continue
            data[sym] = (c, df["high"].tolist(), df["low"].tolist(),
                         df["open"].tolist(), [str(x)[:10] for x in df.index])
        except Exception:  # noqa: BLE001
            skipped += 1
    print(f"{label}: {len(data)} usable, {skipped} skipped", flush=True)
    return data


def _report(name, trades):
    if not trades:
        print(f"{name}: NO TRADES")
        return None
    a = np.array([t["ret"] for t in trades])
    dates = sorted(t["date"] for t in trades)
    mid = dates[len(dates) // 2]
    early = np.array([t["ret"] for t in trades if t["date"] < mid])
    late = np.array([t["ret"] for t in trades if t["date"] >= mid])
    r = {"n": int(a.size), "win": 100 * float((a > 0).mean()),
         "mean": float(a.mean()), "median": float(np.median(a)),
         "worst": float(a.min()),
         "early": float(early.mean()) if early.size else None,
         "late": float(late.mean()) if late.size else None,
         "span": (dates[0], dates[-1])}
    print(f"\n{name}")
    print(f"  n={r['n']}  win={r['win']:.1f}%  mean={r['mean']:+.2f}%  "
          f"median={r['median']:+.2f}%  worst={r['worst']:+.1f}%")
    print(f"  halves: early {r['early']:+.2f}%  late {r['late']:+.2f}%   "
          f"span {r['span'][0]} → {r['span'][1]}")
    return r


def main() -> int:
    traded = {s.upper() for s in universe_symbols(strict=False)}
    have = sorted(d for d in os.listdir(STORE)
                  if os.path.isdir(os.path.join(STORE, d)))
    unseen = [s for s in have if s.upper() not in traded]
    print(f"bar store {len(have)} · traded universe {len(traded)} · "
          f"NEVER SEEN BY THE RULE {len(unseen)}\n", flush=True)

    oos = _report("OUT OF SAMPLE (unseen names)", _run(_load_many(unseen, "unseen")))
    ins = _report("IN SAMPLE (the traded 244, same harness)",
                  _run(_load_many(sorted(traded), "traded")))

    if not oos:
        return 1
    print("\n— gates (frozen in SWING_OUT_OF_SAMPLE_GATES_V1.md) —")
    g0 = oos["n"] >= G0_MIN_N
    g1 = oos["mean"] >= G1_MIN_MEAN
    g2 = oos["win"] >= G2_MIN_WIN
    g3 = oos["worst"] >= G3_WORST_FLOOR
    g4 = (oos["early"] or 0) > 0 and (oos["late"] or 0) > 0
    for label, ok, got in (
            (f"G0 n>={G0_MIN_N}", g0, oos["n"]),
            (f"G1 mean>=+{G1_MIN_MEAN}%", g1, f"{oos['mean']:+.2f}%"),
            (f"G2 win>={G2_MIN_WIN}%", g2, f"{oos['win']:.1f}%"),
            (f"G3 worst>={G3_WORST_FLOOR}%", g3, f"{oos['worst']:+.1f}%"),
            ("G4 both halves +", g4,
             f"{oos['early']:+.2f}% / {oos['late']:+.2f}%")):
        print(f"  {label:22s} {'PASS' if ok else 'FAIL'}  ({got})")
    passed = all((g0, g1, g2, g3, g4))
    print(f"\nVERDICT: {'GENERALISES' if passed else 'DOES NOT GENERALISE'}")
    if ins:
        print(f"decay vs in-sample: {oos['mean'] - ins['mean']:+.2f}pp/trade "
              f"({ins['mean']:+.2f}% → {oos['mean']:+.2f}%)")
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())

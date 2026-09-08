"""Swing sigma-band study — grades SWING_SIGMA_BAND_GATES_V1.md (1a9088f).

The MARGINAL question: do the trades between -2.0 and -2.5 sigma (the ones
the rule refuses, e.g. COST at -2.27) carry their own weight? Same exits,
same trend floor, same fill-at-open honesty as mean_reversion_v2 — the only
change is bucketing entries by their sigma at signal.
"""
import statistics as st
import sys

import numpy as np

sys.path.insert(0, ".")
from tradepro_strategies.universe import universe_symbols, poison_check  # noqa: E402
from tradepro_strategies.cli.build_universe import _load  # noqa: E402
from tradepro_strategies.signals.mean_reversion import (  # noqa: E402
    BB_WINDOW as WINDOW, STOP_PCT, MAX_HOLD)

MAX_DAY_MOVE = 0.35
BANDS = [("A ≤-2.5 (rule)", -999.0, -2.5), ("B -2.5..-2.25", -2.5, -2.25),
         ("C -2.25..-2.0", -2.25, -2.0)]


def _sma(c, i, n):
    return sum(c[i - n + 1:i + 1]) / n


def main():
    syms = universe_symbols()
    print(f"loading {len(syms)} symbols...", flush=True)
    data = {}
    for sym in syms:
        try:
            df = _load(sym)
            if df is None or len(df) < 260:
                continue
            _c = df["close"].tolist()
            _v = df["volume"].tolist() if "volume" in df.columns else None
            if not poison_check(_c, _v)[0]:
                continue
            data[sym] = (df["close"].tolist(), df["high"].tolist(),
                         df["low"].tolist(), df["open"].tolist(),
                         [str(x)[:10] for x in df.index])
        except Exception:
            continue
    print(f"loaded {len(data)}", flush=True)

    rows = {name: [] for name, *_ in BANDS}
    for si, (sym, (c, h, l, o, d)) in enumerate(sorted(data.items())):
        n = len(c)
        i = 210
        while i < n - 1:
            m = _sma(c, i, WINDOW)
            sd = st.pstdev(c[i - WINDOW + 1:i + 1])
            if not (sd > 0 and c[i] > _sma(c, i, 200)):
                i += 1
                continue
            sig = (c[i] - m) / sd
            band = next((nm for nm, lo_, hi_ in BANDS if lo_ < sig <= hi_), None)
            if band is None:
                i += 1
                continue
            entry, tgt_fixed, stop = c[i], m, c[i] * (1 - STOP_PCT)
            out = None
            for j in range(i + 1, min(n, i + MAX_HOLD + 1)):
                if c[j - 1] <= 0 or abs(c[j] / c[j - 1] - 1) > MAX_DAY_MOVE:
                    out = "corrupt"
                    break
                tgt = _sma(c, j, WINDOW)
                fill_s = min(stop, o[j])
                fill_t = max(tgt, o[j])
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
            rows[band].append({"ret": out[0], "date": d[i], "sym": sym})
            i += max(1, out[1]) + 1

    print(f"\n{'band':16}{'n':>6}{'win':>7}{'mean':>8}{'median':>8}{'worst':>8}  two-split cells")
    for name, *_ in BANDS:
        tr = rows[name]
        if not tr:
            print(f"{name:16}     0"); continue
        a = np.array([t["ret"] for t in tr])
        dmed = sorted(t["date"] for t in tr)[len(tr) // 2]
        ss = sorted({t["sym"] for t in tr})
        first = set(ss[:len(ss) // 2])
        cells = []
        for tf in (lambda t: t["date"] < dmed, lambda t: t["date"] >= dmed):
            for sf in (lambda t: t["sym"] in first, lambda t: t["sym"] not in first):
                xs = [t["ret"] for t in tr if tf(t) and sf(t)]
                cells.append(float(np.mean(xs)) if xs else float("nan"))
        print(f"{name:16}{len(a):>6}{100*np.mean(a>0):>6.1f}%{np.mean(a):>+7.2f}%"
              f"{np.median(a):>+7.2f}%{a.min():>+7.1f}%  "
              + " ".join(f"{x:+.2f}" for x in cells), flush=True)


if __name__ == "__main__":
    main()

"""REGIME_CONDITIONING_GATES_V1 — where does the edge actually live?

Gates and a prediction frozen in REGIME_CONDITIONING_GATES_V1.md before this
ran.

DESCRIPTIVE. It tags every trade the gated rules produce with the market state
at ENTRY and reports performance per state. Nothing is filtered and no rule is
changed. See the gates doc for why the live sizing rules in the proposal that
prompted this were refused.

NO LOOK-AHEAD, and this is the part most such studies get wrong: regime labels
use an EXPANDING window. A tercile computed over the full sample labels 2008
"high vol" using 2026 data, which is a time machine wearing a statistic's
clothes.
"""
import statistics as st
import sys
from collections import defaultdict

import numpy as np

sys.path.insert(0, ".")
from backtests.studies.swing_out_of_sample_v1 import _load_many, _sma  # noqa: E402
from tradepro_strategies.signals.mean_reversion import (  # noqa: E402
    BB_WINDOW as W, MAX_HOLD, SIGMA, STOP_PCT, TREND_WINDOW)
from tradepro_strategies.universe import universe_symbols  # noqa: E402

MAX_DAY_MOVE = 0.35
MIN_CELL_N = 500          # V0/G3: below this a cell is reported, not discussed
TRAILING_YEARS = 2


def _true_range(h, l, c, i):
    return max(h[i] - l[i], abs(h[i] - c[i - 1]), abs(l[i] - c[i - 1]))


def adx(h, l, c, i, n=14):
    """Wilder's ADX at bar i, computed from the preceding 2n bars only."""
    if i < 2 * n + 1:
        return None
    plus_dm, minus_dm, tr = [], [], []
    for k in range(i - 2 * n + 1, i + 1):
        up, dn = h[k] - h[k - 1], l[k - 1] - l[k]
        plus_dm.append(up if (up > dn and up > 0) else 0.0)
        minus_dm.append(dn if (dn > up and dn > 0) else 0.0)
        tr.append(_true_range(h, l, c, k))
    atr = st.fmean(tr[-n:])
    if atr <= 0:
        return None
    pdi = 100 * st.fmean(plus_dm[-n:]) / atr
    mdi = 100 * st.fmean(minus_dm[-n:]) / atr
    if pdi + mdi == 0:
        return None
    return 100 * abs(pdi - mdi) / (pdi + mdi)


def build_regimes(spy):
    """(date -> (vol_level, trend_state)) for SPY, expanding window only."""
    c, h, l, o, d = spy
    n = len(c)
    rv = [None] * n
    for i in range(21, n):
        rets = [c[k] / c[k - 1] - 1 for k in range(i - 19, i + 1) if c[k - 1] > 0]
        if len(rets) == 20:
            rv[i] = st.pstdev(rets) * (252 ** 0.5)

    out = {}
    look = 252 * TRAILING_YEARS
    for i in range(n):
        if rv[i] is None or i < look:
            continue
        # TRAILING distribution — never the whole sample.
        hist = sorted(x for x in rv[i - look:i] if x is not None)
        if len(hist) < look // 2:
            continue
        lo, hi = hist[len(hist) // 3], hist[2 * len(hist) // 3]
        vol = "low" if rv[i] <= lo else ("high" if rv[i] >= hi else "med")
        a = adx(h, l, c, i)
        if a is None:
            continue
        trend = "range" if a < 20 else ("trend" if a >= 25 else "neutral")
        out[d[i]] = (vol, trend)
    return out


def swing_trades(data):
    """Every trade the LIVE swing rule produces, tagged with its entry date."""
    out = []
    for sym, (c, h, l, o, d) in sorted(data.items()):
        n = len(c)
        i = 210
        while i < n - 1:
            m = _sma(c, i, W)
            sd = st.pstdev(c[i - W + 1:i + 1])
            if not (sd > 0 and c[i] > _sma(c, i, TREND_WINDOW)):
                i += 1
                continue
            if (c[i] - m) / sd > -SIGMA:
                i += 1
                continue
            entry, stop = c[i], c[i] * (1 - STOP_PCT)
            res = None
            for j in range(i + 1, min(n, i + MAX_HOLD + 1)):
                if c[j - 1] <= 0 or abs(c[j] / c[j - 1] - 1) > MAX_DAY_MOVE:
                    res = "corrupt"
                    break
                tgt = _sma(c, j, W)
                if l[j] <= stop:
                    res = (100 * (min(stop, o[j]) / entry - 1), j - i)
                    break
                if h[j] >= tgt:
                    res = (100 * (max(tgt, o[j]) / entry - 1), j - i)
                    break
            if res == "corrupt":
                i = j + 1
                continue
            if res is None:
                j = min(n - 1, i + MAX_HOLD)
                res = (100 * (c[j] / entry - 1), j - i)
            out.append({"ret": res[0], "date": d[i], "sym": sym})
            i += max(1, res[1]) + 1
    return out


def report(name, trades, regimes):
    tagged = [(t, regimes[t["date"]]) for t in trades if t["date"] in regimes]
    print(f"\n{'='*72}\n{name}: {len(tagged)} of {len(trades)} trades fall in a "
          f"labelled session\n{'='*72}")
    cells = defaultdict(list)
    for t, (vol, trend) in tagged:
        cells[(vol, trend)].append(t)

    pooled = np.array([t["ret"] for t, _ in tagged])
    se = pooled.std(ddof=1) / (len(pooled) ** 0.5) if len(pooled) > 1 else 0.0
    print(f"pooled: n={len(pooled)} mean={pooled.mean():+.2f}% "
          f"win={100*(pooled>0).mean():.1f}%  (1 s.e. = {se:.3f}%)\n")

    print(f"{'vol':>6}{'trend':>9}{'n':>8}{'win%':>8}{'mean%':>9}{'worst%':>9}"
          f"{'top sym':>10}")
    qualifying = []
    for vol in ("low", "med", "high"):
        for trend in ("range", "neutral", "trend"):
            g = cells.get((vol, trend), [])
            if not g:
                print(f"{vol:>6}{trend:>9}{0:>8}{'—':>8}{'—':>9}{'—':>9}{'—':>10}")
                continue
            a = np.array([t["ret"] for t in g])
            syms = defaultdict(int)
            for t in g:
                syms[t["sym"]] += 1
            top = max(syms.values()) / len(g)
            thin = "" if len(g) >= MIN_CELL_N else "  (thin — not discussed)"
            print(f"{vol:>6}{trend:>9}{len(a):>8}{100*(a>0).mean():>7.1f}%"
                  f"{a.mean():>+9.2f}{a.min():>9.1f}{100*top:>9.0f}%{thin}")
            if len(g) >= MIN_CELL_N:
                qualifying.append(((vol, trend), a, top))
    return tagged, qualifying, se


def grade(name, tagged, qualifying, se, regimes):
    print(f"\n— gates ({name}) —")
    v0 = len(qualifying) >= 6
    print(f"  V0 >=6 cells with n>=500        {'PASS' if v0 else 'FAIL'}  "
          f"({len(qualifying)} of 9)")
    if not qualifying:
        print("  nothing qualifies — no further grading, per the pre-stated rule")
        return
    means = [(k, a.mean()) for k, a, _ in qualifying]
    best, worst = max(means, key=lambda x: x[1]), min(means, key=lambda x: x[1])
    spread = best[1] - worst[1]
    g1 = spread > 2 * se
    print(f"  G1 spread > 2 s.e.              {'PASS' if g1 else 'FAIL'}  "
          f"({spread:.2f}% vs {2*se:.2f}%; best {best[0]} {best[1]:+.2f}%, "
          f"worst {worst[0]} {worst[1]:+.2f}%)")

    dates = sorted({t["date"] for t, _ in tagged})
    mid = dates[len(dates) // 2]
    order_ok = True
    for half, sel in (("early", lambda dt: dt < mid), ("late", lambda dt: dt >= mid)):
        hb = [t["ret"] for t, k in tagged if k == best[0] and sel(t["date"])]
        hw = [t["ret"] for t, k in tagged if k == worst[0] and sel(t["date"])]
        if len(hb) < 50 or len(hw) < 50:
            print(f"  G2 {half}: too few trades ({len(hb)}/{len(hw)}) to judge")
            order_ok = False
            continue
        ok = st.fmean(hb) > st.fmean(hw)
        order_ok &= ok
        print(f"  G2 {half}: best {st.fmean(hb):+.2f}% vs worst {st.fmean(hw):+.2f}% "
              f"{'holds' if ok else 'INVERTS'}")
    print(f"  G2 ordering holds in both halves {'PASS' if order_ok else 'FAIL'}")

    conc = max(top for _, _, top in qualifying)
    g3 = conc <= 0.10
    print(f"  G3 no symbol >10% of a cell     {'PASS' if g3 else 'FAIL'}  "
          f"(worst {100*conc:.0f}%)")
    print(f"\n  VERDICT: {'ALL PASS' if (v0 and g1 and order_ok and g3) else 'FAILS'}")


def main() -> int:
    syms = sorted({s.upper() for s in universe_symbols(strict=False)})
    data = _load_many(syms + ["SPY"], "regime")
    if "SPY" not in data:
        print("SPY not in the bar store — cannot label regimes. STOPPING.")
        return 2
    regimes = build_regimes(data["SPY"])
    print(f"labelled {len(regimes)} sessions from SPY "
          f"({min(regimes)} → {max(regimes)})")
    sw = swing_trades({k: v for k, v in data.items() if k != "SPY"})
    tagged, qual, se = report("SWING (mean reversion)", sw, regimes)
    grade("SWING", tagged, qual, se, regimes)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

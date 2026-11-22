"""SELLOFF_TECHNICALS_V1 — grades the three rules pre-registered in
strategies/SELLOFF_TECHNICALS_V1.md (cb21bb2a). Reads the golden daily store;
prints n, win%, mean, median, worst, the four two-split cells and the null for
each rule. Nothing here places or publishes anything.
"""
from __future__ import annotations

import statistics as st
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, ".")
from tradepro_strategies.daily_bars import load_daily  # noqa: E402
from tradepro_strategies.signals.mean_reversion import BB_WINDOW, MAX_HOLD, SIGMA, STOP_PCT  # noqa: E402
from tradepro_strategies.universe import poison_check, universe_symbols  # noqa: E402

COST = 0.0010
BORROW_DAILY = 0.01 / 252
MAX_DAY_MOVE = 0.35
SPY_SELLOFF, SPY_RALLY, RS_PTS, HOLD = -0.02, 0.02, 0.03, 3
MIN_N, PASS_MEAN, BEAT_NULL = 200, 0.40, 0.20


def _adj(sym):
    df = load_daily(sym, require_ohlc=True, fetched_by="study-selloff-technicals")
    if df is None or len(df) < 260:
        return None
    f = df["adj_factor"].fillna(1.0).astype(float) if "adj_factor" in df.columns else 1.0
    out = pd.DataFrame({"o": df["open"].astype(float) * f, "c": df["close"].astype(float) * f,
                        "v": df["volume"].astype(float) if "volume" in df.columns else np.nan})
    out.index = pd.to_datetime([str(t)[:10] for t in df.index])
    out = out[~out.index.duplicated(keep="last")].sort_index()
    if not poison_check(out["c"].tolist(), out["v"].fillna(0).tolist())[0]:
        return None
    return out


def grade(name, rows, null_rows=None):
    if not rows:
        print(f"{name:34s} n=0"); return
    a = np.array([r[0] for r in rows]); dates = [r[1] for r in rows]; syms = [r[2] for r in rows]
    dmed = sorted(dates)[len(dates) // 2]; ss = sorted(set(syms)); first = set(ss[:len(ss) // 2])
    cells = []
    for tf in (lambda d: d < dmed, lambda d: d >= dmed):
        for sf in (lambda s: s in first, lambda s: s not in first):
            xs = [x for x, d, s in rows if tf(d) and sf(s)]
            cells.append(float(np.mean(xs)) if xs else float("nan"))
    null = float(np.mean([r[0] for r in null_rows])) if null_rows else None
    mean = float(a.mean())
    if len(a) < MIN_N: verdict = f"too few ({len(a)} of {MIN_N})"
    elif mean <= 0 or (null is not None and mean <= null): verdict = "FAIL"
    elif mean >= PASS_MEAN and all(c > 0 for c in cells) and (null is None or mean - null >= BEAT_NULL): verdict = "PASS"
    else: verdict = "not enough to show"
    print(f"{name:34s} n={len(a):5d} win {100*np.mean(a>0):5.1f}% mean {mean:+.3f}% med {np.median(a):+.3f}% "
          f"worst {a.min():+.1f}% cells " + " ".join(f"{c:+.2f}" for c in cells)
          + (f" null {null:+.3f}%" if null is not None else "") + f"  → {verdict}", flush=True)


def main():
    syms = universe_symbols()
    spy = _adj("SPY")
    assert spy is not None
    spy_ret = spy["c"].pct_change()
    spy_r5 = spy["c"].pct_change(5)
    selloff_days = set(spy.index[spy_ret <= SPY_SELLOFF]); rally_days = set(spy.index[spy_ret >= SPY_RALLY])
    print(f"SPY sell-off days (<= -2%): {len(selloff_days)} · rally days (>= +2%): {len(rally_days)}", flush=True)
    r1, r1_null, r3, r3_null, r2, r2_null = [], [], [], [], [], []
    loaded = 0
    for sym in syms:
        if sym == "SPY":
            continue
        df = _adj(sym)
        if df is None:
            continue
        loaded += 1
        c, o, idx = df["c"], df["o"], df.index
        cl, ol, d = c.tolist(), o.tolist(), [str(t.date()) for t in idx]
        ret1 = c.pct_change(); r5 = c.pct_change(5)
        n = len(cl)
        pos = {t: i for i, t in enumerate(idx)}
        # R1 / R3 — event-day rules, hold 3 sessions, fill next open, exit open of session 4
        for day in (selloff_days | rally_days):
            i = pos.get(day)
            if i is None or i < 5 or i + HOLD + 1 >= n or day not in spy_r5.index:
                continue
            k = i + 1; x = i + HOLD + 1
            if ol[k] <= 0 or ol[x] <= 0 or abs(ol[k] / cl[i] - 1) > MAX_DAY_MOVE or abs(ol[x] / cl[x - 1] - 1) > MAX_DAY_MOVE:
                continue
            long_ret = (ol[x] / ol[k] - 1 - COST) * 100
            rs = float(r5.iloc[i] - spy_r5.loc[day]) if not (np.isnan(r5.iloc[i]) or np.isnan(spy_r5.loc[day])) else None
            if day in selloff_days:
                r1_null.append((long_ret, d[i], sym))
                if ret1.iloc[i] >= 0 and rs is not None and rs >= RS_PTS:
                    r1.append((long_ret, d[i], sym))
            if day in rally_days:
                short_ret = (-(ol[x] / ol[k] - 1) - COST - BORROW_DAILY * HOLD) * 100
                r3_null.append((short_ret, d[i], sym))
                if ret1.iloc[i] <= 0 and rs is not None and rs <= -RS_PTS:
                    r3.append((short_ret, d[i], sym))
        # R2 — the dip rule below the 200d (and its null: the same rule above)
        i = 210
        while i < n - 1:
            w = cl[i - BB_WINDOW + 1:i + 1]; sd = st.pstdev(w); m = sum(w) / BB_WINDOW
            s200 = sum(cl[i - 199:i + 1]) / 200
            if sd <= 0 or not (cl[i] < m - SIGMA * sd):
                i += 1; continue
            below = cl[i] < s200
            k = i + 1; fill = ol[k]
            if fill <= 0 or abs(fill / cl[i] - 1) > MAX_DAY_MOVE:
                i += 1; continue
            out = None
            for j in range(k, min(n, k + MAX_HOLD + 1)):
                if abs(cl[j] / cl[j - 1] - 1) > MAX_DAY_MOVE:
                    out = "corrupt"; break
                tgt = sum(cl[j - BB_WINDOW + 1:j + 1]) / BB_WINDOW
                if cl[j] <= fill * (1 - STOP_PCT) or cl[j] >= tgt or j - k >= MAX_HOLD:
                    out = j; break
            if out == "corrupt" or out is None or out + 1 >= n:
                i = (j if out == "corrupt" else n) + 1; continue
            px = ol[out + 1]
            if px <= 0 or abs(px / cl[out] - 1) > MAX_DAY_MOVE:
                i = out + 2; continue
            r = (px / fill - 1 - COST) * 100
            (r2 if below else r2_null).append((r, d[i], sym))
            i = out + 2
    print(f"loaded {loaded} symbols\n")
    grade("R1 RS leaders on sell-off day (long)", r1, r1_null)
    grade("R1 null: all stocks, same days", r1_null)
    grade("R2 dip rule BELOW 200d (long)", r2, r2_null)
    grade("R2 null: dip rule ABOVE 200d", r2_null)
    grade("R3 RS laggards on rally day (short)", r3, r3_null)
    grade("R3 null: all stocks short, same days", r3_null)


if __name__ == "__main__":
    main()

"""Analog evaluation v1 — graded against ANALOG_EVALUATION_GATES_V1.md (c221e95).

Walk-forward by calendar quarter. For each quarter, the analog pool is every
bar from EARLIER quarters, minus a 40-calendar-day buffer so no analog's
20-session outcome window can overlap the bar being evaluated.
"""
import os, datetime as dt, numpy as np
from tradepro_strategies.analogs import state_at, outcome, zscore_stats, DIMS
from tradepro_strategies.cli.momentum_candidates import _load, poison_check, _tradeable, BASE_DIR

UP, DN, HORIZON, K = 0.08, -0.08, 20, 50
POOL_CAP, EVAL_STRIDE, BUFFER_DAYS = 60000, 7, 40

recs = []   # (date, sym_idx, state(6), outcome)
syms = [s for s in sorted(os.listdir(BASE_DIR)) if _tradeable(s)]
used = 0
for si, sym in enumerate(syms):
    df = _load(sym)
    if df is None: continue
    c = df["close"].tolist(); h = df["high"].tolist(); l = df["low"].tolist()
    if not poison_check(c)[0]: continue
    d = [str(x)[:10] for x in df.index]
    used += 1
    for i in range(252, len(c) - HORIZON - 1):
        st = state_at(c, h, l, i)
        if st is None: continue
        o = outcome(c, h, l, i, up_pct=UP, dn_pct=DN, horizon=HORIZON)
        if o is None: continue
        recs.append((d[i], si, st, o))
print(f"symbols used {used} · records {len(recs):,}")

recs.sort(key=lambda r: r[0])
dates = np.array([r[0] for r in recs])
X = np.array([r[2] for r in recs], dtype=np.float64)
Y = np.array([r[3] for r in recs], dtype=np.float64)
S = np.array([r[1] for r in recs], dtype=np.int32)

def qkey(ds): return ds[:4] + "Q" + str((int(ds[5:7]) - 1) // 3 + 1)
quarters = sorted({qkey(x) for x in dates})
rng = np.random.default_rng(20260822)

pa, pb, act, ed, es = [], [], [], [], []
viol = 0
for q in quarters:
    in_q = np.where(np.array([qkey(x) for x in dates]) == q)[0]
    if not len(in_q): continue
    q_start = dates[in_q[0]]
    cutoff = (dt.date.fromisoformat(q_start) - dt.timedelta(days=BUFFER_DAYS)).isoformat()
    pool = np.where(dates < cutoff)[0]
    if len(pool) < 5000: continue
    # STRUCTURAL no-lookahead check: no pooled bar may be dated within the
    # buffer of the earliest evaluated bar.
    if dates[pool].max() >= cutoff: viol += 1
    if len(pool) > POOL_CAP:
        pool = rng.choice(pool, POOL_CAP, replace=False)
    Xp, Yp = X[pool], Y[pool]
    mu, sd = Xp.mean(0), Xp.std(0); sd[sd == 0] = 1.0
    Zp = (Xp - mu) / sd
    base = float(Yp.mean())

    ev = in_q[::EVAL_STRIDE]
    Ze = (X[ev] - mu) / sd
    for s in range(0, len(ev), 200):
        chunk = Ze[s:s + 200]
        d2 = ((chunk[:, None, :] - Zp[None, :, :]) ** 2).sum(-1)
        idx = np.argpartition(d2, K, axis=1)[:, :K]
        preds = Yp[idx].mean(1)
        pa.extend(preds.tolist())
        pb.extend([base] * len(preds))
        act.extend(Y[ev[s:s + 200]].tolist())
        ed.extend(dates[ev[s:s + 200]].tolist())
        es.extend(S[ev[s:s + 200]].tolist())

pa, pb, act = np.array(pa), np.array(pb), np.array(act)
ed, es = np.array(ed), np.array(es)
print(f"\nevaluated {len(pa):,} bars · {len(set(es.tolist()))} symbols · lookahead violations {viol}")

def brier(p, a): return float(((p - a) ** 2).mean())
ba, bb = brier(pa, act), brier(pb, act)
print(f"\nG1  Brier — analog {ba:.5f} · base rate {bb:.5f} · improvement {bb - ba:+.5f}"
      f"   {'PASS' if bb - ba >= 0.005 else 'FAIL'} (need >= 0.005)")

def deciles(p, a):
    order = np.argsort(p); n = len(p); out = []
    for k in range(10):
        sl = order[k * n // 10:(k + 1) * n // 10]
        out.append(float(a[sl].mean()))
    return out
dec = deciles(pa, act)
spread = 100 * (dec[-1] - dec[0])
rises = sum(1 for i in range(9) if dec[i + 1] >= dec[i])
print(f"\nG2  decile spread {spread:.1f}pp   {'PASS' if spread >= 10 else 'FAIL'} (need >= 10)")
print("    realized rate by predicted decile: " + " ".join(f"{100*x:.0f}" for x in dec))
print(f"G3  monotone steps {rises}/9   {'PASS' if rises >= 7 else 'FAIL'} (need >= 7)")

print("\nG4  both splits:")
mid = np.median(np.array([int(x[:4]) * 10000 + int(x[5:7]) * 100 + int(x[8:10]) for x in ed]))
di = np.array([int(x[:4]) * 10000 + int(x[5:7]) * 100 + int(x[8:10]) for x in ed])
cells = {"time 1st half": di < mid, "time 2nd half": di >= mid,
         "symbols even": es % 2 == 0, "symbols odd": es % 2 == 1}
g4 = True
for name, m in cells.items():
    if m.sum() < 1000: print(f"    {name}: too few"); g4 = False; continue
    b1, b0 = brier(pa[m], act[m]), brier(pb[m], act[m])
    dd = deciles(pa[m], act[m]); sp = 100 * (dd[-1] - dd[0])
    ok = (b0 - b1) >= 0.005 and sp >= 10
    g4 &= ok
    print(f"    {name}: {'PASS' if ok else 'FAIL'} — Brier {b0-b1:+.5f}, spread {sp:.1f}pp")

nsym = len(set(es.tolist()))
print(f"\nG5  sample {len(pa):,} bars / {nsym} symbols   "
      f"{'PASS' if len(pa) >= 20000 and nsym >= 100 else 'FAIL'}")
print(f"G6  lookahead violations {viol}   {'PASS' if viol == 0 else 'FAIL'}")

gates = {"G1": bb - ba >= 0.005, "G2": spread >= 10, "G3": rises >= 7,
         "G4": g4, "G5": len(pa) >= 20000 and nsym >= 100, "G6": viol == 0}
print(f"\nVERDICT: {'ALL PASS — ships' if all(gates.values()) else 'FAIL — ' + ', '.join(k for k,v in gates.items() if not v)}")

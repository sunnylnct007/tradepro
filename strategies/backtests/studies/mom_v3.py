"""Momentum v3 — graded against MOMENTUM_GATES_V3.md (committed d147326)."""
import os, statistics as st, json
from tradepro_strategies.cli.momentum_candidates import (
    _tradeable, poison_check, _load, sma, _entry_signal, STOP_PCT, TRAIL_PCT, MAX_HOLD, BASE_DIR)

VARIANTS = {"A control": None, "B >=0.7x": 0.7, "C >=1.0x": 1.0, "D >=1.2x": 1.2}
trades = []   # (vol_ratio, pct, bars, entry_date, sym, sym_idx)
sessions = set()

syms = [s for s in sorted(os.listdir(BASE_DIR)) if _tradeable(s)]
kept = 0
for sidx, sym in enumerate(syms):
    df = _load(sym)
    if df is None or "volume" not in df.columns: continue
    c = df["close"].tolist(); h = df["high"].tolist(); l = df["low"].tolist()
    v = df["volume"].tolist(); d = [str(x)[:10] for x in df.index]
    if not poison_check(c)[0]: continue
    kept += 1
    sessions.update(d[210:])
    n = len(c); i = 210
    while i < n - 1:
        if not _entry_signal(c, h, l, i):
            i += 1; continue
        av = sum(v[i-20:i]) / 20
        if not av:          # no usable volume history -> excluded from ALL
            i += 1; continue
        vr = v[i] / av
        entry = c[i]; peak = entry; j = i + 1; exit_i = None; bad = False
        while j <= min(n - 1, i + MAX_HOLD):
            if c[j-1] > 0 and abs(c[j]/c[j-1]-1) > 0.35: bad = True; break
            if c[j] <= entry*(1-STOP_PCT): exit_i = j; break
            peak = max(peak, c[j])
            if c[j] <= peak*(1-TRAIL_PCT): exit_i = j; break
            j += 1
        if bad: i = j + 1; continue
        if exit_i is None:
            if j > i + MAX_HOLD: exit_i = min(n-1, i+MAX_HOLD)
            else: break
        trades.append((vr, 100*(c[exit_i]/entry-1), exit_i-i, d[i], sym, sidx))
        i = exit_i + 1

def stats(ts):
    if not ts: return None
    p = [t[1] for t in ts]
    return {"n": len(ts), "win": 100*sum(1 for x in p if x>0)/len(p),
            "mean": st.mean(p), "median": st.median(p), "worst": min(p),
            "hold": st.median([t[2] for t in ts])}

def sel(thr): return [t for t in trades if thr is None or t[0] >= thr]

dates = sorted({t[3] for t in trades})
mid = dates[len(dates)//2]
n_sessions = len(sessions)

print(f"symbols {kept} · sessions {n_sessions} · total entries {len(trades)} · time split at {mid}\n")
print(f"{'variant':<11}{'trades':>7}{'win%':>7}{'mean%':>8}{'median%':>9}{'worst%':>8}{'hold':>6}{'/session':>10}")
res = {}
for name, thr in VARIANTS.items():
    s = stats(sel(thr)); res[name] = s
    print(f"{name:<11}{s['n']:>7}{s['win']:>6.1f}%{s['mean']:>7.2f}%{s['median']:>8.2f}%"
          f"{s['worst']:>7.1f}%{s['hold']:>6.0f}{s['n']/n_sessions:>10.2f}")

print("\nG7 — does it beat the control in ALL FOUR split cells?")
ctl = sel(None)
cells = {"time: 1st half": lambda t: t[3] < mid, "time: 2nd half": lambda t: t[3] >= mid,
         "symbols: even": lambda t: t[5] % 2 == 0, "symbols: odd": lambda t: t[5] % 2 == 1}
g7 = {}
for name, thr in VARIANTS.items():
    if thr is None: continue
    ok = True; line = []
    for cn, f in cells.items():
        a, b = stats([t for t in sel(thr) if f(t)]), stats([t for t in ctl if f(t)])
        if not a or not b: ok = False; line.append(f"{cn}: n/a"); continue
        good = a["mean"] > b["mean"] and a["win"] > b["win"]
        ok &= good
        line.append(f"{cn}: {'PASS' if good else 'FAIL'} ({a['mean']:+.2f}% vs {b['mean']:+.2f}%, "
                    f"{a['win']:.1f}% vs {b['win']:.1f}%)")
    g7[name] = ok
    print(f"  {name}: {'PASS' if ok else 'FAIL'}")
    for x in line: print(f"      {x}")

print("\nGATES")
G = [("G1 trades >=1500", lambda s,n: s["n"]>=1500), ("G2 win >=50.0%", lambda s,n: s["win"]>=50.0),
     ("G3 mean >=+2.00%", lambda s,n: s["mean"]>=2.0), ("G4 median >0.00%", lambda s,n: s["median"]>0),
     ("G5 worst >=-25.0%", lambda s,n: s["worst"]>=-25.0), ("G6 hold <=40", lambda s,n: s["hold"]<=40),
     ("G7 both splits", lambda s,n: g7.get(n, False)),
     ("G8 >=0.5/session", lambda s,n: s["n"]/n_sessions>=0.5)]
for name, thr in VARIANTS.items():
    if thr is None: continue
    s = res[name]; fails = [g for g,f in G if not f(s,name)]
    print(f"  {name}: {'ALL PASS' if not fails else 'FAIL — ' + ', '.join(fails)}")

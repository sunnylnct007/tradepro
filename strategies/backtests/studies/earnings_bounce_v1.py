"""Does an earnings drop bounce better than an ordinary one? EARNINGS_BOUNCE_GATES_V1.md (41042bb).

Owner's hypothesis: "earnings calendar provides a good opportunity — fundamentally
good stocks tend to bounce back."

COVERAGE LIMIT, carried with every number: earnings history is effectively
post-2020 (7 of 5,062 events precede it; 1 symbol of 205). The time split is two
halves of ONE regime. A pass here is weaker than a pass on the price-only studies.
"""
from __future__ import annotations
import glob, json, os, sys
import numpy as np
import pandas as pd

sys.path.insert(0, "/Users/skumar/sourcecode/tradepro/tradepro/strategies")
from tradepro_strategies.signals.mean_reversion import (
    entry_signal, BB_WINDOW, STOP_PCT, MAX_HOLD, MIN_BARS, TREND_WINDOW)
from tradepro_strategies.universe import universe_symbols

BASE = os.path.expanduser("~/.tradepro/bar_cache/us_etf")
EARN = json.load(open(os.path.expanduser("~/.tradepro/research/earnings_history.json")))
NEAR_SESSIONS = 3          # an event in the 3 sessions up to and including the signal
DROP_PCT = 0.05            # Q2's looser earnings-drop entry

def sma(c, i, n): return sum(c[i - n + 1:i + 1]) / n

def walk(c, h, l, o, i):
    """Same exit machinery as the graded harness: fill prices, not trigger prices."""
    entry = c[i]; stop = entry * (1 - STOP_PCT)
    for j in range(i + 1, min(len(c), i + MAX_HOLD + 1)):
        tgt = sma(c, j, BB_WINDOW)
        hs, ht = l[j] <= stop, h[j] >= tgt
        if hs and ht: return 100 * (min(stop, o[j]) / entry - 1), j - i
        if ht:        return 100 * (max(tgt, o[j]) / entry - 1), j - i
        if hs:        return 100 * (min(stop, o[j]) / entry - 1), j - i
    j = min(len(c) - 1, i + MAX_HOLD)
    return 100 * (c[j] / entry - 1), j - i

rule_earn, rule_rest, q2 = [], [], []
for sym in universe_symbols():
    fs = sorted(glob.glob(f"{BASE}/{sym}/1d/*.parquet"))
    if not fs: continue
    try: df = pd.concat([pd.read_parquet(f) for f in fs]).sort_index()
    except Exception: continue
    df = df[~df.index.duplicated(keep="last")]
    if len(df) < MIN_BARS + 20: continue
    c=df["close"].tolist(); h=df["high"].tolist(); l=df["low"].tolist()
    o=df["open"].tolist(); d=[str(x)[:10] for x in df.index]
    edates = set(EARN.get(sym) or [])
    if not edates: continue
    idx = {dt: k for k, dt in enumerate(d)}
    ebars = sorted(idx[x] for x in edates if x in idx)
    eset = set()
    for b in ebars:                      # event bar + the NEAR window after it
        for k in range(b, min(len(d), b + NEAR_SESSIONS)): eset.add(k)

    # Q1 — the live rule, partitioned
    i = MIN_BARS
    while i < len(c) - 1:
        if not entry_signal(c, i): i += 1; continue
        r, held = walk(c, h, l, o, i)
        (rule_earn if i in eset else rule_rest).append((sym, d[i], r))
        i += max(1, held) + 1

    # Q2 — earnings drop only, no sigma requirement
    i = MIN_BARS
    while i < len(c) - 1:
        if i in eset and c[i-1] > 0 and (c[i]/c[i-1] - 1) <= -DROP_PCT and c[i] > sma(c, i, TREND_WINDOW):
            r, held = walk(c, h, l, o, i)
            q2.append((sym, d[i], r)); i += max(1, held) + 1
        else:
            i += 1

def stat(rs):
    if not rs: return None
    v = np.array([x[2] for x in rs])
    return {"n": len(v), "win": 100*(v>0).mean(), "mean": v.mean(), "worst": v.min(), "total": v.sum()}

def show(lbl, s):
    if not s: print(f"  {lbl:<22} n=0"); return
    print(f"  {lbl:<22} n={s['n']:>5}  win {s['win']:>5.1f}%  mean {s['mean']:>+6.2f}%  "
          f"worst {s['worst']:>+6.1f}%  total {s['total']:>7.0f}%")

print(f"symbols with earnings dates: {sum(1 for v in EARN.values() if v)}\n")
print("Q1 — the LIVE RULE partitioned by earnings proximity")
se, sr = stat(rule_earn), stat(rule_rest)
show("earnings-driven", se); show("ordinary (no event)", sr)
if se and sr:
    print(f"\n  E3: earnings arm beats ordinary by {se['mean']-sr['mean']:+.2f}pt (needs >= +0.20)")

print("\nQ2 — earnings drop >= 5% above the 200-SMA, no sigma requirement")
show("earnings-drop entry", stat(q2))

# TWO-SPLIT on Q1's earnings arm
print("\nE4 two-split — earnings arm mean/trade vs the ordinary arm, in each cell")
if rule_earn and rule_rest:
    alld = sorted(x[1] for x in rule_earn)
    tmid = alld[len(alld)//2]
    syms = sorted({x[0] for x in rule_earn})
    shalf = set(syms[:len(syms)//2])
    print(f"  time split at {tmid} · symbol split {len(shalf)}/{len(syms)-len(shalf)} names")
    cells = 0
    for tl, tf in (("early", lambda x: x[1] < tmid), ("late ", lambda x: x[1] >= tmid)):
        for sl, sf in (("setA", lambda x: x[0] in shalf), ("setB", lambda x: x[0] not in shalf)):
            e = [x for x in rule_earn if tf(x) and sf(x)]
            r = [x for x in rule_rest if tf(x) and sf(x)]
            if not e or not r: print(f"    {tl} {sl}: insufficient"); continue
            diff = np.mean([x[2] for x in e]) - np.mean([x[2] for x in r])
            ok = diff > 0; cells += ok
            print(f"    {tl} {sl}: {diff:+6.2f}pt  (n={len(e):>4} vs {len(r):>4})  {'+' if ok else '-'}")
    print(f"  cells positive: {cells}/4  — E4 needs 4/4")

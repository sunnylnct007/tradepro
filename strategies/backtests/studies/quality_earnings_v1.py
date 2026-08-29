"""Do QUALITY stocks that drop on results recover better? QUALITY_EARNINGS_GATES_V1.md (e298f38).

Quality is POINT-IN-TIME: profitable, growing, P/E in [5,60] from the most
recently REPORTED annual EPS as of the signal bar. Today's P/E on a 2023 event
is look-ahead and would manufacture an edge from nothing.

The null is the SAME STOCK on a random non-earnings day above its 200-SMA, so
"quality stocks went up" sits on BOTH sides and largely cancels. The question is
whether the DROP adds anything within a quality name.

Coverage: ~2023-2026, one post-COVID bull regime. A pass is weak; a FAILURE is
strong, because these are the friendliest conditions the hypothesis will get.
"""
import glob, json, os, sys
import numpy as np, pandas as pd
sys.path.insert(0, "/Users/skumar/sourcecode/tradepro/tradepro/strategies")
from tradepro_strategies.signals.mean_reversion import TREND_WINDOW, MIN_BARS
from tradepro_strategies.universe import universe_symbols

BASE = os.path.expanduser("~/.tradepro/bar_cache/us_etf")
R = os.path.expanduser("~/.tradepro/research")
EARN = json.load(open(f"{R}/earnings_history.json"))
FUND = json.load(open(f"{R}/fundamentals.json"))
HZ = [20, 40, 60, 120]
DROP = 0.05


def sma(c, i, n): return sum(c[i - n + 1:i + 1]) / n


def eps_asof(sym, day):
    """(trailing, prior) EPS most recently REPORTED on or before `day`.

    A fiscal period END is not a publication date -- results appear weeks later,
    so a 90-day lag is applied. Without it the study reads a figure the market
    could not yet have seen, which is the exact bias this guards against.
    """
    ann = (FUND.get(sym) or {}).get("annual_eps") or {}
    usable = sorted((k, v) for k, v in ann.items() if v is not None)
    if len(usable) < 2: return None, None
    import datetime as dt
    d0 = dt.date.fromisoformat(day)
    avail = [(k, v) for k, v in usable if (d0 - dt.date.fromisoformat(k)).days >= 90]
    if len(avail) < 2: return None, None
    return avail[-1][1], avail[-2][1]


qual = {h: [] for h in HZ}; nonq = {h: [] for h in HZ}; null_q = {h: [] for h in HZ}
meta_q = []
rng = np.random.default_rng(11)
for sym in universe_symbols():
    fs = sorted(glob.glob(f"{BASE}/{sym}/1d/*.parquet"))
    if not fs: continue
    try: df = pd.concat([pd.read_parquet(f) for f in fs]).sort_index()
    except Exception: continue
    df = df[~df.index.duplicated(keep="last")]
    c = df["close"].tolist(); d = [str(x)[:10] for x in df.index]
    if len(c) < MIN_BARS + max(HZ) + 5: continue
    ed = set(EARN.get(sym) or [])
    if not ed: continue
    idx = {v: k for k, v in enumerate(d)}
    ebars = sorted(idx[x] for x in ed if x in idx)
    eset = set()
    for b in ebars:
        for k in range(b, min(len(d), b + 3)): eset.add(k)
    trig = [i for i in ebars
            if TREND_WINDOW <= i < len(c) - max(HZ)
            and c[i-1] > 0 and (c[i]/c[i-1] - 1) <= -DROP
            and c[i] > sma(c, i, TREND_WINDOW)]
    nq = 0
    for i in trig:
        e, prev = eps_asof(sym, d[i])
        if e is None: continue
        pe = (c[i] / e) if e > 0 else None
        good = (e > 0 and prev is not None and e > prev and pe is not None and 5 <= pe <= 60)
        tgt = qual if good else nonq
        for h in HZ: tgt[h].append(100 * (c[i+h]/c[i] - 1))
        if good:
            nq += 1; meta_q.append((sym, d[i], round(pe, 1)))
    if nq:
        pool = [i for i in range(TREND_WINDOW, len(c) - max(HZ))
                if i not in eset and c[i] > sma(c, i, TREND_WINDOW)]
        if pool:
            for i in rng.choice(pool, size=min(nq*5, len(pool)), replace=False):
                for h in HZ: null_q[h].append(100 * (c[i+h]/c[i] - 1))

n = len(qual[HZ[0]])
print(f"QUALITY earnings drops: {n}   non-quality: {len(nonq[HZ[0]])}")
if meta_q:
    yy = sorted(x[1] for x in meta_q); print(f"window {yy[0]} -> {yy[-1]}\n")
print(f"{'horizon':<9}{'qual n':>8}{'qual mean':>11}{'null mean':>11}{'Q1 edge':>10}{'nonq mean':>11}{'Q2 edge':>10}")
print("-"*70)
res = {}
for h in HZ:
    a, b, c2 = np.array(qual[h]), np.array(null_q[h]), np.array(nonq[h])
    if not len(a) or not len(b): continue
    res[h] = (a.mean()-b.mean(), a.mean()-(c2.mean() if len(c2) else 0.0))
    print(f"{h:<9}{len(a):>8}{a.mean():>10.2f}%{b.mean():>10.2f}%{res[h][0]:>+9.2f}pt"
          f"{(c2.mean() if len(c2) else float('nan')):>10.2f}%{res[h][1]:>+9.2f}pt")
print("\nGATES")
print(f"  V0 >= 150 quality events           {'PASS' if n>=150 else 'FAIL'}  ({n})")
q1 = res.get(60,(0,))[0] > 0 and res.get(120,(0,))[0] > 0
print(f"  Q1 beats own null at 60 AND 120    {'PASS' if q1 else 'FAIL'}  ({res.get(60,(0,))[0]:+.2f}pt / {res.get(120,(0,))[0]:+.2f}pt)")
q2 = res.get(120,(0,0))[1] >= 1.0
print(f"  Q2 beats non-quality by >=1pt @120 {'PASS' if q2 else 'FAIL'}  ({res.get(120,(0,0))[1]:+.2f}pt)")
print("\nQ3 " + ("evaluated only when Q1 passes — Q1 FAILED." if not q1 else "to run."))

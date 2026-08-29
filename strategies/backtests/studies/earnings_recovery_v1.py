"""Do sound stocks that drop on results RECOVER? — the owner's claim, tested at HIS horizon.

My earlier study (512f5af) tested earnings drops through the SWING exit: -8% stop,
20-session cap, target the 20-day mean. The owner's claim is different in shape --
"fundamentally strong stocks fall after results and get back fine" -- and a stock
that recovers over two or three months would be STOPPED OUT or TIMED OUT long
before it did so. Testing his idea with my exit machinery answered a question he
did not ask.

This measures the drop-and-hold directly, at four horizons, with NO stop.

THE CONTROL, which is the whole test: a no-stop forward return over 2020-2026 is
positive for almost anything, because the market rose. So every number is shown
against a NULL -- the same-horizon return of the same stock measured from a
random non-earnings day. If the earnings-drop bar does not beat its own null,
"they recover" is just "the market went up".

STILL NOT TESTED: "fundamentally strong". There is no fundamentals feed here.
Above-the-200-SMA is a trend proxy and nothing more.

PREDICTION, before running: the drop bar will show a positive raw return at every
horizon (the market rose) and will NOT beat the null. If it DOES beat the null at
the longer horizons, the owner is right and my earlier conclusion was
horizon-bound rather than wrong.
"""
import glob, json, os, sys
import numpy as np, pandas as pd
sys.path.insert(0,"/Users/skumar/sourcecode/tradepro/tradepro/strategies")
from tradepro_strategies.signals.mean_reversion import TREND_WINDOW, MIN_BARS
from tradepro_strategies.universe import universe_symbols

BASE=os.path.expanduser("~/.tradepro/bar_cache/us_etf")
EARN=json.load(open(os.path.expanduser("~/.tradepro/research/earnings_history.json")))
HORIZONS=[20,40,60,120]
DROP=0.05
def sma(c,i,n): return sum(c[i-n+1:i+1])/n

hits={h:[] for h in HORIZONS}; null={h:[] for h in HORIZONS}
rng=np.random.default_rng(7)
for sym in universe_symbols():
    fs=sorted(glob.glob(f"{BASE}/{sym}/1d/*.parquet"))
    if not fs: continue
    try: df=pd.concat([pd.read_parquet(f) for f in fs]).sort_index()
    except Exception: continue
    df=df[~df.index.duplicated(keep="last")]
    c=df["close"].tolist(); d=[str(x)[:10] for x in df.index]
    if len(c)<MIN_BARS+130: continue
    ed=set(EARN.get(sym) or [])
    if not ed: continue
    idx={dt:k for k,dt in enumerate(d)}
    ebars=sorted(idx[x] for x in ed if x in idx)
    eset=set()
    for bpos in ebars:
        for k in range(bpos,min(len(d),bpos+3)): eset.add(k)
    # earnings drops, above the 200-SMA
    trig=[i for i in ebars
          if TREND_WINDOW<=i<len(c)-max(HORIZONS)
          and c[i-1]>0 and (c[i]/c[i-1]-1)<=-DROP and c[i]>sma(c,i,TREND_WINDOW)]
    for i in trig:
        for h in HORIZONS: hits[h].append(100*(c[i+h]/c[i]-1))
    # NULL: same count of NON-earnings days, also above the 200-SMA
    pool=[i for i in range(TREND_WINDOW,len(c)-max(HORIZONS))
          if i not in eset and c[i]>sma(c,i,TREND_WINDOW)]
    if pool and trig:
        pick=rng.choice(pool,size=min(len(trig)*5,len(pool)),replace=False)
        for i in pick:
            for h in HORIZONS: null[h].append(100*(c[i+h]/c[i]-1))

print(f"earnings drops >= {DROP:.0%} above the 200-SMA: {len(hits[HORIZONS[0]])} events\n")
print(f"{'horizon':<10}{'n':>6}{'drop mean':>12}{'drop win%':>11}{'null mean':>12}{'null win%':>11}{'edge':>9}")
print("-"*72)
for h in HORIZONS:
    a=np.array(hits[h]); b=np.array(null[h])
    if not len(a) or not len(b): continue
    edge=a.mean()-b.mean()
    print(f"{h:<10}{len(a):>6}{a.mean():>11.2f}%{100*(a>0).mean():>10.1f}%"
          f"{b.mean():>11.2f}%{100*(b>0).mean():>10.1f}%{edge:>+8.2f}pt")

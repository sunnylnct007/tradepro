"""What did the rule DO in the 2020 crash — on the 136 names that lived through it?

The other 108 are capped and will stay capped (owner call, 29 Aug: "forget 2020
if we can't"). But 136 names is not nothing, and the crash question can be
partly answered without the 70-hour backfill.
"""
import glob, os, sys
import numpy as np, pandas as pd
sys.path.insert(0, "/Users/skumar/sourcecode/tradepro/tradepro/strategies")
from tradepro_strategies.signals.mean_reversion import (
    entry_signal, BB_WINDOW, STOP_PCT, MAX_HOLD, MIN_BARS)
from tradepro_strategies.universe import universe_symbols

BASE = os.path.expanduser("~/.tradepro/bar_cache/us_etf")
def sma(c,i,n): return sum(c[i-n+1:i+1])/n

rows=[]; covered=0
for sym in universe_symbols():
    fs=sorted(glob.glob(f"{BASE}/{sym}/1d/*.parquet"))
    if not fs: continue
    try: df=pd.concat([pd.read_parquet(f) for f in fs]).sort_index()
    except Exception: continue
    df=df[~df.index.duplicated(keep="last")]
    d=[str(x)[:10] for x in df.index]
    if not d or d[0] > "2019-06-01": continue      # must predate the crash properly
    covered+=1
    c=df["close"].tolist(); h=df["high"].tolist(); l=df["low"].tolist(); o=df["open"].tolist()
    i=MIN_BARS
    while i < len(c)-1:
        if not entry_signal(c,i): i+=1; continue
        entry=c[i]; stop=entry*(1-STOP_PCT); res=None
        for j in range(i+1,min(len(c),i+MAX_HOLD+1)):
            tgt=sma(c,j,BB_WINDOW); hs,ht=l[j]<=stop,h[j]>=tgt
            if hs and ht: res=(100*(min(stop,o[j])/entry-1),j-i); break
            if ht: res=(100*(max(tgt,o[j])/entry-1),j-i); break
            if hs: res=(100*(min(stop,o[j])/entry-1),j-i); break
        if res is None:
            j=min(len(c)-1,i+MAX_HOLD); res=(100*(c[j]/entry-1),j-i)
        rows.append((sym,d[i],res[0])); i+=max(1,res[1])+1

def stat(rs,lbl):
    if not rs: print(f"  {lbl:<28} n=0"); return
    v=np.array([r[2] for r in rs])
    print(f"  {lbl:<28} n={len(v):>5}  win {100*(v>0).mean():>5.1f}%  "
          f"mean {v.mean():>+6.2f}%  worst {v.min():>+6.1f}%")

print(f"names that lived through 2020: {covered} of 244\n")
crash=[r for r in rows if "2020-02-01" <= r[1] <= "2020-04-30"]
y2020=[r for r in rows if r[1][:4]=="2020"]
rest=[r for r in rows if not ("2020-02-01" <= r[1] <= "2020-04-30")]
stat(rows,  "all trades, these names")
stat(y2020, "calendar 2020")
stat(crash, "THE CRASH (Feb-Apr 2020)")
stat(rest,  "everything except the crash")
if crash:
    w=sorted(crash,key=lambda r:r[2])[:5]
    print("\n  worst crash-window trades:")
    for s,dt,v in w: print(f"    {s:<6} {dt}  {v:+.1f}%")

"""What does the 200-SMA trend filter actually buy us?

Owner, 29 Aug: "as per swing strategy if 'trend filter (below the 200-SMA)' was
intact it could have been a good buy" — i.e. some names the filter refuses look
like good setups.

The rule requires the SYMBOL above its OWN 200-SMA. This takes every 2.5-sigma
signal and splits on that filter alone, same exit machinery both sides.
"""
import glob, os, sys, statistics as st
import numpy as np, pandas as pd
sys.path.insert(0,"/Users/skumar/sourcecode/tradepro/tradepro/strategies")
from tradepro_strategies.signals.mean_reversion import (
    SIGMA, BB_WINDOW, STOP_PCT, MAX_HOLD, MIN_BARS, TREND_WINDOW)
from tradepro_strategies.universe import universe_symbols
BASE=os.path.expanduser("~/.tradepro/bar_cache/us_etf")
def sma(c,i,n): return sum(c[i-n+1:i+1])/n

above=[]; below=[]
for sym in universe_symbols():
    fs=sorted(glob.glob(f"{BASE}/{sym}/1d/*.parquet"))
    if not fs: continue
    try: df=pd.concat([pd.read_parquet(f) for f in fs]).sort_index()
    except Exception: continue
    df=df[~df.index.duplicated(keep="last")]
    c=df["close"].tolist(); h=df["high"].tolist(); l=df["low"].tolist(); o=df["open"].tolist()
    d=[str(x)[:10] for x in df.index]
    if len(c)<MIN_BARS+MAX_HOLD+2: continue
    i=MIN_BARS
    while i<len(c)-1:
        w=c[i-BB_WINDOW+1:i+1]; m=sum(w)/BB_WINDOW; sd=st.pstdev(w)
        if not (sd>0 and c[i] < m - SIGMA*sd):
            i+=1; continue
        up = c[i] > sma(c,i,TREND_WINDOW)          # the FILTER, the only difference
        entry=c[i]; stop=entry*(1-STOP_PCT); res=None
        for j in range(i+1,min(len(c),i+MAX_HOLD+1)):
            tgt=sma(c,j,BB_WINDOW); hs,ht=l[j]<=stop,h[j]>=tgt
            if hs and ht: res=(100*(min(stop,o[j])/entry-1),j-i); break
            if ht: res=(100*(max(tgt,o[j])/entry-1),j-i); break
            if hs: res=(100*(min(stop,o[j])/entry-1),j-i); break
        if res is None:
            j=min(len(c)-1,i+MAX_HOLD); res=(100*(c[j]/entry-1),j-i)
        (above if up else below).append((sym,d[i],res[0]))
        i+=max(1,res[1])+1

def rep(lbl,rs):
    v=np.array([x[2] for x in rs])
    print(f"  {lbl:<28} n={len(v):>5}  win {100*(v>0).mean():>5.1f}%  mean {v.mean():>+6.2f}%  "
          f"median {np.median(v):>+6.2f}%  worst {v.min():>+7.1f}%  total {v.sum():>7.0f}%")
print("Every 2.5-sigma signal, split ONLY on the symbol's own 200-SMA:")
rep("ABOVE 200-SMA (the rule)", above)
rep("BELOW 200-SMA (refused)", below)
b=np.array([x[2] for x in below]); a=np.array([x[2] for x in above])
print(f"\n  the filter costs {len(b)} trades and avoids {b.mean()-a.mean():+.2f}pt/trade")
print(f"  tail: below-200 trades losing worse than -15%: "
      f"{100*(b<-15).mean():.1f}%  vs above-200 {100*(a<-15).mean():.1f}%")

# ── THE TWO TESTS THAT MATTER ────────────────────────────────────────────
# A full-sample average can hide a regime. The rule wins 8.3% of the time in a
# crash; names BELOW their 200-SMA in a crash are the falling-knife case the
# filter exists for, and that damage would be invisible in a 6,000-trade mean.
print("\nCRASH WINDOW (Feb-Apr 2020)")
for lbl, rs in (("ABOVE 200-SMA", above), ("BELOW 200-SMA", below)):
    v=np.array([x[2] for x in rs if "2020-02-01"<=x[1]<="2020-04-30"])
    if len(v): rep(lbl, [(None,None,x) for x in v])
    else: print(f"  {lbl:<28} n=0")

print("\nTWO-SPLIT — below-200 mean vs above-200 mean, per cell")
alld=sorted(x[1] for x in below); tmid=alld[len(alld)//2]
syms=sorted({x[0] for x in below}); half=set(syms[:len(syms)//2])
cells=0
for tl,tf in (("early",lambda x:x[1]<tmid),("late ",lambda x:x[1]>=tmid)):
    for sl,sf in (("setA",lambda x:x[0] in half),("setB",lambda x:x[0] not in half)):
        bb=[x[2] for x in below if tf(x) and sf(x)]
        aa=[x[2] for x in above if tf(x) and sf(x)]
        if not bb or not aa: continue
        diff=np.mean(bb)-np.mean(aa); cells+= diff>0
        print(f"  {tl} {sl}: below {np.mean(bb):+6.2f}%  above {np.mean(aa):+6.2f}%  "
              f"diff {diff:+6.2f}pt  n={len(bb):>4}  {'+' if diff>0 else '-'}")
print(f"  cells where REMOVING the filter helps: {cells}/4")

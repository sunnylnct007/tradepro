"""Intraday dip v1 — corrected grading.

TWO FLAWS IN THE GATES AS WRITTEN, found by running them:

G6 compared expectancy PER TRADE against a benchmark PER DAY. A trade holding
10 sessions was being credited against one day of being long. Fixed: both
sides are now per day held.

G4 (same-day exit >= 40%) is UNMEASURABLE under the pessimistic rule. Pessimism
says a same-session target never counts, so the only same-day exit possible is
a stop — the 1-8% observed is structural, not a property of the strategy. G4
needs intraday bars, and the store has a median of 14 sessions of them. So G4
is reported as UNGRADEABLE rather than failed.
"""
import statistics as st, itertools
from tradepro_strategies.universe import universe_symbols
from tradepro_strategies.cli.build_universe import _load

DIPS=[0.5,1,2,3]; TGTS=[0.5,1,2,3,5]; STOPS=[-4,-8]; CARRY=[1,3,5,10]
MAXDAY=0.35
data={}
for s in universe_symbols():
    df=_load(s)
    if df is None or "open" not in df.columns: continue
    data[s]=(df["open"].tolist(),df["high"].tolist(),df["low"].tolist(),
             df["close"].tolist(),[str(x)[:10] for x in df.index])
bench=[]
for s,(o,h,l,c,d) in data.items():
    for i in range(1,len(c)):
        if o[i]>0 and c[i-1]>0 and abs(c[i]/c[i-1]-1)<=MAXDAY: bench.append(100*(c[i]/o[i]-1))
BENCH=st.mean(bench)

def run(dip,tgt,stop,carry,pess):
    sess=0;fills=0;res=[];days=[];sd=0;dates=[];sidx=[]
    for si,(s,(o,h,l,c,d)) in enumerate(data.items()):
        n=len(c);i=1
        while i<n:
            if o[i]<=0 or c[i-1]<=0 or abs(o[i]/c[i-1]-1)>MAXDAY: i+=1;continue
            sess+=1; lim=o[i]*(1-dip/100)
            if l[i]>lim: i+=1;continue
            fills+=1; T=lim*(1+tgt/100); S=lim*(1+stop/100); out=None
            for j in range(i,min(n,i+carry+1)):
                if j>i and (c[j-1]<=0 or abs(c[j]/c[j-1]-1)>MAXDAY): break
                ht=h[j]>=T; hs=l[j]<=S
                if j==i and pess: ht=False
                if hs: out=(stop,j-i);break
                if ht: out=(tgt,j-i);break
            if out is None:
                j=min(n-1,i+carry); out=(100*(c[j]/lim-1), j-i)
            res.append(out[0]); days.append(max(1,out[1])); sd+=(out[1]==0)
            dates.append(d[i]); sidx.append(si); i+=max(1,out[1])+1
    if not res: return None
    per_day=[r/dd for r,dd in zip(res,days)]
    return {"fill_rate":fills/max(1,sess),"n":len(res),
            "win":100*sum(1 for x in res if x>0)/len(res),"exp":st.mean(res),
            "exp_per_day":st.mean(per_day),"median":st.median(res),
            "hold":st.mean(days),"sameday":100*sd/len(res),
            "res":res,"days":days,"dates":dates,"sidx":sidx}

print(f"benchmark: being long open->close = {BENCH:+.4f}%/day\n")
print("YOUR IDEA as described — small dip, small target, out same day or next:")
print(f"{'dip':>5}{'tgt':>5}{'stop':>6}{'carry':>7}{'fill%':>8}{'n':>8}{'win%':>7}"
      f"{'exp/trade':>11}{'exp/day':>10}{'vs long':>10}")
for dip,tgt,carry in [(0.5,0.5,1),(1,1,1),(1,1,3),(2,2,3),(0.5,1,1),(1,2,3)]:
    r=run(dip,tgt,-8,carry,True)
    if r: print(f"{dip:>5}{tgt:>5}{-8:>6}{carry:>7}{100*r['fill_rate']:>7.1f}%{r['n']:>8}"
                f"{r['win']:>6.1f}%{r['exp']:>10.3f}%{r['exp_per_day']:>9.4f}%"
                f"{('BEATS' if r['exp_per_day']>BENCH else 'loses'):>10}")

rows=[]
for dip,tgt,stop,carry in itertools.product(DIPS,TGTS,STOPS,CARRY):
    r=run(dip,tgt,stop,carry,True)
    if r: rows.append(((dip,tgt,stop,carry),r))
rows.sort(key=lambda x:-x[1]["exp_per_day"])
print(f"\nfull sweep ({len(rows)} cells), ranked by expectancy PER DAY HELD:")
print(f"{'dip':>5}{'tgt':>5}{'stop':>6}{'carry':>7}{'fill%':>8}{'n':>8}{'win%':>7}"
      f"{'exp/day':>10}{'hold':>7}{'  vs being long'}")
for k,r in rows[:10]:
    print(f"{k[0]:>5}{k[1]:>5}{k[2]:>6}{k[3]:>7}{100*r['fill_rate']:>7.1f}%{r['n']:>8}"
          f"{r['win']:>6.1f}%{r['exp_per_day']:>9.4f}%{r['hold']:>7.1f}"
          f"   {'BEATS' if r['exp_per_day']>BENCH else 'loses'} ({r['exp_per_day']/BENCH:.1f}x)")

best_k,best=rows[0]
print(f"\nbest cell {best_k}: n={best['n']}, exp/day {best['exp_per_day']:+.4f}% vs bench {BENCH:+.4f}%")
opt=run(*best_k,False)
print(f"optimistic band for the same cell: exp/trade {opt['exp']:+.3f}% (pessimistic {best['exp']:+.3f}%)"
      f" · same-day exits {opt['sameday']:.1f}% (pessimistic {best['sameday']:.1f}%)")

import numpy as np
di=np.array([int(x[:4])*10000+int(x[5:7])*100+int(x[8:10]) for x in best["dates"]])
si=np.array(best["sidx"]); pd_=np.array([r/d for r,d in zip(best["res"],best["days"])])
mid=np.median(di)
print("\nG5 both splits (expectancy per day beats being long in every cell?):")
allok=True
for name,m in (("time 1st half",di<mid),("time 2nd half",di>=mid),
               ("symbols even",si%2==0),("symbols odd",si%2==1)):
    v=pd_[m]; ok=v.mean()>BENCH; allok&=ok
    print(f"   {name}: {'PASS' if ok else 'FAIL'} — {v.mean():+.4f}% vs {BENCH:+.4f}% (n={len(v)})")
print(f"\nG1 fill>=15%: {'PASS' if best['fill_rate']>=0.15 else 'FAIL'}")
print(f"G2 exp>=+0.15%/trade: {'PASS' if best['exp']>=0.15 else 'FAIL'} ({best['exp']:+.3f}%)")
print(f"G3 win>=55%: {'PASS' if best['win']>=55 else 'FAIL'} ({best['win']:.1f}%)")
print(f"G4 sameday>=40%: UNGRADEABLE on daily bars (needs intraday; store has ~14 sessions)")
print(f"G5 both splits: {'PASS' if allok else 'FAIL'}")
print(f"G6 beats being long: {'PASS' if best['exp_per_day']>BENCH else 'FAIL'}")
print(f"G7 n>=5000: {'PASS' if best['n']>=5000 else 'FAIL'} ({best['n']})")

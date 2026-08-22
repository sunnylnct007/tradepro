"""Corrected AGAIN. mean(return/days) overweights short lucky trades: a +3% in
one day contributes 3%/day to the average, and you cannot earn that every day.
The capital-time weighting is sum(returns)/sum(days_held) — total profit per
day of capital actually committed. That is comparable to being long."""
import statistics as st, itertools, numpy as np
from tradepro_strategies.universe import universe_symbols
from tradepro_strategies.cli.build_universe import _load
DIPS=[0.5,1,2,3]; TGTS=[0.5,1,2,3,5]; STOPS=[-4,-8]; CARRY=[1,3,5,10]; MAXDAY=0.35
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
def run(dip,tgt,stop,carry,pess=True):
    sess=0;fills=0;res=[];days=[];dates=[];sidx=[]
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
            res.append(out[0]); days.append(max(1,out[1])); dates.append(d[i]); sidx.append(si)
            i+=max(1,out[1])+1
    if not res: return None
    return {"fill_rate":fills/max(1,sess),"n":len(res),
            "win":100*sum(1 for x in res if x>0)/len(res),"exp":st.mean(res),
            "per_day":sum(res)/sum(days),"hold":st.mean(days),
            "res":np.array(res),"days":np.array(days),
            "dates":np.array(dates),"sidx":np.array(sidx)}
print(f"benchmark, being long open->close: {BENCH:+.4f}%/day\n")
rows=[]
for k in itertools.product(DIPS,TGTS,STOPS,CARRY):
    r=run(*k)
    if r: rows.append((k,r))
rows.sort(key=lambda x:-x[1]["per_day"])
print("ranked by CAPITAL-TIME expectancy (total return / total days held):")
print(f"{'dip':>5}{'tgt':>5}{'stop':>6}{'carry':>7}{'fill%':>8}{'n':>8}{'win%':>7}"
      f"{'exp/trade':>11}{'%/day held':>12}{'hold':>7}{'  vs long'}")
for k,r in rows[:10]:
    print(f"{k[0]:>5}{k[1]:>5}{k[2]:>6}{k[3]:>7}{100*r['fill_rate']:>7.1f}%{r['n']:>8}"
          f"{r['win']:>6.1f}%{r['exp']:>10.3f}%{r['per_day']:>11.4f}%{r['hold']:>7.1f}"
          f"   {r['per_day']/BENCH:>5.2f}x")
print("\nYOUR IDEA as described (same-day / next-day, small target):")
for dip,tgt,carry in [(0.5,0.5,1),(1,1,1),(1,1,3),(1,2,3)]:
    r=run(dip,tgt,-8,carry)
    print(f"  dip {dip}% tgt {tgt}% carry {carry}: win {r['win']:.1f}% · "
          f"exp/trade {r['exp']:+.3f}% · {r['per_day']:+.4f}%/day held "
          f"({r['per_day']/BENCH:.2f}x long)")
k,b=rows[0]
print(f"\nbest cell {k}: {b['per_day']:+.4f}%/day held vs {BENCH:+.4f}% = {b['per_day']/BENCH:.2f}x")
di=np.array([int(x[:4])*10000+int(x[5:7])*100+int(x[8:10]) for x in b["dates"]]); mid=np.median(di)
print("G5 both splits (capital-time expectancy beats being long?):")
allok=True
for name,m in (("time 1st half",di<mid),("time 2nd half",di>=mid),
               ("symbols even",b['sidx']%2==0),("symbols odd",b['sidx']%2==1)):
    v=b['res'][m].sum()/b['days'][m].sum(); ok=v>BENCH; allok&=ok
    print(f"   {name}: {'PASS' if ok else 'FAIL'} — {v:+.4f}% vs {BENCH:+.4f}% ({v/BENCH:.2f}x)")
print(f"\nG6 beats being long: {'PASS' if b['per_day']>BENCH else 'FAIL'}")
print(f"G5 both splits: {'PASS' if allok else 'FAIL'}")

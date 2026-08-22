"""Intraday dip-entry v1 — graded against INTRADAY_DIP_GATES_V1.md (1b654a4)."""
import statistics as st, itertools, json
from tradepro_strategies.universe import universe_symbols
from tradepro_strategies.cli.build_universe import _load

DIPS=[0.5,1,2,3,5]; TGTS=[0.5,1,2,3,5]; STOPS=[-4,-8]; CARRY=[1,3,5,10]
MAXDAY=0.35

data={}
for s in universe_symbols():
    df=_load(s)
    if df is None or "open" not in df.columns: continue
    o=df["open"].tolist(); h=df["high"].tolist(); l=df["low"].tolist(); c=df["close"].tolist()
    d=[str(x)[:10] for x in df.index]
    data[s]=(o,h,l,c,d)
print(f"loaded {len(data)} symbols")

# benchmark: mean next-day open->close return of the same universe (G6)
bench=[]
for s,(o,h,l,c,d) in data.items():
    for i in range(1,len(c)):
        if o[i]>0 and c[i-1]>0 and abs(c[i]/c[i-1]-1)<=MAXDAY:
            bench.append(100*(c[i]/o[i]-1))
BENCH=st.mean(bench)
print(f"benchmark (be long open->close, same universe): {BENCH:+.4f}%/day over {len(bench):,} sessions\n")

def run(dip,tgt,stop,carry,pessimistic=True):
    sessions=0; fills=0; res=[]; sameday=0; meta=[]
    for si,(s,(o,h,l,c,d)) in enumerate(data.items()):
        n=len(c); i=1
        while i<n:
            if o[i]<=0 or c[i-1]<=0 or abs(o[i]/c[i-1]-1)>MAXDAY: i+=1; continue
            sessions+=1
            lim=o[i]*(1-dip/100)
            if l[i]>lim: i+=1; continue           # never traded down to us
            fills+=1
            entry=lim; T=entry*(1+tgt/100); S=entry*(1+stop/100)
            out=None; day=0
            for j in range(i, min(n, i+carry+1)):
                if j>i and (c[j-1]<=0 or abs(c[j]/c[j-1]-1)>MAXDAY): break
                hit_t = h[j]>=T; hit_s = l[j]<=S
                if j==i:
                    # entry session: the low that filled us and the high may be
                    # in either order. Pessimistic = the high came first, so it
                    # is not available to us.
                    if pessimistic: hit_t=False
                if hit_s and hit_t: out=(stop,j-i); break
                if hit_s: out=(stop,j-i); break
                if hit_t: out=(tgt,j-i); break
            if out is None:
                j=min(n-1,i+carry); out=(100*(c[j]/entry-1), j-i)
            res.append(out[0]); sameday += (out[1]==0)
            meta.append((d[i],si))
            i += max(1,out[1])+1
    if not res: return None
    return {"sessions":sessions,"fills":fills,"fill_rate":fills/max(1,sessions),
            "n":len(res),"win":100*sum(1 for x in res if x>0)/len(res),
            "exp":st.mean(res),"median":st.median(res),"worst":min(res),
            "sameday":100*sameday/len(res),"res":res,"meta":meta}

rows=[]
for dip,tgt,stop,carry in itertools.product(DIPS,TGTS,STOPS,CARRY):
    r=run(dip,tgt,stop,carry,pessimistic=True)
    if r: rows.append(((dip,tgt,stop,carry),r))
print(f"swept {len(rows)} combinations (pessimistic)\n")

def gates(k,r):
    dip,tgt,stop,carry=k
    g={"G1 fill>=15%": r["fill_rate"]>=0.15, "G2 exp>=+0.15%": r["exp"]>=0.15,
       "G3 win>=55%": r["win"]>=55, "G4 sameday>=40%": r["sameday"]>=40,
       "G6 beats long": r["exp"]>BENCH, "G7 n>=5000": r["n"]>=5000}
    return g

ok=[(k,r) for k,r in rows if all(gates(k,r).values())]
rows.sort(key=lambda x:-x[1]["exp"])
print(f"{'dip':>5}{'tgt':>5}{'stop':>6}{'carry':>7}{'fill%':>8}{'n':>8}{'win%':>7}{'exp%':>8}{'med%':>8}{'sameday%':>10}{'  gates failed'}")
for k,r in rows[:14]:
    f=[g for g,v in gates(k,r).items() if not v]
    print(f"{k[0]:>5}{k[1]:>5}{k[2]:>6}{k[3]:>7}{100*r['fill_rate']:>7.1f}%{r['n']:>8}"
          f"{r['win']:>6.1f}%{r['exp']:>7.3f}%{r['median']:>7.2f}%{r['sameday']:>9.1f}%"
          f"   {', '.join(f) if f else 'NONE — all pass'}")
print(f"\ncells passing G1-G4,G6,G7 (pessimistic): {len(ok)}")
json.dump({"bench":BENCH,"pass":[list(k) for k,_ in ok]}, open("/private/tmp/claude-501/-Users-skumar-sourcecode-tradepro-tradepro/9ed351b8-677f-49d5-a41e-d93c28c8a91b/scratchpad/dip_pass.json","w"))

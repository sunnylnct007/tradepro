"""Intraday coverage across the DEFINED universe — what is actually testable?

The owner's intraday idea (enter on a dip inside the session, book profit the
same day, carry over if unfilled) cannot be tested on daily bars at all. This
answers what exists before anything is built on the assumption that it does.
"""
import glob, os, collections, datetime as dt
from tradepro_strategies.universe import universe_symbols

BASE = os.path.expanduser("~/.tradepro/bar_cache/us_etf")
syms = universe_symbols()
print(f"universe: {len(syms)} symbols\n")

rows = []
for s in syms:
    r = {"symbol": s}
    for res in ("5m", "1m"):
        fs = sorted(glob.glob(f"{BASE}/{s}/{res}/*.parquet"))
        if not fs:
            r[res] = None; continue
        try:
            import pandas as pd
            df = pd.concat([pd.read_parquet(f) for f in fs]).sort_index()
            df = df[~df.index.duplicated(keep="last")]
        except Exception as e:
            r[res] = {"error": str(e)[:40]}; continue
        d = sorted({str(x)[:10] for x in df.index})
        src = collections.Counter(str(x) for x in df["source"].dropna().tolist()) if "source" in df.columns else {}
        ibkr = sum(n for k, n in src.items() if "ibkr" in k.lower())
        r[res] = {"bars": len(df), "sessions": len(d), "first": d[0], "last": d[-1],
                  "ibkr_pct": round(100 * ibkr / max(1, sum(src.values())), 1) if src else None}
    rows.append(r)

def summarize(res):
    have = [r for r in rows if r.get(res) and "error" not in r[res]]
    print(f"=== {res} ===")
    print(f"  symbols with any {res} data: {len(have)}/{len(syms)}")
    if not have: return
    sess = sorted(r[res]["sessions"] for r in have)
    print(f"  sessions per symbol: median {sess[len(sess)//2]} · min {sess[0]} · max {sess[-1]}")
    lasts = collections.Counter(r[res]["last"] for r in have)
    print(f"  most recent session present: " + ", ".join(f"{k} ({n})" for k, n in lasts.most_common(4)))
    ib = [r[res]["ibkr_pct"] for r in have if r[res]["ibkr_pct"] is not None]
    if ib: print(f"  IBKR share: median {sorted(ib)[len(ib)//2]}% · min {min(ib)}% · max {max(ib)}%")
    deep = [r for r in have if r[res]["sessions"] >= 250]
    print(f"  symbols with >= 250 intraday sessions (~1yr): {len(deep)}")
    if deep:
        print("    " + ", ".join(r["symbol"] for r in sorted(deep, key=lambda x: -x[res]["sessions"])[:20]))
    print()

summarize("5m"); summarize("1m")
none5 = [r["symbol"] for r in rows if not r.get("5m")]
print(f"NO 5m data at all ({len(none5)}): " + ", ".join(none5[:40]) + (" ..." if len(none5) > 40 else ""))

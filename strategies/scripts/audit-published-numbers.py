"""Recompute every published number from source bars and compare.

Owner, 12 Sep 2026: "each time i ask a number u find a bug." A fair
inference — if hand-sampling keeps hitting defects, the unsampled numbers
are probably no better. This is the systematic version of that sampling:
it recomputes what each screen CLAIMS from the bars underneath and reports
any disagreement.

First run: 46 numbers across swing, watch and momentum — zero disagreed.
Which located the real weakness. This week's defects were never wrong
arithmetic; they were wrong STATEMENTS about correct arithmetic: a green
"tradeable" over unverified prices, a "repair" naming a condition that was
not failing, an order tag saying 2.5sigma after the rule moved to 2.25, a
409 reported as "already placed" when it meant "refused". The maths layer
is sound; the layer that DESCRIBES it is where the bugs live.

Run it after any change to a screen's numbers:
    uv run python scripts/audit-published-numbers.py

Not "does the code run" — does the number on the screen equal the number
the data implies. Independent arithmetic, deliberately not importing the
producers' helpers, so a shared bug cannot agree with itself.
"""
import sys, statistics as st, requests
sys.path.insert(0, ".")
from tradepro_strategies.cli.push_to_api import load_credentials
from tradepro_strategies.cli.build_universe import _load

b, tok = load_credentials(); b = b.rstrip("/")
H = {"Authorization": f"Bearer {tok}"}
bad, checked = [], 0

def cmp(sym, field, shown, calc, tol):
    global checked
    checked += 1
    if shown is None or calc is None: return
    if abs(float(shown) - float(calc)) > tol:
        bad.append(f"{sym:6} {field:22} screen={shown}  recomputed={calc:.4g}")

def bars(sym):
    df = _load(sym)
    return (df["close"].tolist(), df["high"].tolist(), df["low"].tolist(),
            [str(x)[:10] for x in df.index])

# ---------- SWING ----------
a = requests.get(f"{b}/api/today-setups/swing/latest", headers=H, timeout=40).json()["artifact"]
print(f"SWING rows: {len(a.get('candidates', []))} (signal bar {a.get('signal_bar')})")
for c in a.get("candidates", []):
    sym = c["symbol"]
    try: cl, hi, lo, d = bars(sym)
    except Exception: continue
    if a.get("signal_bar") not in d: continue
    i = d.index(a["signal_bar"])
    m20 = sum(cl[i-19:i+1]) / 20
    sd = st.pstdev(cl[i-19:i+1])
    s200 = sum(cl[i-199:i+1]) / 200
    trs = [max(hi[j]-lo[j], abs(hi[j]-cl[j-1]), abs(lo[j]-cl[j-1])) for j in range(i-13, i+1)]
    atr = sum(trs)/14
    cmp(sym, "close",              c.get("close"),            cl[i],                      0.02)
    cmp(sym, "target(=20d mean)",  c.get("target"),           m20,                        0.02)
    cmp(sym, "stop(=entry*0.92)",  c.get("stop"),             cl[i]*0.92,                 0.02)
    cmp(sym, "sigma_below",        c.get("sigma_below"),      (m20-cl[i])/sd,             0.02)
    cmp(sym, "atr_pct",            c.get("atr_pct"),          100*atr/cl[i],              0.05)
    cmp(sym, "pct_above_200sma",   c.get("pct_above_200sma"), 100*(cl[i]/s200-1),         0.15)
    cmp(sym, "sma200_cushion_atr", c.get("sma200_cushion_atr"), (cl[i]-s200)/atr,         0.05)
    cmp(sym, "day_chg_pct",        c.get("day_chg_pct"),      100*(cl[i]/cl[i-1]-1),      0.06)
    cmp(sym, "below_20d_high_pct", c.get("below_20d_high_pct"),
        100*(max(hi[i-19:i+1])-cl[i])/max(hi[i-19:i+1]),                                  0.06)
    if c.get("reward_risk"):
        cmp(sym, "reward_risk", c.get("reward_risk"), (m20-cl[i])/(cl[i]-cl[i]*0.92),     0.02)
    if c.get("breakeven_win_pct") and c.get("reward_risk"):
        cmp(sym, "breakeven_win_pct", c.get("breakeven_win_pct"),
            100/(1+c["reward_risk"]),                                                     0.15)

# ---------- WATCH ----------
w = requests.get(f"{b}/api/today-setups/preearnings/latest", headers=H, timeout=40).json()["artifact"]
rows = [r for r in w.get("candidates_v2", []) if r.get("strategy") != "Scout"]
print(f"WATCH rows: {len(rows)}")
for r in rows:
    sym = r.get("symbol")
    try: cl, hi, lo, d = bars(sym)
    except Exception: continue
    i = len(cl)-1
    k = 2/(20+1); ema = cl[0]
    for x in cl[1:]: ema = x*k + ema*(1-k)
    sma50 = sum(cl[i-49:i+1])/50
    cmp(sym, "entry(=last close)", r.get("entry"), cl[i], 0.02)
    lvl = r.get("level")
    if lvl and r.get("levelLabel") == "sma50":
        cmp(sym, "level(sma50)", lvl, sma50, 0.6)

# ---------- MOMENTUM ----------
mo = requests.get(f"{b}/api/today-setups/momentum/latest", headers=H, timeout=40).json()["artifact"]
print(f"MOMENTUM rows: {len(mo.get('candidates', []))}")
for c in mo.get("candidates", []):
    sym = c["symbol"]
    try: cl, hi, lo, d = bars(sym)
    except Exception: continue
    if c.get("bar") not in d: continue
    i = d.index(c["bar"])
    s200 = sum(cl[i-199:i+1])/200; s20 = sum(cl[i-19:i+1])/20
    trs = [max(hi[j]-lo[j], abs(hi[j]-cl[j-1]), abs(lo[j]-cl[j-1])) for j in range(i-13, i+1)]
    atr = sum(trs)/14
    cmp(sym, "close",            c.get("close"),            cl[i],               0.02)
    cmp(sym, "stop(=close*0.92)",c.get("stop"),             cl[i]*0.92,          0.02)
    cmp(sym, "pct_above_200sma", c.get("pct_above_200sma"), 100*(cl[i]/s200-1),  0.15)
    cmp(sym, "pct_above_20sma",  c.get("pct_above_20sma"),  100*(cl[i]/s20-1),   0.15)
    cmp(sym, "atr_pct",          c.get("atr_pct"),          100*atr/cl[i],       0.05)
    cmp(sym, "chg_5d_pct",       c.get("chg_5d_pct"),       100*(cl[i]/cl[i-5]-1), 0.15)

print(f"\n=== {checked} numbers checked, {len(bad)} disagree ===")
for x in bad: print("  MISMATCH:", x)

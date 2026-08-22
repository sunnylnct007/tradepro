"""Does entry EXTENSION above the 20-SMA predict momentum-trade failure?

Asked because PLTR qualified at +12.7% above its 20-day average while every
other candidate sat between +0.4% and +8.5%. Bucketing every historical
momentum entry by that same number answers "is PLTR a fake-out" with the
rule's own record instead of an opinion about the chart.
"""
import os, glob, statistics as st
from tradepro_strategies.cli.momentum_candidates import (
    _tradeable, poison_check, _load, sma, _entry_signal, STOP_PCT, TRAIL_PCT, MAX_HOLD, BASE_DIR)

buckets = {"0-3%": [], "3-6%": [], "6-9%": [], "9-12%": [], ">12%": []}
def bucket(x):
    for name, hi in (("0-3%",3),("3-6%",6),("6-9%",9),("9-12%",12)):
        if x < hi: return name
    return ">12%"

syms = [s for s in sorted(os.listdir(BASE_DIR)) if _tradeable(s)]
for sym in syms:
    df = _load(sym)
    if df is None: continue
    c = df["close"].tolist(); h = df["high"].tolist(); l = df["low"].tolist()
    if not poison_check(c)[0]: continue
    i = 210
    n = len(c)
    while i < n - 1:
        if not _entry_signal(c, h, l, i):
            i += 1; continue
        ext = 100 * (c[i] / sma(c, i, 20) - 1)
        entry = c[i]; peak = entry; j = i + 1; exit_i = None; bad = False
        while j <= min(n - 1, i + MAX_HOLD):
            if c[j-1] > 0 and abs(c[j]/c[j-1]-1) > 0.35: bad = True; break
            if c[j] <= entry*(1-STOP_PCT): exit_i = j; break
            peak = max(peak, c[j])
            if c[j] <= peak*(1-TRAIL_PCT): exit_i = j; break
            j += 1
        if bad: i = j+1; continue
        if exit_i is None:
            if j > i + MAX_HOLD: exit_i = min(n-1, i+MAX_HOLD)
            else: break
        buckets[bucket(ext)].append(100*(c[exit_i]/entry-1))
        i = exit_i + 1

print(f"{'extension at entry':<20}{'trades':>8}{'win%':>8}{'mean%':>9}{'median%':>9}{'worst%':>9}")
for k, v in buckets.items():
    if not v: continue
    print(f"{k:<20}{len(v):>8}{100*sum(1 for x in v if x>0)/len(v):>7.1f}%{st.mean(v):>8.2f}%"
          f"{st.median(v):>8.2f}%{min(v):>8.1f}%")

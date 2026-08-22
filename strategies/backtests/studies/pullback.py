"""Is a "pullback" where PRICE FELL to the 10-SMA different from one where the
10-SMA rose to meet a FLAT price?

PLTR triggered the second kind. It gapped +29.5% on 5.1x volume, then drifted
sideways for six sessions on HALF average volume while the 10-day average
climbed up to it. The rule cannot tell those apart — both end with
close ~= 10-SMA — but they are not the same setup, so test whether the rule's
own record can tell them apart.

Split on the 5-session price change at entry, and on entry volume.
"""
import os, statistics as st
from tradepro_strategies.cli.momentum_candidates import (
    _tradeable, poison_check, _load, sma, _entry_signal, STOP_PCT, TRAIL_PCT, MAX_HOLD, BASE_DIR)

rows = []
for sym in [s for s in sorted(os.listdir(BASE_DIR)) if _tradeable(s)]:
    df = _load(sym)
    if df is None: continue
    c = df["close"].tolist(); h = df["high"].tolist(); l = df["low"].tolist()
    v = df["volume"].tolist() if "volume" in df.columns else None
    if not poison_check(c)[0]: continue
    n = len(c); i = 210
    while i < n - 1:
        if not _entry_signal(c, h, l, i):
            i += 1; continue
        chg5 = 100 * (c[i] / c[i - 5] - 1)
        av = (sum(v[i-20:i]) / 20) if v and sum(v[i-20:i]) else 0
        vr = (v[i] / av) if av else None
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
        rows.append((chg5, vr, 100*(c[exit_i]/entry-1)))
        i = exit_i + 1

def show(title, groups):
    print(f"\n{title}")
    print(f"{'':<34}{'trades':>8}{'win%':>8}{'mean%':>9}{'median%':>10}{'worst%':>9}")
    for name, vals in groups:
        if not vals: continue
        print(f"{name:<34}{len(vals):>8}{100*sum(1 for x in vals if x>0)/len(vals):>7.1f}%"
              f"{st.mean(vals):>8.2f}%{st.median(vals):>9.2f}%{min(vals):>8.1f}%")

show("A. Did PRICE fall into the average, or did the average rise to meet it?", [
    ("price FELL hard (5d < -4%)",        [r[2] for r in rows if r[0] < -4]),
    ("price fell (5d -4%..-1%)",          [r[2] for r in rows if -4 <= r[0] < -1]),
    ("price FLAT (5d -1%..+1%)",          [r[2] for r in rows if -1 <= r[0] <= 1]),
    ("price still RISING (5d > +1%)",     [r[2] for r in rows if r[0] > 1]),
])
show("B. Volume on the entry bar (vs its own 20-day average)", [
    ("dried up   (< 0.7x)",  [r[2] for r in rows if r[1] is not None and r[1] < 0.7]),
    ("normal     (0.7-1.2x)",[r[2] for r in rows if r[1] is not None and 0.7 <= r[1] < 1.2]),
    ("elevated   (> 1.2x)",  [r[2] for r in rows if r[1] is not None and r[1] >= 1.2]),
])
show("C. The PLTR combination — flat price AND dead volume", [
    ("flat price + volume < 0.7x", [r[2] for r in rows if -1 <= r[0] <= 1 and r[1] is not None and r[1] < 0.7]),
    ("everything else",            [r[2] for r in rows if not (-1 <= r[0] <= 1 and r[1] is not None and r[1] < 0.7)]),
])

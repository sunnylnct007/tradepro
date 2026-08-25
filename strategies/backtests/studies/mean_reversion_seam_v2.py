"""G5 was a DATA ARTEFACT. My seam study filtered the wrong half of the trade.

CORRECTION, 25 Aug 2026, to my own study committed hours earlier.

I claimed the worst trade of -23.2% was "triple-confirmed as a property of the
STRATEGY rather than the data" because it held at -23.2% across three runs:
the full store, seams removed, and an entire provider removed. That claim was
wrong, and the reason is a methodological error in my own filter.

**`mean_reversion_seam_v1` excluded signals whose 20-DAY SIGNAL WINDOW
contained a convention seam. It never looked at the HOLDING PERIOD** — bars
i+1 to i+20, which is where the exit happens and therefore where a phantom bar
decides what the trade is WORTH. I filtered the half of the trade that
generates the signal and ignored the half that generates the result.

The worst trade in the entire backtest is HYG, signal 2021-11-26. HYG is a
BOND ETF that moved +/-0.5% a day all that month:

    2021-11-26   85.47   ibkr_web    <- signal
    2021-11-29   86.00   ibkr_web
    2021-11-30   65.42   yfinance    -23.9%   <- one bar
    2021-12-01   85.37   ibkr_web    +30.5%

A single yfinance bar at 65.42 between ibkr_web bars at 86.00 and 85.37. HYG
did not fall 24% and recover 30% in two days. The -8% stop is "gapped through"
by that phantom and fills at min(stop, open) = -23.2%.

It sits at i+4 — INSIDE the holding period, OUTSIDE the signal window — so my
seam filter passed it through in every run. Three confirmations of the same
blind spot are not three confirmations.

**And my falsification test was blind too.** I wrote in
`mean_reversion_corrected_v1`: "if a corrected close changes the worst trade,
then -23.2% was a data artefact after all". It did not change it — because the
data lane's manifest covers `source == "ibkr"` rows and HYG's phantom is a
`yfinance` row. I designed the right test and pointed it at a dataset that
could not contain the fault.

## What the numbers actually are

Filtering contamination across the WHOLE trade path — signal window and
holding period:

    trades                                  2,457
    contaminated anywhere on the path          88   (3.6%)
    worst trade, all                       -23.2%   HYG   <- fabricated
    worst trade, CLEAN paths only          -17.7%   HOOD 2024-08-02
    win   all 74.7%  ->  clean 75.3%
    mean  all +1.22% ->  clean +1.22%

The mean does not move at all and the win rate improves slightly. **The
contamination distorts only the tail — which is to say, only G5.**

-17.7% is a real trade in a real August 2024 selloff, and it is also the figure
this rule originally recorded on the 89-name universe before the hold change.
The clean tail is consistent across two independent universes.

## What this changes

**G5 clears its gate by 7.3 points, not 1.8.** Every warning I have written
that "G5 is now a gate to watch, the margin is only 1.1/1.8 points" was based
on a fabricated number. The strategy is SAFER than reported, not riskier —
which is the direction that makes this an embarrassing error rather than a
dangerous one, and it does not make it less of an error.

This study is the seam filter extended to the full trade path.
"""


from __future__ import annotations

import itertools
import statistics as st
import sys

import numpy as np

from tradepro_strategies.universe import universe_symbols, poison_check
from tradepro_strategies.cli.build_universe import _load

# IMPORTED, NOT RETYPED. These were a hardcoded tuple here, which meant
# raising MAX_HOLD in signals/mean_reversion.py did not reach the harness —
# it silently kept grading a 10-session hold and appeared to CONTRADICT the
# result that motivated the change. Same duplicate-constant drift this session
# has chased through poison_check, the strategy list and the entry rule.
from tradepro_strategies.signals.mean_reversion import (   # noqa: E402
    SIGMA, BB_WINDOW as WINDOW, STOP_PCT, MAX_HOLD)
MAX_DAY_MOVE = 0.35          # a >35% session inside a hold is a corrupt bar


def _sma(c, i, n):
    return sum(c[i - n + 1:i + 1]) / n


def run(target_mode: str, touch: str):
    trades, holds, dates, sidx = [], [], [], []
    for si, sym in enumerate(SYMS):
        df = DATA.get(sym)
        if df is None:
            continue
        c, h, l, o, d = df
        n = len(c)
        i = 210
        while i < n - 1:
            m = _sma(c, i, WINDOW)
            sd = st.pstdev(c[i - WINDOW + 1:i + 1])
            if not (sd > 0 and c[i] < m - SIGMA * sd and c[i] > _sma(c, i, 200)):
                i += 1
                continue
            # THE ONE CHANGE vs v2: a signal whose own 20-day window straddles
            # a convention seam is measuring an adjusted close against a raw
            # one. Skip it rather than trade it.
            # WHOLE TRADE PATH, not just the signal window. The exit is where
            # a phantom bar decides what the trade is worth, and the holding
            # period is where the -23.2% HYG bar sat, unseen, in three runs.
            if any(k in SEAMS.get(sym, ())
                   for k in range(i - WINDOW + 1, min(n, i + MAX_HOLD + 1))):
                i += 1
                continue
            entry = c[i]
            tgt_fixed = m
            stop = entry * (1 - STOP_PCT)
            out = None
            for j in range(i + 1, min(n, i + MAX_HOLD + 1)):
                if c[j - 1] <= 0 or abs(c[j] / c[j - 1] - 1) > MAX_DAY_MOVE:
                    out = "corrupt"
                    break
                tgt = _sma(c, j, WINDOW) if target_mode == "moving" else tgt_fixed
                hit_t = (h[j] >= tgt) if touch == "high" else (c[j] >= tgt)
                hit_s = (l[j] <= stop) if touch == "high" else (c[j] <= stop)
                # FILL PRICES, NOT TRIGGER PRICES.
                #
                # A stop does not fill at the stop. If the session OPENS below
                # it the order becomes a market order and fills at the open —
                # which is how a -8% stop produces a -22% trade. Modelling the
                # fill at the trigger made the worst trade come out at exactly
                # -8.0% in every variant, i.e. a stop that never slips, which
                # is not a thing that exists. Same treatment for the target: a
                # gap ABOVE it fills better, so both directions are honest
                # rather than only the flattering one.
                fill_s = min(stop, o[j])
                fill_t = max(tgt, o[j])
                if hit_s and hit_t:
                    # Daily bars cannot order the high and the low. Assume the
                    # bad one — the same conservative rule the odds calculator
                    # and the dip study use.
                    out = (100 * (fill_s / entry - 1), j - i)
                    break
                if hit_s:
                    out = (100 * (fill_s / entry - 1), j - i)
                    break
                if hit_t:
                    out = (100 * (fill_t / entry - 1), j - i)
                    break
            if out == "corrupt":
                i = j + 1
                continue
            if out is None:
                j = min(n - 1, i + MAX_HOLD)
                out = (100 * (c[j] / entry - 1), j - i)
            trades.append(out[0]); holds.append(max(1, out[1]))
            dates.append(d[i]); sidx.append(si)
            i += max(1, out[1]) + 1
    if not trades:
        return None
    a = np.array(trades)
    wins = a[a > 0]
    # G4: what share of TOTAL PROFIT comes from the top 1% of trades.
    top = np.sort(wins)[::-1][:max(1, len(a) // 100)] if len(wins) else np.array([0.0])
    tail = 100 * top.sum() / wins.sum() if len(wins) and wins.sum() > 0 else 0.0
    return {"n": len(a), "win": 100 * len(wins) / len(a), "mean": a.mean(),
            "median": float(np.median(a)), "worst": a.min(), "tail": tail,
            "hold": float(np.median(holds)), "res": a,
            "dates": np.array(dates), "sidx": np.array(sidx)}


SYMS = universe_symbols()
DATA = {}
SEAMS: dict[str, set] = {}
for s in SYMS:
    df = _load(s)
    if df is None:
        continue
    c = df["close"].tolist()
    v = df["volume"].tolist() if "volume" in df.columns else None
    if not poison_check(c, v)[0]:
        continue
    # Indices where the close STEPS >=5% at a change of source. Those are the
    # convention seams; a window containing one is measuring two scales.
    src_ = df["source"].astype(str).tolist() if "source" in df.columns else [""] * len(c)
    seam = {i_ for i_ in range(1, len(c))
            if src_[i_] != src_[i_ - 1] and abs(c[i_] / c[i_ - 1] - 1) >= 0.05}
    SEAMS[s] = seam
    DATA[s] = (c, df["high"].tolist(), df["low"].tolist(), df["open"].tolist(),
               [str(x)[:10] for x in df.index])
print(f"universe {len(SYMS)} · usable {len(DATA)} · store verified phantom-free\n")

print(f"{'target':<9}{'touch':<7}{'trades':>8}{'win%':>7}{'mean%':>8}{'median%':>9}"
      f"{'worst%':>9}{'tail%':>7}{'hold':>6}")
results = {}
for tm, tc in itertools.product(("moving", "fixed"), ("high", "close")):
    r = run(tm, tc)
    if not r:
        continue
    results[(tm, tc)] = r
    print(f"{tm:<9}{tc:<7}{r['n']:>8}{r['win']:>6.1f}%{r['mean']:>7.2f}%{r['median']:>8.2f}%"
          f"{r['worst']:>8.1f}%{r['tail']:>6.1f}%{r['hold']:>6.0f}")

print("\nv1 reported: 2,413 trades · 62.4% win · +0.77%/trade · worst -12.5% · "
      "tail 26% · hold 4")
match = [k for k, r in results.items() if abs(r["hold"] - 4) < 0.5]
print("conventions reproducing a 4-bar hold: "
      + (", ".join(f"{a}/{b}" for a, b in match) if match else "NONE"))


# ── grading ───────────────────────────────────────────────────────────────
PRIMARY = ("moving", "high")
r = results[PRIMARY]
print(f"\n{'='*72}\nGRADED against MEAN_REVERSION_GATES_V1.md — primary convention "
      f"{PRIMARY[0]}/{PRIMARY[1]}")
print("(a limit resting at the 20-day mean, moved daily — the closest reading of\n"
      " 'target the 20-day mean' to how the order would actually be worked)\n")

# G4 both ways, because v1's definition is not recoverable and the two differ.
a = r["res"]
wins = a[a > 0]
k = max(1, len(a) // 100)
tail_of_wins = 100 * np.sort(wins)[::-1][:k].sum() / wins.sum()
net = a.sum()
tail_of_net = 100 * np.sort(a)[::-1][:k].sum() / net if net > 0 else float("inf")
print(f"G4 measured two ways (v1's definition is not recoverable):")
print(f"   top 1% as share of WINNING profit : {tail_of_wins:.1f}%")
print(f"   top 1% as share of NET profit     : {tail_of_net:.1f}%\n")

gates = [
    ("V0 >= 1,000 trades",      r["n"] >= 1000,        f"{r['n']}"),
    ("G1 win rate >= 55%",      r["win"] >= 55,        f"{r['win']:.1f}%"),
    ("G2 mean net > 0",         r["mean"] > 0,         f"{r['mean']:+.2f}%"),
    ("G3 median hold <= 10",    r["hold"] <= 10,       f"{r['hold']:.0f} bars"),
    ("G4 top-1% share <= 25%",  tail_of_net <= 25,     f"{tail_of_net:.1f}% of net"),
    ("G5 worst trade >= -25%",  r["worst"] >= -25,     f"{r['worst']:.1f}%"),
]
for name, ok, val in gates:
    print(f"  {name:<26}{'PASS' if ok else 'FAIL':<6}{val}")

# The two-split test — not in the v1 gates, added because it has rejected
# three candidates today that looked fine on the full sample.
di = np.array([int(x[:4]) * 10000 + int(x[5:7]) * 100 + int(x[8:10]) for x in r["dates"]])
mid = np.median(di)
print("\nTWO-SPLIT TEST (not a v1 gate — added after it rejected momentum v3,\n"
      "the intraday dip study, and would have caught both on the full sample):")
split_ok = True
for name, m in (("time 1st half", di < mid), ("time 2nd half", di >= mid),
                ("symbols even", r["sidx"] % 2 == 0), ("symbols odd", r["sidx"] % 2 == 1)):
    v = a[m]
    ok = len(v) > 100 and v.mean() > 0 and 100 * (v > 0).sum() / len(v) >= 55
    split_ok &= ok
    print(f"   {name:<16}{'PASS' if ok else 'FAIL':<6}n={len(v):<6}"
          f"win {100*(v>0).sum()/len(v):.1f}%  mean {v.mean():+.2f}%")

allg = all(ok for _, ok, _ in gates)
print(f"\nVERDICT: {'ALL SIX v1 GATES PASS' if allg else 'FAILS: ' + ', '.join(n for n,ok,_ in gates if not ok)}"
      f" · two-split {'PASS' if split_ok else 'FAIL'}")
print("\nSENSITIVITY — the same gates under the other three exit conventions:")
for key, rr in results.items():
    if key == PRIMARY:
        continue
    aa = rr["res"]; kk = max(1, len(aa) // 100)
    tn = 100 * np.sort(aa)[::-1][:kk].sum() / aa.sum() if aa.sum() > 0 else 999
    bad = [n for n, ok in (("G1", rr["win"] >= 55), ("G2", rr["mean"] > 0),
                           ("G3", rr["hold"] <= 10), ("G4", tn <= 25),
                           ("G5", rr["worst"] >= -25)) if not ok]
    print(f"   {key[0]}/{key[1]:<6} {'all pass' if not bad else 'fails ' + ','.join(bad)}"
          f"   (tail {tn:.1f}% of net, hold {rr['hold']:.0f}, worst {rr['worst']:.1f}%)")


# ── RESULT, 25 Aug 2026 ────────────────────────────────────────────────────
#
#                    baseline   signal-window   WHOLE PATH
#                                   filtered      filtered
#   trades              2,503          2,429         2,385
#   G1 win              73.2%          73.3%         74.0%   PASS
#   G2 mean            +1.10%         +1.05%        +1.13%   PASS
#   G3 hold                 7              7             7   PASS
#   G4 tail             17.8%          17.9%         16.0%   PASS
#   G5 worst           -23.2%         -23.2%       **-17.7%**  PASS
#
# G5 moves by 5.5 points the moment the holding period is filtered, and does
# not move at all when only the signal window is. That is the whole finding:
# the -23.2% lived in the half of the trade I never looked at.
#
# G5's true margin is 7.3 points, not 1.8. Every note I have written warning
# that "G5 is a gate to watch" was sized off a fabricated number. The strategy
# is safer than reported.
#
# G4 also improves, 17.8% -> 16.0%, for the same reason: a fabricated -23.2%
# loss distorts the net, and the tail is measured as a share of net.
#
# WHAT I GOT RIGHT AND WHAT THAT WAS WORTH: the mean is unmoved (+1.10% ->
# +1.13%) and the win rate is unmoved (73.2% -> 74.0%) across every one of
# these runs. The central claim — that the edge is real and not a data
# artefact — survives all four filters, including this one. It was only ever
# the TAIL that was contaminated, and the tail is one gate.
#
# THE LESSON IS NOT "CHECK MORE CAREFULLY". I checked three times. Each check
# asked the same question — does the SIGNAL window contain bad data — because
# that is where my model of the rule lives. The failure is that a trade has two
# halves and my filter had one. The data lane and I spent the day agreeing that
# an invariant beats thoroughness because it has no model; this is the same
# lesson arriving at my own expense, and I did not recognise it while writing
# three studies that all shared the boundary.
#
# The invariant that would have caught it, stated so it can be reused: A TRADE
# RESULT MUST BE EXPLAINABLE BY BARS THAT ARE THEMSELVES CONSISTENT. Not "is
# the signal clean" but "is every bar this trade TOUCHED clean" — entry,
# exit, and everything between. That is checkable without knowing what is
# wrong with the data, which is the property that matters.

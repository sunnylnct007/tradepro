"""Swing re-graded on CORRECTED seam bars — the precise version.

25 Aug 2026, and the last approximation removed.

The chain: my `mean_reversion_seam_v1` filtered signal WINDOWS and missed a
phantom in the HOLDING period, which cost me a false claim that G5's -23.2%
was a property of the strategy. `seam_v2` fixed the filter by EXCLUDING any
trade touching a source-change step anywhere on its path — blunt, and it threw
away 118 trades to remove a handful of bad bars.

The data lane then isolated the actual bars (ISOLATED_SEAM_BARS.json), and I
found 28 of their first 71 were the COVID crash rather than seams — same-source
neighbours, so no convention boundary existed to produce a spike. They
regenerated: **43 rows, 8 symbols (HYG, SCHD, XLRE, VXUS, VLUE, USMV, QUAL,
VOO), every one yfinance isolated among ibkr_web, every implied factor below
1.0**, and it passes verify_seam_manifest.py.

So this run CORRECTS those 43 closes to the raw scale rather than excluding the
trades that touch them. No trade is discarded and no data is invented: the
correction is stored / implied_factor, which is the adjusted price divided by
its own dividend factor.

PRE-REGISTERED, before running:

  * All six gates hold. Three filters have already passed; a correction is
    gentler than any of them.
  * The trade count returns to roughly the 2,503 baseline, NOT seam_v2's
    2,385 — correcting a bar keeps its trade.
  * **G5 is the test.** It printed -23.2% on the raw store, -23.2% under a
    signal-window filter, and -17.7% once the whole path was filtered. If the
    corrected run also gives -17.7%, that number is earned on data that is
    RIGHT rather than data that is ABSENT, which is the stronger claim and the
    one I could not make this morning. If it gives something else, my -17.7%
    was an artefact of exclusion and I will have been wrong about G5 twice in
    one day.
  * I expect the mean to move by almost nothing. 43 bars out of 571,254.

This is also the closest available preview of the post-migration re-grade the
data lane has made a hard requirement of the store repair.
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
CORRECTED = [0]

# The data lane's manifest: authoritative api_close per symbol-date.
import json as _json
from pathlib import Path as _Path
_mf = _Path(__file__).resolve().parents[3] / "ISOLATED_SEAM_BARS.json"
_raw = _json.loads(_mf.read_text())
_rows = _raw if isinstance(_raw, list) else (
    _raw.get("rows") or _raw.get("bars") or next(iter(_raw.values())))
BAD: dict[str, dict[str, float]] = {}
for _r in _rows:
    # stored / implied_factor puts an ADJUSTED close back on the raw scale
    # the surrounding ibkr_web bars use.
    BAD.setdefault(_r["symbol"], {})[_r["date"]] = float(_r["implied_factor"])
print(f"manifest: {len(_rows)} isolated seam bars across {len(BAD)} symbols")
for s in SYMS:
    df = _load(s)
    if df is None:
        continue
    c = df["close"].tolist()
    hi_ = df["high"].tolist(); lo_ = df["low"].tolist(); op_ = df["open"].tolist()
    v = df["volume"].tolist() if "volume" in df.columns else None
    if not poison_check(c, v)[0]:
        continue
    # CORRECT the isolated seam bars in memory — ALL FOUR PRICES.
    #
    # A seam bar is dividend-adjusted in open, high, low and close alike, and
    # the harness checks the stop against the LOW. Correcting only the close
    # leaves the phantom low in place, and the first version of this study did
    # exactly that: HYG's corrected close moved -0.10% on 2021-09-30 while its
    # uncorrected low still gapped the stop, producing a -23.9% trade on a day
    # nothing happened. A partial correction looks identical to a complete one.
    fix = BAD.get(s)
    if fix:
        dts = [str(x)[:10] for x in df.index]
        for i_, dt_ in enumerate(dts):
            f = fix.get(dt_)
            if f:
                c[i_] /= f; hi_[i_] /= f; lo_[i_] /= f; op_[i_] /= f
                CORRECTED[0] += 1
    SEAMS[s] = set()
    DATA[s] = (c, hi_, lo_, op_, [str(x)[:10] for x in df.index])
print(f"universe {len(SYMS)} · usable {len(DATA)} · {CORRECTED[0]} closes corrected from the manifest\n")

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
#                     raw store   signal-window   whole-path   CORRECTED
#                                    filtered       filtered     (this)
#   trades              2,503          2,429          2,385       2,508
#   G1 win              73.2%          73.3%          74.0%       73.2%
#   G2 mean            +1.10%         +1.05%         +1.13%      +1.08%
#   G3 hold                 7              7              7           7
#   G4 tail             17.8%          17.9%          16.0%       17.5%
#   G5 worst           -23.2%         -23.2%         -17.7%      -17.7%
#
# **G5 = -17.7% is now confirmed on data that is RIGHT rather than data that is
# ABSENT.** That is the claim I could not make this morning, and it is the one
# the pre-registered test was written to decide. Trade count returns to 2,508
# against the 2,503 baseline, as predicted — correcting a bar keeps its trade
# where excluding it does not. The mean moves 0.02 points.
#
# The worst six are all real, clean trades: HOOD -17.7% (Aug 2024 selloff),
# CRWD -17.0% (the July 2024 outage), META -14.9%, MTUM -12.5% (Feb 2020),
# TER -12.4%, NKE -12.2%.
#
# BUT THE FIRST VERSION OF THIS STUDY SAID -23.9%, AND I NEARLY REPORTED IT.
#
# I had pre-registered that a corrected run giving anything other than -17.7%
# meant my earlier figure was an artefact and I had been wrong about G5 twice.
# The first run returned -23.9% — worse than the raw store — and by my own
# written test that was a falsification.
#
# It was not. **I corrected the CLOSE and nothing else.** A seam bar is
# dividend-adjusted in open, high, low and close alike, and this harness checks
# the stop against the LOW. So HYG's corrected close moved -0.10% on 2021-09-30
# while its uncorrected low still gapped the stop, and the fill was taken at
# min(stop, open) on an uncorrected open — a -23.9% trade on a day when nothing
# happened. Correcting all four gives -17.7%.
#
# A PARTIAL CORRECTION LOOKS EXACTLY LIKE A COMPLETE ONE. Nothing in the output
# said "close only". The series I inspected — closes — was visibly, correctly
# smooth, which is precisely why I believed it. Had I trusted my own
# pre-registration mechanically I would have published a false retraction of a
# true result, on the strength of a test I designed correctly and then fed with
# a half-fixed dataset.
#
# That is the third time today the same shape has caught me: the signal-window
# filter that never looked at the holding period, the falsification test
# pointed at a manifest that could not contain the fault, and now a correction
# applied to one of four series. Each time the method was right and the INPUT
# was narrower than the method assumed.
#
# CARRIED CAVEAT: mean_reversion_corrected_v1 has the same limitation and
# cannot be fixed the same way — the ibkr bad-bar manifest supplies api_close
# only, with no api_open/high/low, so its trades still run on uncorrected lows.
# Its "all six gates pass" therefore stands as a bound rather than a
# measurement, and the data lane would need OHLC in that manifest to close it.

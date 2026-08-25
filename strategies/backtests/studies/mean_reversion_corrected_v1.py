"""Re-grade Swing on CORRECTED closes, not on a deleted provider.

The strongest version of this morning's question, and the last one available
before the store is actually repaired.

The previous study (mean_reversion_ibkr_source_v1) answered "do the bad closes
hold the result up?" by deleting every `ibkr`-sourced bar — 84,555 of them, a
strict superset of the fault. All six gates survived, which was conclusive in
one direction but blunt: 83,517 good bars were destroyed to remove the bad
ones, and it could say nothing about what the gates look like on data that is
RIGHT rather than data that is ABSENT.

The data lane then shipped the actual list (180b1bc): `BAD_BARS_IBKR_SOCKET.json`,
7,037 rows across 120 of 244 symbols, 2022-01-03 to 2026-05-29, each with the
authoritative `api_close` — what IBKR returns today for the same instrument-date,
and which agrees with parquet to 0.000% on every `ibkr_web` bar.

Note their own correction: they first reported 1,038 rows from a 40-symbol
sample. The true figure across all 244 is 7,037. The first number was too
SMALL, which is the opposite of the day's other three errors and the reason
the previous study's superset approach was the right instinct at the time.

So this run does the thing the deletion could not: it OVERWRITES each listed
close with `api_close` in memory and grades on the corrected series. Nothing
is written to the store — the forward-test freeze holds — but this is a
faithful preview of the post-migration re-grade the data lane has now made a
hard requirement of the repair plan.

PRE-REGISTERED, before running:

  * I expect all six gates to hold. Two independent exclusions have already
    passed, and a correction is gentler than a deletion.
  * I expect the TRADE COUNT to sit near the 2,503 baseline rather than the
    2,205 of the deletion run, because corrected bars still produce trades.
  * The number I am actually watching is G5. It has printed -23.2% in all
    three runs so far — full store, seams removed, provider removed — and I
    have argued that makes it a property of the strategy. **This is the run
    that can falsify that.** If a corrected close changes the worst trade,
    then -23.2% was a data artefact after all and my triple-confirmation
    was three views of the same bad bar surviving in all of them. If it holds
    at -23.2% on CORRECTED data, the claim is properly earned.
  * I do NOT expect a large mean change. 7,037 rows is 1.2% of the universe's
    571,254 daily bars.

CAVEAT, carried from the data lane and not to be lost: 79 of the 244 symbols
have NO API coverage, so their parquet bars cannot be checked at all. This
manifest is a FLOOR. "Not in the manifest" does not mean "clean" for those
names, and this study cannot speak for them.
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
_mf = _Path(__file__).resolve().parents[3] / "BAD_BARS_IBKR_SOCKET.json"
_raw = _json.loads(_mf.read_text())
_rows = _raw if isinstance(_raw, list) else (
    _raw.get("rows") or _raw.get("bars") or next(iter(_raw.values())))
BAD: dict[str, dict[str, float]] = {}
for _r in _rows:
    BAD.setdefault(_r["symbol"], {})[_r["date"]] = float(_r["api_close"])
print(f"manifest: {len(_rows)} bad bars across {len(BAD)} symbols")
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
    # CORRECT the known-bad closes in memory. No exclusion at all this time.
    fix = BAD.get(s)
    if fix:
        dts = [str(x)[:10] for x in df.index]
        for i_, dt_ in enumerate(dts):
            if dt_ in fix:
                c[i_] = fix[dt_]
                CORRECTED[0] += 1
    SEAMS[s] = set()
    DATA[s] = (c, df["high"].tolist(), df["low"].tolist(), df["open"].tolist(),
               [str(x)[:10] for x in df.index])
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



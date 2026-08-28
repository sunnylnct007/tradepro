"""THE SIX GATES on fully corrected data — a RUNNABLE harness. 28 Aug 2026.

WHY THIS FILE EXISTS
--------------------
`mean_reversion_fully_corrected_v1.py` — the study that produced the currently
published headline numbers, including G5 = -21.3% — contains **fifty-four lines
of prose and ZERO lines of code.** It does not parse. It never could have run.

So the single most-quoted result behind the live Swing sleeve was not
reproducible, and nobody found out for three days, because a write-up committed
next to working studies is indistinguishable from one until you execute it.

This is that harness, rebuilt from `mean_reversion_seam_corrected_v1.py` (which
does run) with the second manifest applied, and it PRINTS THE GATES rather than
leaving a human to compare numbers against a document.

WHAT THIS CHECK CANNOT FAIL ON, carried with the result:
  * 79 of 244 symbols have no API coverage in the manifests, so nothing here
    speaks for their bars. It is a measurement over the covered set.
  * It sees only instrument-dates BOTH stores hold.
  * Corrections come from manifests captured 27 Aug; bars written since are
    uncorrected.

BOTH MANIFESTS, AND NEITHER ALONE IS ENOUGH
-------------------------------------------
    BAD_BARS_IBKR_SOCKET.json   25,178 rows / 121 symbols   source == "ibkr"
    ISOLATED_SEAM_BARS.json         43 rows /   8 symbols   yfinance seams

Correcting only ibkr rows left G5 at -23.2% (worst trade is HYG, a yfinance
seam); correcting only seams gave -17.7% (BROS still masked by a bad ibkr low).
Each manifest hides the other's worst case. All four OHLC fields are corrected,
because the harness checks the stop against the LOW and a partial correction
looks identical to a complete one.
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
CORRECTED = {"seam": 0, "ibkr": 0}

import json as _json
from pathlib import Path as _Path

_root = _Path(__file__).resolve().parents[3]


def _rows_of(path):
    raw = _json.loads(path.read_text())
    return raw if isinstance(raw, list) else (
        raw.get("rows") or raw.get("bars") or next(iter(raw.values())))


# Manifest 1 — yfinance dividend seams. `implied_factor` puts an ADJUSTED close
# back on the raw scale the surrounding ibkr_web bars use. Applied to all four
# fields: a seam bar is adjusted in open/high/low/close alike.
SEAM: dict[str, dict[str, float]] = {}
for _r in _rows_of(_root / "ISOLATED_SEAM_BARS.json"):
    SEAM.setdefault(_r["symbol"], {})[_r["date"]] = float(_r["implied_factor"])

# Manifest 2 — bars the IBKR socket wrote wrong, with the authoritative API
# values field by field. This is the one that unmasks BROS.
IBKR: dict[str, dict[str, dict]] = {}
for _r in _rows_of(_root / "BAD_BARS_IBKR_SOCKET.json"):
    api = _r.get("api") or {}
    if api:
        IBKR.setdefault(_r["symbol"], {})[_r["date"]] = api

print(f"manifests: {sum(len(v) for v in SEAM.values())} seam bars / {len(SEAM)} symbols · "
      f"{sum(len(v) for v in IBKR.values())} ibkr bars / {len(IBKR)} symbols")

for s in SYMS:
    df = _load(s)
    if df is None:
        continue
    c = df["close"].tolist()
    hi_ = df["high"].tolist(); lo_ = df["low"].tolist(); op_ = df["open"].tolist()
    v = df["volume"].tolist() if "volume" in df.columns else None
    if not poison_check(c, v)[0]:
        continue
    dts = [str(x)[:10] for x in df.index]

    fix = SEAM.get(s)
    if fix:
        for i_, dt_ in enumerate(dts):
            f = fix.get(dt_)
            if f:
                c[i_] /= f; hi_[i_] /= f; lo_[i_] /= f; op_[i_] /= f
                CORRECTED["seam"] += 1

    bad = IBKR.get(s)
    if bad:
        for i_, dt_ in enumerate(dts):
            a = bad.get(dt_)
            if not a:
                continue
            if a.get("open") is not None:  op_[i_] = float(a["open"])
            if a.get("high") is not None:  hi_[i_] = float(a["high"])
            if a.get("low") is not None:   lo_[i_] = float(a["low"])
            if a.get("close") is not None: c[i_] = float(a["close"])
            CORRECTED["ibkr"] += 1

    DATA[s] = (c, hi_, lo_, op_, dts)

print(f"universe {len(SYMS)} · usable {len(DATA)} · "
      f"corrected: {CORRECTED['seam']} seam + {CORRECTED['ibkr']} ibkr bars\n")

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

# ── GRADED against MEAN_REVERSION_GATES_V1.md ────────────────────────────
#
# The gate DEFINITIONS and the primary convention are taken verbatim from
# backtests/studies/mean_reversion_v2.py, which is the harness the gates were
# pre-registered for. I first wrote thresholds from memory here and got almost
# all of them wrong: V0 (>=1,000 trades) became "G1 >= 400", G4 became "share
# of trades below -10%" instead of TOP-1% PROFIT SHARE, and the graded
# convention became fixed/high instead of moving/high. Every error was in the
# generous direction, and it would have printed ALL SIX GATES PASS either way.
#
# A gate remembered is a gate moved.
PRIMARY = ("moving", "high")
r = results[PRIMARY]
print(f"\n{'='*72}\nGRADED against MEAN_REVERSION_GATES_V1.md — primary convention "
      f"{PRIMARY[0]}/{PRIMARY[1]}")
print("(a limit resting at the 20-day mean, moved daily — the closest reading of\n"
      " 'target the 20-day mean' to how the order would actually be worked)\n")

a = r["res"]
wins = a[a > 0]
k = max(1, len(a) // 100)
tail_of_wins = 100 * np.sort(wins)[::-1][:k].sum() / wins.sum()
net = a.sum()
tail_of_net = 100 * np.sort(a)[::-1][:k].sum() / net if net > 0 else float("inf")
print("G4 measured two ways (v1's definition is not recoverable):")
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
allpass = True
for name, ok, val in gates:
    allpass &= ok
    print(f"  {name:<26}{'PASS' if ok else 'FAIL':<6}{val}")

print(f"\n{'ALL SIX PASS' if allpass else 'AT LEAST ONE FAILS'} — on the covered set, "
      "with the docstring's caveats.")
raise SystemExit(0 if allpass else 1)

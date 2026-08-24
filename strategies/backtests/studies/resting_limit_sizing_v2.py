"""Resting limit at reduced size — graded against RESTING_LIMIT_SIZING_GATES_V2.md
(committed 6540686 BEFORE this run).

Measures the PORTFOLIO, not the trade: v1's R3 was a per-trade percentage and
percentages ignore position size, so it could not see whether smaller positions
buy a shallower drawdown.

Trades are placed on a real timeline with real overlap — a position occupies
capital from entry to exit, and a new signal is skipped if the capital is not
free. That overlap is the whole point: 6,748 trades cannot all be taken at 5%
each without exceeding the account.
"""
from __future__ import annotations
import statistics as st
import numpy as np
from tradepro_strategies.universe import universe_symbols, poison_check
from tradepro_strategies.cli.build_universe import _load
from tradepro_strategies.signals.mean_reversion import (
    SIGMA, BB_WINDOW, TREND_WINDOW, MAX_HOLD, STOP_PCT)
MAXDAY = 0.35


def sma(c, i, n):
    return sum(c[i - n + 1:i + 1]) / n


DATA = {}
for _s in universe_symbols():
    _df = _load(_s)
    if _df is None or "open" not in _df.columns:
        continue
    _c = _df["close"].tolist()
    _v = _df["volume"].tolist() if "volume" in _df.columns else None
    if not poison_check(_c, _v)[0]:
        continue
    DATA[_s] = (_c, _df["high"].tolist(), _df["low"].tolist(), _df["open"].tolist(),
                [str(x)[:10] for x in _df.index])


def collect(mode):
    """(entry_date, exit_date, pct) for every trade the variant would take."""
    out = []
    for _sym, (c, h, l, o, d) in DATA.items():
        n = len(c); i = 210
        while i < n - 2:
            w = c[i - BB_WINDOW + 1:i + 1]; sd = st.pstdev(w)
            if sd <= 0 or c[i] <= sma(c, i, TREND_WINDOW):
                i += 1; continue
            lvl = (sum(w) / BB_WINDOW) - SIGMA * sd
            if mode == "control":
                if not (c[i] < lvl): i += 1; continue
                entry = o[i + 1]
            else:
                if not (l[i + 1] <= lvl <= h[i + 1]): i += 1; continue
                if c[i + 1] <= sma(c, i + 1, TREND_WINDOW): i += 1; continue
                entry = lvl
            stop = entry * (1 - STOP_PCT); r = None; j = i + 1
            for j in range(i + 1, min(n - 1, i + 1 + MAX_HOLD) + 1):
                if c[j - 1] <= 0 or abs(c[j] / c[j - 1] - 1) > MAXDAY: break
                tgt = sma(c, j, BB_WINDOW)
                if l[j] <= stop: r = 100 * (min(stop, o[j]) / entry - 1); break
                if h[j] >= tgt: r = 100 * (max(tgt, o[j]) / entry - 1); break
            if r is None:
                j = min(n - 1, i + 1 + MAX_HOLD); r = 100 * (c[j] / entry - 1)
            out.append((d[i + 1], d[j], r, _sym)); i = j + 1
    out.sort()
    return out


def portfolio(trades, size_pct):
    """Event-driven equity curve, marked only at well-defined points.

    Two accounting bugs preceded this and both produced obviously-wrong
    numbers, which is the only reason they were caught:

      1. P&L credited at ENTRY rather than exit — compounds a gain before it
         is earned. Gave a 2,384% return.
      2. Equity marked INSIDE the settle loop, while the open-position list
         was half-rebuilt, so equity briefly looked near-zero. Gave a 95%
         drawdown on the control, which does not have one.

    Now: settle every due exit FIRST, then mark equity once, then decide.
    Capital is committed at entry and released with its P&L at exit; a signal
    with no free cash is SKIPPED, as a real account would.
    """
    START = 100000.0
    cash = START
    open_pos = []                    # (exit_date, committed, pct)
    peak = START
    maxdd = 0.0
    taken = skipped = 0
    max_util = 0.0

    for ent, exi, pct, _sy in trades:
        # 1. settle everything that has exited by now
        due = [p for p in open_pos if p[0] <= ent]
        open_pos = [p for p in open_pos if p[0] > ent]
        for _, com, pc in due:
            cash += com * (1 + pc / 100.0)
        # 2. mark ONCE, with the book in a consistent state
        committed = sum(p[1] for p in open_pos)
        equity = cash + committed
        peak = max(peak, equity)
        maxdd = min(maxdd, 100 * (equity - peak) / peak)
        max_util = max(max_util, 100 * committed / equity if equity > 0 else 0.0)
        # 3. take the trade if the cash is there
        want = equity * size_pct
        if want > cash:
            skipped += 1
            continue
        cash -= want
        open_pos.append((exi, want, pct))
        taken += 1

    for _, com, pc in open_pos:
        cash += com * (1 + pc / 100.0)
    equity = cash
    peak = max(peak, equity)
    maxdd = min(maxdd, 100 * (equity - peak) / peak)
    return {"ret": 100 * (equity / START - 1), "dd": maxdd, "taken": taken,
            "skipped": skipped, "util": max_util}


ctrl_t = collect("control")
lim_t = collect("limit_e")
print(f"control trades {len(ctrl_t):,} · variant E trades {len(lim_t):,}\n")
print(f"{'variant':<28}{'size':>7}{'taken':>8}{'skipped':>9}{'return%':>10}{'maxDD%':>9}{'ret/DD':>8}{'util%':>8}")
res = {}
for lbl, tr, sz in (("A control", ctrl_t, 0.05), ("B variant E", lim_t, 0.05),
                    ("C variant E half", lim_t, 0.025), ("D variant E third", lim_t, 0.0167)):
    r = portfolio(tr, sz); res[lbl] = r
    rd = r["ret"] / abs(r["dd"]) if r["dd"] else float("inf")
    print(f"{lbl:<28}{sz*100:>6.1f}%{r['taken']:>8}{r['skipped']:>9}"
          f"{r['ret']:>9.0f}%{r['dd']:>8.1f}%{rd:>8.1f}{r['util']:>7.0f}%")
a = res["A control"]; a_rd = a["ret"] / abs(a["dd"])
print(f"\nGATES (control: {a['ret']:.0f}% return, {a['dd']:.1f}% max DD, ret/DD {a_rd:.1f})\n")
for lbl in ("B variant E", "C variant E half", "D variant E third"):
    r = res[lbl]; rd = r["ret"] / abs(r["dd"]) if r["dd"] else 0
    g = {"S1 return": r["ret"] >= a["ret"], "S2 maxDD": abs(r["dd"]) <= abs(a["dd"]),
         "S3 ret/DD": rd >= a_rd, "S4 util<=100": r["util"] <= 100}
    bad = [k for k, v in g.items() if not v]
    print(f"  {lbl:<26}{'passes S1-S4' if not bad else 'FAIL — ' + ', '.join(bad)}")


# ── S5: the two-split. The gate that killed momentum v3, the intraday dip
# study, the analog evaluator and the resting-limit v1 variants. Return AND
# drawdown must beat the control in every cell, on the SAME split of the data.
print("\nS5 — two-split (return and drawdown vs the control in each cell)\n")


def split_cells(trades):
    di = [int(t[0][:4]) for t in trades]
    mid = sorted(di)[len(di) // 2]
    syms = {}
    # symbol identity is not carried in the tuple, so split on entry-date parity
    # as an independent second cut — a different partition of the same data.
    return {
        "time 1st half": [t for t in trades if int(t[0][:4]) < mid],
        "time 2nd half": [t for t in trades if int(t[0][:4]) >= mid],
        # THE PRE-REGISTERED SECOND CUT: by SYMBOL, not by date parity.
        # A first pass substituted odd/even entry dates because the trade
        # tuples did not carry the symbol. That is a different, weaker test —
        # date parity splits the same symbols across both cells, so it cannot
        # detect an effect that lives in a subset of names. Corrected rather
        # than reported as a pass.
        "symbols A-M": [t for t in trades if t[3][0] <= "M"],
        "symbols N-Z": [t for t in trades if t[3][0] > "M"],
    }


ctrl_cells = split_cells(ctrl_t)
lim_cells = split_cells(lim_t)
for lbl, sz in (("C variant E half", 0.025), ("D variant E third", 0.0167)):
    ok = True
    print(f"  {lbl}:")
    for cell in ctrl_cells:
        ca = portfolio(ctrl_cells[cell], 0.05)
        va = portfolio(lim_cells[cell], sz)
        good = va["ret"] >= ca["ret"] and abs(va["dd"]) <= abs(ca["dd"])
        ok &= good
        print(f"     {cell:<16}{'PASS' if good else 'FAIL'}   "
              f"ret {va['ret']:>7.0f}% vs {ca['ret']:>6.0f}%   "
              f"DD {va['dd']:>6.1f}% vs {ca['dd']:>6.1f}%")
    print(f"     -> S5 {'PASS' if ok else 'FAIL'}\n")

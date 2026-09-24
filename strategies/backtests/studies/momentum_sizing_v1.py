"""MOMENTUM_SIZING_GATES_V1 — does the gap tail survive at portfolio level?

Gates frozen in MOMENTUM_SIZING_GATES_V1.md BEFORE this ran.

REUSES the swing sizing study's `replay` rather than restating it, so the two
sleeves are compared by the same code and a difference in result is a
difference in the strategy. Signals come from the LIVE paper module
(signals.momentum_pullback), which imports the screen's own entry — so this
grades the thing that would actually trade, not a third copy of it.
"""
import os
import sys
from collections import defaultdict

sys.path.insert(0, ".")
from backtests.studies.swing_sizing_v1 import replay  # noqa: E402
from tradepro_strategies.cli.momentum_candidates import (  # noqa: E402
    BASE_DIR, _load, _tradeable, poison_check)
from tradepro_strategies.signals import momentum_pullback as M  # noqa: E402

MAX_DAY_MOVE = 0.35
CAPITAL = 150_000.0
MAX_OPEN = 15
SIZES = (0.02, 0.03, 0.05, 0.08, 0.10)
G1_DD, G2_DAY, G4_EXPO = 25.0, 10.0, 80.0


def signals_by_date() -> tuple[dict, list]:
    by_date = defaultdict(list)
    worst = []
    for sym in sorted(os.listdir(BASE_DIR)):
        if not _tradeable(sym):
            continue
        df = _load(sym)
        if df is None or "volume" not in df.columns:
            continue
        c = df["close"].tolist()
        d = [str(x)[:10] for x in df.index]
        if not poison_check(c)[0]:
            continue
        n = len(c)
        i = 210
        while i < n - 1:
            if not M.entry_signal(c, i):
                i += 1
                continue
            entry = c[i]
            j = i + 1
            exit_i = None
            bad = False
            while j <= min(n - 1, i + M.MAX_HOLD):
                if c[j - 1] > 0 and abs(c[j] / c[j - 1] - 1) > MAX_DAY_MOVE:
                    bad = True
                    break
                if M.exit_decision(c, j, fill_price=entry, bars_held=j - i)[0]:
                    exit_i = j
                    break
                j += 1
            if bad:
                i = j + 1
                continue
            if exit_i is None:
                if j > i + M.MAX_HOLD:
                    exit_i = min(n - 1, i + M.MAX_HOLD)
                else:
                    break
            ret = 100 * (c[exit_i] / entry - 1)
            by_date[d[i]].append({"sym": sym, "ret": ret,
                                  "days": exit_i - i, "exit": d[exit_i]})
            worst.append((ret, sym, d[i]))
            i = exit_i + 1
    return by_date, worst


def main() -> int:
    by_date, trades = signals_by_date()
    dates = sorted(by_date)
    n = sum(len(v) for v in by_date.values())
    c2008 = sum(len(v) for k, v in by_date.items() if k.startswith("2008"))
    c2020 = sum(len(v) for k, v in by_date.items() if "2020-02" <= k <= "2020-04")
    print(f"{n} signals, {dates[0]} -> {dates[-1]}")
    print(f"G0: {c2008} signals in 2008 · {c2020} in the Feb-Apr 2020 crash\n")

    print(f"{'size':>6}{'final':>12}{'return':>10}{'max DD':>9}"
          f"{'worst day':>11}{'peak expo':>11}")
    rows = {}
    for pct in SIZES:
        r = replay(by_date, pct, MAX_OPEN, CAPITAL)
        rows[pct] = r
        print(f"{pct:>5.0%}{r['final']:>12,.0f}{r['ret_pct']:>9.0f}%"
              f"{r['max_dd']:>8.1f}%{r['worst_day']:>10.1f}%{r['peak_expo']:>10.0f}%")

    live = rows[0.05]
    worst_trade = min(t[0] for t in trades)
    # G5-here: can ONE position's tail alone breach the worst-day bar?
    one_pos_hit = abs(worst_trade) * 0.05
    print("\n— gates (frozen in MOMENTUM_SIZING_GATES_V1.md), judged at 5% —")
    checks = (
        ("G0 traded both crises", c2008 > 0 and c2020 > 0, f"{c2008} / {c2020}"),
        (f"G1 max DD <= {G1_DD}%", live["max_dd"] <= G1_DD, f"{live['max_dd']:.1f}%"),
        (f"G2 worst day <= {G2_DAY}%", abs(live["worst_day"]) <= G2_DAY,
         f"{live['worst_day']:.1f}%"),
        ("G3 return positive", live["ret_pct"] > 0, f"{live['ret_pct']:.0f}%"),
        (f"G4 peak expo <= {G4_EXPO}%", live["peak_expo"] <= G4_EXPO,
         f"{live['peak_expo']:.0f}%"),
        (f"G5 one position cannot breach {G2_DAY}%", one_pos_hit <= G2_DAY,
         f"worst trade {worst_trade:.1f}% x 5% = {one_pos_hit:.2f}% of equity"),
    )
    for label, ok, got in checks:
        print(f"  {label:38} {'PASS' if ok else 'FAIL'}  ({got})")
    print(f"\n  VERDICT at 5%: {'ALL PASS' if all(c[1] for c in checks) else 'FAILS'}")
    print("\nSwing, same harness, for comparison:")
    print("  5% -> +90% return, 23.3% max DD, -6.5% worst day, 77% peak exposure")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

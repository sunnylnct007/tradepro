"""Momentum + index and sector filter — graded against
MOMENTUM_INDEX_SECTOR_GATES_V1.md (committed e975a3f, before this ran).

FIRST PASS. Reads daily candles saved from the TradePro `get_candles`
endpoint (one JSON file per symbol in a directory), NOT the golden parquet
store, because the session that ran it could not reach the Mac's bar cache.
The gates doc states that limit; do not quote these numbers as the screen's
evidence. A confirmation run on the golden store needs only `load_series`
swapped for `daily_bars.load_daily`.

The v2 entry and exit are copied VERBATIM from
`cli/momentum_candidates._entry_signal` and the `mom_v3.py` trade loop, so
the control here is the same rule on different bars. They are copied rather
than imported so this file runs with no repo install and no bar store.

Usage:  python momentum_index_sector_v1.py <candles_dir> <tradeable.json>
"""
import glob
import json
import os
import statistics as st
import sys
from bisect import bisect_right
from collections import defaultdict

STOP_PCT, TRAIL_PCT, MAX_HOLD = 0.08, 0.08, 60   # v2 constants, unchanged
RS_LOOKBACK = 63                                  # relative_context.py's lookback
MIN_BENCH_BARS = 210

GICS_ETF = {
    "Information Technology": "XLK", "Financials": "XLF", "Health Care": "XLV",
    "Industrials": "XLI", "Consumer Discretionary": "XLY",
    "Consumer Staples": "XLP", "Utilities": "XLU", "Real Estate": "XLRE",
    "Materials": "XLB", "Communication Services": "XLC", "Energy": "XLE",
}
BENCH = {"SPY"} | set(GICS_ETF.values())
ETFS = {"AGG", "DIA", "EEM", "EFA", "GDX", "GLD", "HYG", "ITA", "IVV", "IWM",
        "KRE", "QQQ", "SLV", "SOXX", "SPY", "TLT", "USO", "VOO", "VTI",
        "VXUS"} | set(GICS_ETF.values())


def sma(c, i, n):
    return sum(c[i - n + 1:i + 1]) / n if i >= n - 1 else 0.0


def _entry_signal(c, i):
    """v2 entry at bar i — verbatim from momentum_candidates._entry_signal."""
    if i < 210 or c[i - 1] <= 0:
        return False
    s200, s50, s20, s10 = sma(c, i, 200), sma(c, i, 50), sma(c, i, 20), sma(c, i, 10)
    prev10 = sma(c, i - 1, 10)
    return bool(s200 > 0 and c[i] > s200 and s20 > s50 and c[i] > s20
                and c[i] <= s10 * 1.005 and c[i - 1] > prev10)


def load_series(candles_dir):
    """symbol -> (dates, closes) on adjusted closes, de-duplicated by date."""
    out = {}
    for path in glob.glob(os.path.join(candles_dir, "*")):
        try:
            d = json.load(open(path))
        except (ValueError, OSError):
            continue
        if not isinstance(d, dict) or "candles" not in d:
            continue
        rows = {}
        for k in d["candles"]:
            px = k.get("adjOrClose") or k.get("adjustedClose") or k.get("close")
            if px and px > 0:
                rows[k["timestamp"][:10]] = float(px)
        dates = sorted(rows)
        out[d["symbol"].upper()] = (dates, [rows[x] for x in dates])
    return out


class Bench:
    """A benchmark read strictly as-of a date: last bar ON OR BEFORE it."""

    def __init__(self, dates, closes):
        self.d, self.c = dates, closes

    def idx(self, date):
        return bisect_right(self.d, date) - 1

    def ready(self, date):
        return self.idx(date) >= MIN_BENCH_BARS

    def uptrend(self, date):
        i = self.idx(date)
        if i < MIN_BENCH_BARS:
            return None
        return self.c[i] > sma(self.c, i, 200) and sma(self.c, i, 20) > sma(self.c, i, 50)

    def ret(self, date, n=RS_LOOKBACK):
        i = self.idx(date)
        if i < n or self.c[i - n] <= 0:
            return None
        return self.c[i] / self.c[i - n] - 1


def run(candles_dir, universe_path):
    series = load_series(candles_dir)
    uni = json.load(open(universe_path))["symbols"]
    members = sorted(s["symbol"] for s in uni
                     if (s.get("harvest") or "large_50" in s.get("tags", []))
                     and s["symbol"] not in ETFS)
    sector_of = {s["symbol"]: s.get("sector") for s in uni}
    bench = {b: Bench(*series[b]) for b in BENCH if b in series}
    spy = bench["SPY"]

    trades, sessions, missing, kept = [], set(), [], 0
    open_marks = []
    for sidx, sym in enumerate(members):
        if sym not in series:
            missing.append(sym)
            continue
        d, c = series[sym]
        if len(c) < 220:
            missing.append(sym + "(<220 bars)")
            continue
        kept += 1
        sessions.update(d[210:])
        etf = GICS_ETF.get(sector_of.get(sym))
        n, i = len(c), 210
        while i < n - 1:
            if not _entry_signal(c, i):
                i += 1
                continue
            entry, peak, j, exit_i, bad = c[i], c[i], i + 1, None, False
            while j <= min(n - 1, i + MAX_HOLD):
                if c[j - 1] > 0 and abs(c[j] / c[j - 1] - 1) > 0.35:
                    bad = True
                    break
                if c[j] <= entry * (1 - STOP_PCT):
                    exit_i = j
                    break
                peak = max(peak, c[j])
                if c[j] <= peak * (1 - TRAIL_PCT):
                    exit_i = j
                    break
                j += 1
            if bad:
                i = j + 1
                continue
            still_open = False
            if exit_i is None:
                if j > i + MAX_HOLD:
                    exit_i = min(n - 1, i + MAX_HOLD)
                else:
                    exit_i, still_open = n - 1, True      # marked, not closed
            date = d[i]
            sec = bench.get(etf) if etf else None
            if sec is None or not sec.ready(date):
                sec = spy                                  # the fixed fallback
            m_up, s_up = spy.uptrend(date), sec.uptrend(date)
            sr = c[i] / c[i - RS_LOOKBACK] - 1 if c[i - RS_LOOKBACK] > 0 else None
            mr, er = spy.ret(date), sec.ret(date)
            rs = None if None in (sr, mr, er) else (sr > mr and sr > er)
            flags = {
                "A": True,
                "B": bool(m_up),
                "C": bool(m_up) and bool(s_up),
                "D": bool(rs),
                "E": bool(m_up) and bool(s_up) and bool(rs),
            }
            rec = (100 * (c[exit_i] / entry - 1), exit_i - i, date, sym, sidx, flags)
            if still_open:
                open_marks.append(rec)
                break
            trades.append(rec)
            i = exit_i + 1
    return trades, open_marks, sessions, kept, missing, members


def stats(ts):
    if not ts:
        return None
    p = [t[0] for t in ts]
    return {"n": len(ts), "win": 100 * sum(1 for x in p if x > 0) / len(p),
            "mean": st.mean(p), "median": st.median(p), "worst": min(p),
            "hold": st.median([t[1] for t in ts])}


def month_p10(sel, ctl_months):
    """10th percentile of entry-month mean returns over the control's months
    (>= 5 control entries). A month the variant sits out scores 0.0."""
    by = defaultdict(list)
    for t in sel:
        by[t[2][:7]].append(t[0])
    vals = sorted(st.mean(by[m]) if by.get(m) else 0.0 for m in ctl_months)
    k = max(0, int(round(0.10 * (len(vals) - 1))))
    return vals[k], len(vals)


def main():
    candles_dir, universe_path = sys.argv[1], sys.argv[2]
    trades, open_marks, sessions, kept, missing, members = run(candles_dir, universe_path)
    V = ["A", "B", "C", "D", "E"]
    NAME = {"A": "A control", "B": "B market", "C": "C mkt+sector",
            "D": "D rel.strength", "E": "E all"}

    def sel(v, pool=trades):
        return [t for t in pool if t[5][v]]

    dates = sorted(t[2] for t in trades)
    mid = dates[len(dates) // 2]
    ctl = sel("A")
    cm = defaultdict(int)
    for t in ctl:
        cm[t[2][:7]] += 1
    ctl_months = [m for m, k in cm.items() if k >= 5]

    print(f"universe {len(members)} · loaded {kept} · missing {missing}")
    print(f"sessions {len(sessions)} · closed trades {len(trades)} · open marks {len(open_marks)} "
          f"· first entry {dates[0]} · last {dates[-1]} · time split {mid}\n")
    print(f"{'variant':<16}{'trades':>7}{'kept':>7}{'win%':>7}{'mean%':>8}{'median%':>9}"
          f"{'worst%':>8}{'hold':>6}{'p10 month%':>12}")
    res = {}
    for v in V:
        s = stats(sel(v))
        s["p10"], s["months"] = month_p10(sel(v), ctl_months)
        res[v] = s
        print(f"{NAME[v]:<16}{s['n']:>7}{100*s['n']/len(ctl):>6.0f}%{s['win']:>6.1f}%{s['mean']:>7.2f}%"
              f"{s['median']:>8.2f}%{s['worst']:>7.1f}%{s['hold']:>6.0f}{s['p10']:>11.2f}%")
    print(f"(bad-month gate uses {res['A']['months']} entry-months with >= 5 control entries)")

    cells = {"time: 1st half": lambda t: t[2] < mid, "time: 2nd half": lambda t: t[2] >= mid,
             "symbols: even": lambda t: t[4] % 2 == 0, "symbols: odd": lambda t: t[4] % 2 == 1}
    g7 = {}
    print("\nG7 — beats the control on mean AND win rate in ALL FOUR cells?")
    for v in V[1:]:
        ok, lines = True, []
        for cn, f in cells.items():
            a, b = stats([t for t in sel(v) if f(t)]), stats([t for t in ctl if f(t)])
            good = bool(a and b and a["mean"] > b["mean"] and a["win"] > b["win"])
            ok &= good
            lines.append(f"      {cn}: {'PASS' if good else 'FAIL'} "
                         f"({a['mean']:+.2f}% vs {b['mean']:+.2f}%, {a['win']:.1f}% vs {b['win']:.1f}%)")
        g7[v] = ok
        print(f"  {NAME[v]}: {'PASS' if ok else 'FAIL'}")
        print("\n".join(lines))

    c = res["A"]
    gates = [
        ("G1 kept >=40%", lambda s, v: s["n"] >= 0.40 * c["n"]),
        ("G2 mean >= ctl+0.30", lambda s, v: s["mean"] >= c["mean"] + 0.30),
        ("G3 win >= ctl+1.5", lambda s, v: s["win"] >= c["win"] + 1.5),
        ("G4 median >= ctl+0.20", lambda s, v: s["median"] >= c["median"] + 0.20),
        ("G5 worst no worse", lambda s, v: s["worst"] >= c["worst"]),
        ("G6 p10 month >= ctl+1.0", lambda s, v: s["p10"] >= c["p10"] + 1.0),
        ("G7 both splits", lambda s, v: g7[v]),
    ]
    print("\nGATES")
    for v in V[1:]:
        fails = [g for g, f in gates if not f(res[v], v)]
        print(f"  {NAME[v]}: {'ALL PASS' if not fails else 'FAIL — ' + ', '.join(fails)}")

    print("\nOCTOBER 2026 REPLAY (descriptive, not a gate) — control entries 2026-10-01..31,")
    print("closed trades at exit, open ones marked at the last bar")
    octo = [t for t in trades + open_marks if "2026-10-01" <= t[2] <= "2026-10-31"]
    if octo:
        print(f"  control: {len(octo)} entries, mean {st.mean(t[0] for t in octo):+.2f}%")
        for v in V[1:]:
            keep = [t for t in octo if t[5][v]]
            blk = [t for t in octo if not t[5][v]]
            km = f"{st.mean(t[0] for t in keep):+.2f}%" if keep else "n/a"
            bm = f"{st.mean(t[0] for t in blk):+.2f}%" if blk else "n/a"
            print(f"  {NAME[v]}: blocks {len(blk)} of {len(octo)} · kept mean {km} · blocked mean {bm}")
        for a, b, lab in (("2026-10-13", "2026-10-16", "13-16 Oct (the T212 burst)"),):
            w = [t for t in octo if a <= t[2] <= b]
            if w:
                print(f"  {lab}: {len(w)} control entries, mean {st.mean(t[0] for t in w):+.2f}%; "
                      + ", ".join(f"{v} blocks {sum(1 for t in w if not t[5][v])}" for v in V[1:]))
    else:
        print("  no control entries in the window")


if __name__ == "__main__":
    main()

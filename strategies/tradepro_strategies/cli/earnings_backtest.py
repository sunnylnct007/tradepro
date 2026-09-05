"""tradepro-earnings-backtest — grade EARNINGS_GATES_V2.md, nothing else.

    uv run tradepro-earnings-backtest --symbols DELL,SNOW,AAPL   # smoke
    uv run tradepro-earnings-backtest                            # the graded run

This runner exists to answer a PRE-REGISTERED document (commit 16de3c0,
written and pushed before this file). It grades Q1, Q2 and Q3 against the
frozen thresholds and writes one artifact. It emits NO candidates and NO
signals — a study is not a strategy until the gates say so.

## The three hazards this file is built around

**Look-ahead via alignment.** yfinance_hist rows carry session=None: we do not
know BMO vs AMC. The graded run assumes AMC — the print lands between the close
of D and the open of the NEXT session T — so the entry close can never contain
the post-print move. If the truth was BMO, our "entry" is post-print and the
measured edge gets WORSE, not better. A3 re-runs everything at ±1 session.

**Double-counted prints.** The two sources can list one print a day apart
(Finnhub says the 1st, Yahoo says the 2nd). Exact-date dedupe leaves both, and
each becomes a "separate" trade around the same move. Events within 3 calendar
days of each other for one symbol are CLUSTERED to the earliest date; raw and
clustered counts are both reported.

**Coverage that shrinks silently.** Earnings history reaches ~2015; the bar
store caps ~4y for 98 of 244 names (IBKR's 1000-bar limit). Every event that
cannot be graded for missing bars is COUNTED and reported — a backtest that
quietly grades 60% of its events while reporting all of them is how G4 stayed
ungraded in the wheel study for three versions.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import logging
import statistics
from pathlib import Path

log = logging.getLogger("tradepro.earnings_backtest")

COST = 0.0020          # 10 bps per side, per the doc's data contract
FALL = 0.96            # >= 4% fall over the five sessions into the print
NEAR_PRINT_EXCL = 10   # comparison arm: >= 10 sessions from any print
SMA_N = 200
MIN_HISTORY = 210      # bars needed before an entry can be evaluated


# ── data loading ──────────────────────────────────────────────────────────

def load_events(base: str, token: str | None, symbols: list[str]) -> dict[str, list[str]]:
    """{symbol: [iso_date,...]} — PAST prints, both sources, exact-deduped
    then clustered (<=3 calendar days -> earliest). Counts reported."""
    import requests
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    today = _dt.date.today().isoformat()
    out: dict[str, list[str]] = {}
    raw_n = 0
    for sym in symbols:
        r = requests.get(f"{base}/api/earnings-calendar/{sym}",
                         params={"back": 4000, "ahead": 0},
                         headers=headers, timeout=45)
        if r.status_code != 200:
            continue
        dates = sorted({str(e.get("report_date"))[:10]
                        for e in (r.json().get("events") or [])
                        if str(e.get("report_date"))[:10] < today})
        raw_n += len(dates)
        clustered: list[str] = []
        for d in dates:  # ascending: keep the EARLIEST of a cluster (conservative under AMC)
            if clustered and (_dt.date.fromisoformat(d)
                              - _dt.date.fromisoformat(clustered[-1])).days <= 3:
                continue
            clustered.append(d)
        if clustered:
            out[sym] = clustered
    total = sum(len(v) for v in out.values())
    print(f"events: {raw_n} raw -> {total} after clustering, {len(out)} symbol(s)")
    return out


_STORE = None


def load_closes(sym: str, start: _dt.datetime, end: _dt.datetime):
    """(closes, iso_dates) — DISK ONLY (`skip_fetch=True`).

    A study reads the store; it never mutates it and never touches a provider.
    The first cut went through fetch_daily_bars, which tried to HARVEST the
    2015-era gap live — five symbols ran ten minutes with the IBKR session on
    the line, and an earlier force_refresh through a similar path is how
    TSLA's bars got damaged on 2 Sep. What is on disk is the dataset; what is
    missing is REPORTED as missing, not fetched mid-study.
    """
    global _STORE
    from ..bar_cache import asset_classes as _plugins  # noqa: F401 — registers
    from ..bar_cache.store import BarStore
    from ..ibkr_bars import route_asset_class
    if _STORE is None:
        from pathlib import Path as _P
        # Default provider chain so the asset-class registry populates —
        # skip_fetch=True below is the guard that keeps every provider idle.
        # An empty chain also empties the registry and every read KeyErrors.
        _STORE = BarStore(base_dir=_P("~/.tradepro/bar_cache").expanduser())
    ac = route_asset_class(sym)
    if ac is None:
        return None, None
    try:
        res = _STORE.get(sym, ac, "1d", start, end,
                         allow_partial=True, skip_fetch=True,
                         fetched_by="earnings_backtest")
        df = getattr(res, "df", res)
    except Exception:  # noqa: BLE001 — a missing symbol is coverage, not a crash
        return None, None
    if df is None or getattr(df, "empty", True):
        return None, None
    sub = df["close"].dropna()
    return [float(x) for x in sub.tolist()], [str(x)[:10] for x in sub.index]


# ── shared mechanics ──────────────────────────────────────────────────────

def _sma(closes: list[float], i: int, n: int = SMA_N) -> float | None:
    if i + 1 < n:
        return None
    return sum(closes[i - n + 1:i + 1]) / n


def _t_index(dates: list[str], print_date: str, shift: int) -> int | None:
    """Index of the first session whose OPEN follows the print (AMC), +shift."""
    import bisect
    t = bisect.bisect_right(dates, print_date) + shift
    return t if 0 < t < len(dates) else None


def _swing_sim(closes: list[float], entry_i: int) -> float | None:
    """Swing-style exit (20-day-mean target, -8% stop, 20-bar cap), net of costs.
    Used by Q1/Q2 so their exits match the live rule they are compared to."""
    from ..signals.mean_reversion import MAX_HOLD, exit_decision
    fill = closes[entry_i]
    for j in range(entry_i + 1, min(entry_i + MAX_HOLD + 2, len(closes))):
        do_exit, _why = exit_decision(closes, j, fill_price=fill,
                                      bars_held=j - entry_i)
        if do_exit:
            return closes[j] / fill - 1 - COST
    return None  # ran off the end of history: UNGRADED, never assumed


# ── Q3: the owner's shape ─────────────────────────────────────────────────

def q3_trades(closes, dates, prints, shift: int) -> list[dict]:
    out = []
    for pd_ in prints:
        t = _t_index(dates, pd_, shift)
        if t is None or t < MIN_HISTORY or t + 1 >= len(closes):
            continue
        e = t - 1
        sma = _sma(closes, e)
        if sma is None or closes[e] <= sma:
            continue
        if closes[e] > closes[e - 5] * FALL:
            continue
        ret = closes[t + 1] / closes[e] - 1 - COST
        out.append({"date": dates[e], "ret": ret})
    return out


def q3_comparison(closes, dates, prints, shift: int) -> list[float]:
    """The identical dip with NO print nearby — exit two sessions later."""
    pis = sorted(i for i in (
        _t_index(dates, p, shift) for p in prints) if i is not None)
    rets = []
    for d in range(MIN_HISTORY, len(closes) - 2):
        if pis and min(abs(d - pi) for pi in pis) < NEAR_PRINT_EXCL:
            continue
        sma = _sma(closes, d)
        if sma is None or closes[d] <= sma:
            continue
        if closes[d] > closes[d - 5] * FALL:
            continue
        rets.append(closes[d + 2] / closes[d] - 1 - COST)
    return rets


# ── Q1/Q2: carried from v1 ────────────────────────────────────────────────

def q1_q2_trades(closes, dates, prints) -> tuple[list[dict], list[dict]]:
    """Q1: every live-rule firing, tagged earnings-driven or not.
    Q2: >=5% drop on the earnings session itself, above the SMA."""
    from ..signals.mean_reversion import entry_signal
    pis = set(i for i in (_t_index(dates, p, 0) for p in prints) if i is not None)
    q1, q2 = [], []
    for i in range(MIN_HISTORY, len(closes) - 1):
        if entry_signal(closes, i):
            ret = _swing_sim(closes, i)
            if ret is not None:
                q1.append({"ret": ret, "date": dates[i],
                           "earnings": any((i - k) in pis for k in range(0, 3))})
        if i in pis and closes[i] <= closes[i - 1] * 0.95:
            sma = _sma(closes, i)
            if sma is not None and closes[i] > sma:
                ret = _swing_sim(closes, i)
                if ret is not None:
                    q2.append({"ret": ret, "date": dates[i]})
    return q1, q2


# ── grading ───────────────────────────────────────────────────────────────

def _mean(xs):
    return statistics.fmean(xs) if xs else None


def two_split(trades: list[dict], key="ret") -> dict:
    """Four cells: time halves (median trade date) x symbol halves (alpha).
    Deterministic, decided by the data's own median — nothing to tune."""
    if not trades:
        return {}
    dmed = sorted(t["date"] for t in trades)[len(trades) // 2]
    syms = sorted({t["sym"] for t in trades})
    first = set(syms[:len(syms) // 2])
    cells = {}
    for tn, tf in (("early", lambda t: t["date"] < dmed),
                   ("late", lambda t: t["date"] >= dmed)):
        for sn, sf in (("A-half", lambda t: t["sym"] in first),
                       ("Z-half", lambda t: t["sym"] not in first)):
            xs = [t[key] for t in trades if tf(t) and sf(t)]
            cells[f"{tn}/{sn}"] = {"n": len(xs), "mean": _mean(xs)}
    return cells


def pct(x):
    return f"{100 * x:+.2f}%" if x is not None else "—"


def main() -> int:
    ap = argparse.ArgumentParser(prog="tradepro-earnings-backtest")
    ap.add_argument("--symbols", help="comma list (default: committed universe minus account-barred)")
    ap.add_argument("--out", default="backtests/earnings_v2_result.json")
    args = ap.parse_args()
    logging.basicConfig(level=logging.WARNING)

    from .push_to_api import load_credentials
    from ..universe import universe_symbols

    base, token = load_credentials()
    base = base.rstrip("/")

    if args.symbols:
        syms = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
    else:
        syms = list(universe_symbols(strict=False))
        try:
            from ..paper.broker_ineligible import account_untradeable
            barred = set(account_untradeable(base, token))
            syms = [s for s in syms if s not in barred]
        except Exception:  # noqa: BLE001
            pass
    print(f"universe: {len(syms)} symbol(s)")

    events = load_events(base, token, syms)

    end = _dt.datetime.now(_dt.UTC)
    start = end - _dt.timedelta(days=4200)

    q3_all: dict[int, list[dict]] = {-1: [], 0: [], 1: []}
    comp_all: list[dict] = []
    q1_all: list[dict] = []
    q2_all: list[dict] = []
    no_bars: list[str] = []
    dropped_missing_bars = 0
    bar_span: dict[str, str] = {}

    for n, sym in enumerate(sorted(events), 1):
        closes, dates = load_closes(sym, start, end)
        if not closes:
            no_bars.append(sym)
            continue
        bar_span[sym] = dates[0]
        gradeable_from = dates[MIN_HISTORY] if len(dates) > MIN_HISTORY else "9999"
        dropped_missing_bars += sum(1 for p in events[sym] if p < gradeable_from)
        for shift in (-1, 0, 1):
            for t in q3_trades(closes, dates, events[sym], shift):
                q3_all[shift].append({**t, "sym": sym})
        comp_all += [{"ret": r, "sym": sym}
                     for r in q3_comparison(closes, dates, events[sym], 0)]
        q1, q2 = q1_q2_trades(closes, dates, events[sym])
        q1_all += [{**t, "sym": sym} for t in q1]
        q2_all += [{**t, "sym": sym} for t in q2]
        if n % 25 == 0:
            print(f"  {n}/{len(events)} · Q3 {len(q3_all[0])} · comp {len(comp_all)}"
                  f" · Q1 {len(q1_all)} · Q2 {len(q2_all)}")

    # ── COVERAGE, before any verdict ─────────────────────────────────────
    print(f"\nCOVERAGE — graded only what has bars, and says so:")
    print(f"  symbols with events {len(events)}, with no bars at all {len(no_bars)}"
          + (f" ({', '.join(no_bars[:8])}…)" if no_bars else ""))
    print(f"  events dropped for missing bar history: {dropped_missing_bars}")
    spans = sorted(bar_span.values())
    if spans:
        print(f"  bar history starts: earliest {spans[0]}, median {spans[len(spans)//2]}")

    q3 = q3_all[0]
    rets = [t["ret"] for t in q3]
    comp = [t["ret"] for t in comp_all]
    n_syms = len({t["sym"] for t in q3})
    mean = _mean(rets)
    cmean = _mean(comp)
    win = (sum(1 for r in rets if r > 0) / len(rets)) if rets else None
    srt = sorted(rets)
    p5 = srt[max(0, int(0.05 * len(srt)) - 1)] if srt else None
    worst = srt[0] if srt else None
    cells = two_split(q3)
    edge = (mean - cmean) if (mean is not None and cmean is not None) else None
    sh_dn = _mean([t["ret"] for t in q3_all[-1]])
    sh_up = _mean([t["ret"] for t in q3_all[1]])

    gates = {
        "P0": (len(rets) >= 400 and n_syms >= 80,
               f"{len(rets)} events across {n_syms} symbols (need ≥400 / ≥80)"),
        "P1": (mean is not None and mean >= 0.0075, f"mean {pct(mean)} (need ≥ +0.75%)"),
        "P2": (edge is not None and edge >= 0.0050,
               f"edge vs no-print arm {pct(edge)} (arm {pct(cmean)}, n={len(comp)}; need ≥ +0.50pt)"),
        "P3": (win is not None and win >= 0.55,
               f"win rate {100*win:.1f}% (need ≥55%)" if win is not None else "—"),
        "P4": (p5 is not None and p5 >= -0.12, f"5th pct {pct(p5)} (need ≥ −12%)"),
        "P5": (worst is not None and worst >= -0.35, f"worst {pct(worst)} (need ≥ −35%)"),
        "P6": (bool(cells) and all(c["mean"] is not None and c["mean"] > 0
                                   for c in cells.values()),
               " · ".join(f"{k} {pct(v['mean'])} (n={v['n']})" for k, v in cells.items())),
        "A3": (sh_dn is not None and sh_up is not None and sh_dn > 0 and sh_up > 0,
               f"shift −1 {pct(sh_dn)} · shift +1 {pct(sh_up)} (both must stay >0)"),
    }

    print("\n══ Q3 — buy weakness INTO the print (graded, AMC alignment) ══")
    for g, (ok, detail) in gates.items():
        print(f"  {g}  {'PASS' if ok else 'FAIL'}   {detail}")
    verdict = all(ok for ok, _ in gates.values())
    print(f"\n  Q3 VERDICT: {'ALL GATES PASS' if verdict else 'FAILS — does not ship'}")

    # ── Q1/Q2 (v1's questions, bars unchanged) ───────────────────────────
    q1e = [t["ret"] for t in q1_all if t["earnings"]]
    q1o = [t["ret"] for t in q1_all if not t["earnings"]]
    print("\n══ Q1 — the live rule's trades, earnings-driven vs not ══")
    print(f"  earnings-driven {len(q1e)}: mean {pct(_mean(q1e))}  ·  "
          f"the rest {len(q1o)}: mean {pct(_mean(q1o))}")
    q2r = [t["ret"] for t in q2_all]
    q2win = (sum(1 for r in q2r if r > 0) / len(q2r)) if q2r else None
    print("══ Q2 — ≥5% earnings-session drop, swing exits ══")
    print(f"  V0 {len(q2r)} trades (need ≥300) · E1 win "
          f"{100*q2win:.1f}% (need ≥55%)" if q2win is not None else "  no trades")
    print(f"  E2 mean {pct(_mean(q2r))} · E5 worst {pct(min(q2r) if q2r else None)}")

    out = {
        "generated_utc": end.isoformat(),
        "doc": "EARNINGS_GATES_V2.md @ 16de3c0",
        "coverage": {"symbols_with_events": len(events), "no_bars": no_bars,
                     "events_dropped_missing_bars": dropped_missing_bars},
        "q3": {"n": len(rets), "symbols": n_syms, "mean": mean, "win": win,
               "p5": p5, "worst": worst, "comparison_mean": cmean,
               "comparison_n": len(comp), "edge": edge, "cells": cells,
               "shift_minus1_mean": sh_dn, "shift_plus1_mean": sh_up,
               "gates": {g: ok for g, (ok, _) in gates.items()},
               "verdict": verdict},
        "q1": {"earnings_n": len(q1e), "earnings_mean": _mean(q1e),
               "rest_n": len(q1o), "rest_mean": _mean(q1o)},
        "q2": {"n": len(q2r), "mean": _mean(q2r), "win": q2win,
               "worst": (min(q2r) if q2r else None)},
    }
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(out, indent=1))
    print(f"\nwrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

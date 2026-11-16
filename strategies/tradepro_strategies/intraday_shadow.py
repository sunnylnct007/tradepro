"""Intraday rules, recorded forward WITHOUT placing anything (16 Nov 2026).

Owner: "what abt the intraday one that u said u will test everyday without
placing". Two intraday rules from the 13 Nov scorecard spec were backtested on
two years of 5-minute bars and FAILED (rule 7: +0.11%/trade, t~1.0, shrank with
more data; rule 2: -0.02%/trade). A backtest can be wrong in either direction,
so each rule now builds a FORWARD record from the start date below: every
session, the same frozen rule is replayed on that day's harvested 5-minute
bars, and the would-be trade and its result are recorded. No order is ever
sent — this module has no broker import.

The record is RECOMPUTED from the start date on every run instead of being
appended to: the bars are the source of truth, so a missed evening or a
re-run can never leave a gap or a double count, and nothing is stored but the
published summary.

The rules are copied VERBATIM from the backtests that judged them
(project_rule7_orb_stocks_in_play_backtest, project_rule2_gap_down_short_backtest)
so the forward record measures the same thing. Fills are the spec's model
(a 5m bar's close), not real executions: this records whether the RULE has an
edge, not what slippage would do to it.

Rule 7 — opening-range breakout on stocks in play. The 09:30 5m candle's
volume at least 2x its average over the prior 20 sessions. Candle up -> long
when a later bar CLOSES above its high; down -> short on a close below its low;
entry at that close. Stop at the other end of the candle (a bar opening
through it exits at the open), else flat at the last bar's close.

Rule 2 — gap-down opening-range break short. Gap down >= 2% (09:30 open vs the
prior session's close). Between 10:00 and 11:25 a 5m bar closes below the
09:30-09:55 low, with the stock below its session VWAP and QQQ below QQQ's.
Short at that close; stop +1.5%, target -1.5% (stop checked first in a bar);
else flat at the close. At most 2 trades a day, earliest first.

$10,000 notional a trade, 0.06% round trip, as the backtests.
"""
from __future__ import annotations

import json
import pathlib
from datetime import date, datetime, timezone

import numpy as np
import pandas as pd

RECORD_START = "2026-11-16"          # first forward session; never moved
NOTIONAL, COST = 10_000.0, 0.0006
R7_VOLUME_X = 2.0
#: RULE7_BREAKOUT_VOLUME_GATES_V1: a breakout bar at >= 1.5x the usual volume
#: for that time of day is "high". Fixed before any result; never re-tuned.
R7_BREAKOUT_HIGH_X = 1.5
R2_GAP, R2_STOP, R2_TGT, R2_MAX_A_DAY = -0.02, 0.015, 0.015, 2

#: What the two-year backtest said, for the side-by-side (per trade, after cost).
BACKTEST = {
    "rule7": {"avg_pct": 0.11, "note": "2y of 5m on these 29 names; t~1.0, shrank with more data — FAILED"},
    "rule2": {"avg_pct": -0.02, "note": "2y, n=251; the spec's +0.4% was its last-64-session window — FAILED"},
}

UNIVERSE_FILE = pathlib.Path(__file__).resolve().parent.parent / "universe" / "intraday_shadow.json"


def universe() -> list[str]:
    return list(json.loads(UNIVERSE_FILE.read_text()))


def _sessions(sym: str, lookback_days: int) -> list[tuple[date, pd.DataFrame]]:
    from .daily_bars import load_bars
    df = load_bars(sym, "5m", lookback_days=lookback_days, fetched_by="intraday-shadow")
    if df is None or not len(df):
        return []
    df = df.tz_convert("America/New_York").between_time("09:30", "15:55")
    out = []
    for d, g in df.groupby(df.index.date):
        if len(g) >= 30 and g.index[0].strftime("%H:%M") == "09:30":
            out.append((d, g))
    return out


def _vwap(g: pd.DataFrame) -> pd.Series:
    tp = (g.high + g.low + g.close) / 3
    return (tp * g.volume).cumsum() / g.volume.cumsum().replace(0, np.nan)


def breakout_volume_x(ss: list[tuple[date, pd.DataFrame]], i: int, t) -> float | None:
    """The bar at time-of-day t on session i, against that same bar's average
    volume over the prior 20 sessions that have it."""
    hhmm = t.strftime("%H:%M")
    hist = [float(g.volume[g.index.strftime("%H:%M") == hhmm].iloc[0])
            for _, g in ss[max(0, i - 20):i] if (g.index.strftime("%H:%M") == hhmm).any()]
    if not hist or np.mean(hist) <= 0:
        return None
    today = ss[i][1]
    return round(float(today.loc[t].volume) / float(np.mean(hist)), 2)


def rule7_trades(sym: str, ss: list[tuple[date, pd.DataFrame]], start: date) -> list[dict]:
    first_vol = [float(g.iloc[0].volume) for _, g in ss]
    rows = []
    for i, (d, g) in enumerate(ss):
        if d < start or i < 20:
            continue
        avg = float(np.mean(first_vol[i - 20:i]))
        f = g.iloc[0]
        if avg <= 0 or f.volume < R7_VOLUME_X * avg or f.close == f.open:
            continue
        side = 1 if f.close > f.open else -1
        lvl, stop = (f.high, f.low) if side == 1 else (f.low, f.high)
        if lvl == stop:
            continue
        rest = g.iloc[1:]
        k = next((j for j in range(len(rest))
                  if (side == 1 and rest.iloc[j].close > lvl) or (side == -1 and rest.iloc[j].close < lvl)), None)
        if k is None:
            continue
        entry, et = float(rest.iloc[k].close), rest.index[k]
        exit_px, why = float(rest.iloc[-1].close), "close"
        for j in range(k + 1, len(rest)):
            b = rest.iloc[j]
            if side == 1 and b.low <= stop:
                exit_px, why = float(b.open if b.open < stop else stop), "stop"
                break
            if side == -1 and b.high >= stop:
                exit_px, why = float(b.open if b.open > stop else stop), "stop"
                break
        pct = side * (exit_px / entry - 1) - COST
        bvx = breakout_volume_x(ss, i, et)
        rows.append({"rule": "rule7", "date": str(d), "symbol": sym, "side": "long" if side == 1 else "short",
                     "volume_x": round(float(f.volume / avg), 1), "entry_time": et.strftime("%H:%M"),
                     "breakout_volume_x": bvx,
                     "breakout_volume": (None if bvx is None else "high" if bvx >= R7_BREAKOUT_HIGH_X else "low"),
                     "entry": round(entry, 2), "stop": round(float(stop), 2), "exit": round(exit_px, 2),
                     "exit_why": why, "pct": round(pct * 100, 3), "usd": round(NOTIONAL * pct, 2)})
    return rows


def rule2_signals(sym: str, ss: list[tuple[date, pd.DataFrame]], qqq: dict, start: date) -> list[dict]:
    out = []
    for (pd_, pg), (d, g) in zip(ss, ss[1:]):
        if d < start or d not in qqq:
            continue
        gap = float(g.iloc[0].open / pg.iloc[-1].close - 1)
        if gap > R2_GAP:
            continue
        orl = float(g.between_time("09:30", "09:55").low.min())
        v = _vwap(g)
        qg, qv = qqq[d]
        for t, b in g.between_time("10:00", "11:25").iterrows():
            if t not in qv.index:
                continue
            if b.close < orl and b.close < v.loc[t] and qg.loc[t].close < qv.loc[t]:
                out.append({"date": d, "t": t, "symbol": sym, "entry": float(b.close), "gap": gap, "g": g})
                break
    return out


def rule2_trades(signals: list[dict]) -> list[dict]:
    by_day: dict[date, list[dict]] = {}
    for s in signals:
        by_day.setdefault(s["date"], []).append(s)
    rows = []
    for d, lst in sorted(by_day.items()):
        for s in sorted(lst, key=lambda x: x["t"])[:R2_MAX_A_DAY]:
            entry = s["entry"]
            stop, tgt = entry * (1 + R2_STOP), entry * (1 - R2_TGT)
            after = s["g"][s["g"].index > s["t"]]
            px, why = (float(after.iloc[-1].close) if len(after) else entry), "close"
            for _, b in after.iterrows():
                if b.high >= stop:
                    px, why = float(max(b.open, stop)), "stop"
                    break
                if b.low <= tgt:
                    px, why = float(min(b.open, tgt)), "target"
                    break
            pct = -(px / entry - 1) - COST
            rows.append({"rule": "rule2", "date": str(d), "symbol": s["symbol"], "side": "short",
                         "gap_pct": round(s["gap"] * 100, 2), "entry_time": s["t"].strftime("%H:%M"),
                         "entry": round(entry, 2), "exit": round(px, 2), "exit_why": why,
                         "pct": round(pct * 100, 3), "usd": round(NOTIONAL * pct, 2)})
    return rows


def day_tags(qss: list[tuple[date, pd.DataFrame]]) -> dict[str, str]:
    tags = {}
    for (_, pg), (d, g) in zip(qss, qss[1:]):
        r = float(g.iloc[-1].close / pg.iloc[-1].close - 1)
        tags[str(d)] = "Up" if r > 0.005 else "Down" if r < -0.005 else "Flat"
    return tags


def summarise(rows: list[dict]) -> dict:
    if not rows:
        return {"trades": 0}
    p = np.array([r["pct"] for r in rows])
    return {"trades": len(rows), "win_pct": round(float((p > 0).mean() * 100), 1),
            "avg_pct": round(float(p.mean()), 3), "total_usd": round(sum(r["usd"] for r in rows), 2),
            "sessions": len({r["date"] for r in rows})}


def build(start: str = RECORD_START, symbols: list[str] | None = None) -> dict:
    syms = symbols or universe()
    start_d = date.fromisoformat(start)
    lookback = (date.today() - start_d).days + 45          # 20 prior sessions for rule 7's volume average
    qss = _sessions("QQQ", lookback)
    qqq = {d: (g, _vwap(g)) for d, g in qss}
    tags = day_tags(qss)
    r7, r2sig, missing, stale = [], [], [], []
    q_last = qss[-1][0] if qss else None
    for s in syms:
        ss = _sessions(s, lookback)
        if not ss:
            missing.append(s)
            continue
        if q_last and ss[-1][0] < q_last:
            # Its 5m bars stopped arriving: a name that cannot fire must be
            # NAMED, not silently counted as "no signal today".
            stale.append(f"{s} (bars to {ss[-1][0]})")
        r7 += rule7_trades(s, ss, start_d)
        r2sig += rule2_signals(s, ss, qqq, start_d)
    r2 = rule2_trades(r2sig)
    # A SESSION COUNTS ONLY WHEN IT IS OVER: QQQ has its 15:55 bar. Before
    # that, "exit at the close" would be whatever bar came last — a mid-day
    # price recorded as a result. Trades on an unfinished session are shown as
    # IN PROGRESS and kept out of the record.
    complete = {str(d) for d, g in qss if g.index[-1].strftime("%H:%M") == "15:55"}
    for r in r7 + r2:
        r["day"] = tags.get(r["date"])
        r["final"] = r["date"] in complete or r["exit_why"] in ("stop", "target")
        if not r["final"]:
            r["exit_why"] = "in progress (session not over)"
    sessions = sorted(str(d) for d, _ in qss if d >= start_d and str(d) in complete)
    latest = max((str(d) for d, _ in qss if d >= start_d), default=None)
    rules = {}
    for key, rows, name in (("rule7", r7, "Opening-range breakout, stocks in play"),
                            ("rule2", r2, "Gap-down opening-range short")):
        done = [r for r in rows if r["final"]]
        rules[key] = {"name": name, "record": summarise(done), "backtest": BACKTEST[key],
                      "by_day": {t: summarise([r for r in done if r["day"] == t]) for t in ("Up", "Flat", "Down")},
                      "by_breakout_volume": ({v: summarise([r for r in done if r.get("breakout_volume") == v])
                                              for v in ("high", "low")} if key == "rule7" else None),
                      "latest_session": [r for r in rows if r["date"] == latest]}
    return {"as_of_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "record_start": start, "sessions": len(sessions), "latest_session": latest,
            "latest_session_final": latest in complete if latest else None,
            "latest_day": tags.get(latest) if latest and latest in complete else None,
            "universe_n": len(syms), "missing": missing, "stale": stale, "rules": rules,
            "trades": sorted(r7 + r2, key=lambda r: (r["date"], r["entry_time"])),
            "placed_orders": 0,
            "how": "Replayed on harvested 5-minute bars after the close. No order is ever sent."}

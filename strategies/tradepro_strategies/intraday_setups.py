"""Intraday setups forming — the two intraday rules' CONDITIONS, shown during
the session from the 5-minute bars we already harvest (21 Nov 2026).

Owner: "with this if we use our 5 min harvested data we should be able to do
early indicator of some stocks for intraday". This is the honest half of that:
no new rule. For each name in the intraday universe it states, as of the last
harvested 5m bar, what rule 7 (opening-range breakout on a stock in play) and
rule 2 (gap-down opening-range short) can see — in play or not, the gap, the
opening range, VWAP side, relative strength vs QQQ since the open, and whether
the rule has TRIGGERED and at which bar. Definitions are imported from
intraday_shadow so the board and the forward record can never disagree.

WHAT IT IS NOT. Not a signal and not an edge: both rules FAILED their
two-year backtests and their forward record is too short to grade. Every row
carries the rule's current verdict for that reason. The data is up to one
harvest interval old (the 5m lane runs every 30 minutes on the Mac), and the
board says which bar it is reading. An "early indicator" that predicts the
rest of the day would be a NEW rule: pre-registered and backtested on the 5m
store first, never shown before that. No order is ever sent from here.
"""
from __future__ import annotations

from datetime import date, datetime, timezone

import numpy as np
import pandas as pd

from . import intraday_shadow as S

LOOKBACK_DAYS = 45          # 20 prior sessions for the opening-volume norm
HARVEST_INTERVAL_MIN = 30   # the 5m lane's cadence; stated on the board
S_MAX_DAY_MOVE = 0.35       # the studies' corrupt-bar guard (a >35% open is not a gap)


def _spy_below_200d(session: date) -> bool | None:
    from .daily_bars import load_daily
    df = load_daily("SPY", lookback_days=420, fetched_by="intraday-setups")
    if df is None or len(df) < 201:
        return None
    c = df["close"].astype(float)
    c.index = pd.to_datetime([str(t)[:10] for t in df.index])
    c = c[c.index < pd.Timestamp(session)]
    return bool(c.iloc[-1] < c.tail(200).mean()) if len(c) >= 200 else None


def _first_bar_volume_x(ss: list[tuple[date, pd.DataFrame]], i: int) -> float | None:
    hist = [float(g.iloc[0].volume) for _, g in ss[max(0, i - 20):i]]
    if len(hist) < 5 or np.mean(hist) <= 0:
        return None
    return round(float(ss[i][1].iloc[0].volume) / float(np.mean(hist)), 2)


def setup_row(sym: str, ss: list[tuple[date, pd.DataFrame]], qqq_today: pd.DataFrame | None) -> dict | None:
    """The latest session's state for one name. None when it has fewer than
    two sessions (no prior close to gap against)."""
    if len(ss) < 2:
        return None
    i = len(ss) - 1
    d, g = ss[i]
    prev_close = float(ss[i - 1][1].iloc[-1].close)
    f, last = g.iloc[0], g.iloc[-1]
    gap = float(f.open / prev_close - 1)
    if abs(gap) > S_MAX_DAY_MOVE:
        # SNDK 20 Nov 2026 read as a -88% gap: a split or a mis-mapped bar,
        # not a move. The rules' corrupt-bar guard applies here too — NAMED,
        # not scored as "in play" and "triggered".
        return {"symbol": sym, "session": str(d), "as_of_bar": last.name.strftime("%H:%M"), "bars": int(len(g)),
                "last": round(float(last.close), 2), "prev_close": round(prev_close, 2),
                "not_scored": f"{gap * 100:+.0f}% open vs prior close — a split or a corrupt bar, not a gap",
                "in_play": None, "opening_volume_x": None, "gap_pct": None, "day_pct": None,
                "rule7": {"eligible": False, "triggered_at": None}, "rule2": {"eligible": False, "triggered_at": None}}
    vol_x = _first_bar_volume_x(ss, i)
    orr = g.between_time("09:30", "09:55")
    or_hi, or_lo = float(orr.high.max()), float(orr.low.min())
    vwap = float(S._vwap(g).iloc[-1])
    px = float(last.close)
    rs = None
    if qqq_today is not None and len(qqq_today):
        qo, ql = float(qqq_today.iloc[0].open), float(qqq_today.iloc[-1].close)
        rs = round(((px / f.open - 1) - (ql / qo - 1)) * 100, 2)
    pg = ss[i - 1][1]
    levels = {"prior_high": round(float(pg.high.max()), 2), "prior_low": round(float(pg.low.min()), 2),
              "prior_close": round(prev_close, 2), "today_high": round(float(g.high.max()), 2),
              "today_low": round(float(g.low.min()), 2), "opening_range_high": round(or_hi, 2),
              "opening_range_low": round(or_lo, 2), "vwap": round(vwap, 2),
              "note": "levels are FACTS about price; whether price reacts at them is untested intraday "
                      "(daily S/R study: no edge vs placebo on 76,260 touches)"}
    row = {
        "symbol": sym, "session": str(d), "as_of_bar": last.name.strftime("%H:%M"), "levels": levels,
        "bars": int(len(g)), "last": round(px, 2), "prev_close": round(prev_close, 2),
        "gap_pct": round(gap * 100, 2), "day_pct": round((px / prev_close - 1) * 100, 2),
        "opening_volume_x": vol_x, "in_play": (None if vol_x is None else vol_x >= S.R7_VOLUME_X),
        "opening_range": {"high": round(or_hi, 2), "low": round(or_lo, 2),
                          "position": "above" if px > or_hi else "below" if px < or_lo else "inside"},
        "vwap": round(vwap, 2), "vs_vwap": "above" if px > vwap else "below",
        "rs_vs_qqq_pct": (None if rs is None else float(rs)),
        "rule7": {"eligible": bool(vol_x is not None and vol_x >= S.R7_VOLUME_X and f.close != f.open),
                  "side": None, "level": None, "stop": None, "triggered_at": None},
        "rule2": {"eligible": gap <= S.R2_GAP, "triggered_at": None},
    }
    if row["rule7"]["eligible"]:
        side = 1 if f.close > f.open else -1
        lvl, stop = (f.high, f.low) if side == 1 else (f.low, f.high)
        row["rule7"].update({"side": "long" if side == 1 else "short", "level": round(float(lvl), 2),
                             "stop": round(float(stop), 2)})
        rest = g.iloc[1:]
        for t, b in rest.iterrows():
            if (side == 1 and b.close > lvl) or (side == -1 and b.close < lvl):
                row["rule7"]["triggered_at"] = t.strftime("%H:%M")
                break
    if row["rule2"]["eligible"] and qqq_today is not None and len(qqq_today):
        v, qv = S._vwap(g), S._vwap(qqq_today)
        for t, b in g.between_time("10:00", "11:25").iterrows():
            if t in qv.index and b.close < or_lo and b.close < v.loc[t] and qqq_today.loc[t].close < qv.loc[t]:
                row["rule2"]["triggered_at"] = t.strftime("%H:%M")
                break
    return row


def build(symbols: list[str] | None = None) -> dict:
    syms = symbols or S.universe()
    qss = S._sessions("QQQ", LOOKBACK_DAYS)
    if not qss:
        raise RuntimeError("QQQ has no 5m bars; the session cannot be read")
    q_date, qg = qss[-1]
    final = qg.index[-1].strftime("%H:%M") == "15:55"
    rows, missing, stale = [], [], []
    for s in syms:
        ss = S._sessions(s, LOOKBACK_DAYS)
        if not ss:
            missing.append(s)
            continue
        if ss[-1][0] < q_date:
            stale.append(f"{s} (bars to {ss[-1][0]})")
            continue
        r = setup_row(s, ss, qg)
        if r:
            rows.append(r)
    rows.sort(key=lambda r: (-(r["in_play"] or 0), -(r["opening_volume_x"] or 0)))
    not_scored = [f"{r['symbol']}: {r['not_scored']}" for r in rows if r.get("not_scored")]
    return {"as_of_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "session": str(q_date), "session_final": final, "as_of_bar": qg.index[-1].strftime("%H:%M"),
            "data_age_note": (f"bars are up to {HARVEST_INTERVAL_MIN} minutes old — the 5m lane harvests "
                              f"every {HARVEST_INTERVAL_MIN} minutes"),
            "spy_below_200d": _spy_below_200d(q_date),
            "qqq_day_pct": round((float(qg.iloc[-1].close) / float(qss[-2][1].iloc[-1].close) - 1) * 100, 2) if len(qss) > 1 else None,
            "universe_n": len(syms), "missing": missing, "stale": stale, "not_scored": not_scored,
            "in_play": [r["symbol"] for r in rows if r["in_play"]],
            "rule7_triggered": [r["symbol"] for r in rows if r["rule7"]["triggered_at"]],
            "rule2_triggered": [r["symbol"] for r in rows if r["rule2"]["triggered_at"]],
            "rows": rows, "placed_orders": 0,
            "what_this_is": ("The two intraday rules' CONDITIONS as of the last harvested bar. Not a signal: "
                             "both rules failed their backtests and their forward record is too short to grade — "
                             "see `verdicts`. No order is sent from here.")}

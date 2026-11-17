"""Today's biggest gainers and losers across every stock we harvest — the
market's list, not a signal.

Owner, 8 Sep 2026: "we scanning the market again and again but not showing
something like market gainer/loser". The first version came from a Yahoo
download inside the pre-earnings watch job and was removed with that job on
14 Nov; the owner, 17 Nov: "i see u have removed the gainers and losers list
... that was across all stocks". It returns built from OUR store:

  * the move = the latest harvested 5-minute close against the previous
    session's SETTLED daily close (the golden bar store, never a vendor quote);
  * 52-week high / low / position in the range from the same daily closes;
  * volume = today's volume so far against the average volume to the SAME
    time of day over the prior 20 sessions (a 10:00 total against full days
    would always read "quiet");
  * a name with no 5-minute bar for the latest session is not ranked; it is
    COUNTED, so a thin list reads as a thin scan, not a quiet market.

No strategy has judged these names. Nothing here places an order.
"""
from __future__ import annotations

from datetime import datetime, timezone

import numpy as np
import pandas as pd

NY = "America/New_York"
ROWS_EACH_SIDE = 25


def _five_min(sym: str) -> pd.DataFrame | None:
    from .daily_bars import load_bars
    df = load_bars(sym, "5m", lookback_days=40, fetched_by="market-movers")
    if df is None or not len(df):
        return None
    return df.tz_convert(NY).between_time("09:30", "15:55")


def _daily(sym: str) -> pd.Series | None:
    from .daily_bars import load_daily
    df = load_daily(sym, lookback_days=400, fetched_by="market-movers")
    if df is None or not len(df):
        return None
    c = df["close"].dropna()
    c.index = pd.DatetimeIndex([pd.Timestamp(x).date() for x in c.index])
    return c[~c.index.duplicated(keep="last")]


def volume_x(m5: pd.DataFrame, day, upto: str) -> float | None:
    """Volume today to `upto` (HH:MM, New York) against the mean of the same
    span over the prior 20 sessions that have bars to that time."""
    hhmm = m5.index.strftime("%H:%M")
    dates = m5.index.date
    today = float(m5.volume[(dates == day) & (hhmm <= upto)].sum())
    prior = sorted({d for d in dates if d < day})[-20:]
    hist = []
    for d in prior:
        sel = (dates == d) & (hhmm <= upto)
        if sel.any() and (hhmm[dates == d] >= upto).any():
            hist.append(float(m5.volume[sel].sum()))
    if not hist or np.mean(hist) <= 0:
        return None
    return round(today / float(np.mean(hist)), 2)


def row_for(sym: str, m5: pd.DataFrame, daily: pd.Series) -> dict | None:
    if m5 is None or not len(m5) or daily is None or len(daily) < 2:
        return None
    day = m5.index[-1].date()
    last_bar = m5.index[-1]
    prev = daily[daily.index < pd.Timestamp(day)]
    if prev.empty:
        return None
    prev_close = float(prev.iloc[-1])
    last = float(m5.close.iloc[-1])
    if prev_close <= 0:
        return None
    row = {"symbol": sym, "last": round(last, 2), "prev_close": round(prev_close, 2),
           "prev_close_date": str(prev.index[-1].date()),
           "chg_pct": round((last / prev_close - 1) * 100, 2),
           "session": str(day), "bar_time": last_bar.strftime("%H:%M")}
    win = pd.concat([prev.tail(251), pd.Series([last], index=[pd.Timestamp(day)])])
    row["window_sessions"] = int(len(win))
    if len(win) >= 60:
        hi, lo = float(win.max()), float(win.min())
        row.update(hi_52w=round(hi, 2), lo_52w=round(lo, 2),
                   off_hi_pct=round(100 * (last - hi) / hi, 1) if hi else None,
                   range_pos_pct=round(100 * (last - lo) / (hi - lo), 0) if hi > lo else None)
    # Volume to the last FINISHED bar: the newest one may still be forming
    # (the harvest writes it part-way through), which would understate today.
    todays = m5.index[m5.index.date == day]
    if len(todays) >= 2:
        row["vol_x_20d"] = volume_x(m5, day, todays[-2].strftime("%H:%M"))
    else:
        row["vol_x_20d"] = None
    return row


def build(symbols: list[str] | None = None, held: set[str] | None = None) -> dict:
    if symbols is None:
        from .universe import harvest_symbols
        symbols = harvest_symbols()
    rows, no_bars = [], []
    for sym in symbols:
        try:
            r = row_for(sym, _five_min(sym), _daily(sym))
        except Exception:  # noqa: BLE001 — one bad name must not cost the list; it is COUNTED
            r = None
        if r is None:
            no_bars.append(sym)
        else:
            rows.append(r)
    latest = max((r["session"] for r in rows), default=None)
    behind = sorted(r["symbol"] for r in rows if r["session"] != latest)
    ranked = [r for r in rows if r["session"] == latest]
    for r in ranked:
        r["status"] = "held" if held and r["symbol"] in held else ""
    ranked.sort(key=lambda r: -r["chg_pct"])
    return {
        "as_of_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "session": latest,
        # A RANGE, not the newest: mid-harvest some names are a bar or more
        # ahead of others, and "to 14:10" over a list mostly at 13:20 overstates it.
        "bar_time_ny": (lambda ts: None if not ts else (ts[0] if ts[0] == ts[-1] else f"{ts[0]}–{ts[-1]}"))(
            sorted({r["bar_time"] for r in ranked})),
        "scanned": len(symbols), "ranked": len(ranked),
        "no_bars": sorted(no_bars), "behind": behind,
        "gainers": [r for r in ranked if r["chg_pct"] > 0][:ROWS_EACH_SIDE],
        "losers": sorted([r for r in ranked if r["chg_pct"] < 0], key=lambda r: r["chg_pct"])[:ROWS_EACH_SIDE],
        "source": "harvested 5-minute bars vs the previous settled daily close (golden bar store)",
        "note": "the market's list, not a signal: no strategy has judged these names",
        "placed_orders": 0,
    }

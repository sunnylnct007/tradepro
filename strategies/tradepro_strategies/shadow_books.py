"""Shadow books — several frozen rules recorded in parallel, NO orders.

Pre-registered in SHADOW_BOOKS_V1.md (a650b501) before any result below was
computed. Owner, 21 Nov 2026: "can we not evaluate a bit more strategy in
parallel" → "yes go with it".

Each book replays a rule on SETTLED daily bars from the date the rule was
frozen, so every session it records is data the rule never saw. The record is
RECOMPUTED from that date on every run (same as intraday_shadow): the bars are
the source of truth, so a missed night cannot leave a gap or a double count.
This module has no broker import; it cannot place anything.

The rules are IMPORTED from their signal modules — the same definitions the
screens and paper lanes use. Fills: signal on a close, buy at the next open;
exit checked on the close, sell at the next open. That is what a live book can
do, and it is why these numbers sit a little under the studies' figures.
"""
from __future__ import annotations

import math
from datetime import date, datetime, timezone

import numpy as np
import pandas as pd

from .signals import mean_reversion as MR
from .signals import momentum_pullback as MP

NOTIONAL, COST = 10_000.0, 0.0010
MAX_DAY_MOVE = 0.35          # the studies' corrupt-bar guard
VERSION = "SHADOW_BOOKS_V1"

#: Fixed in SHADOW_BOOKS_V1.md. Never moved.
DAILY_BOOKS = {
    "swing": {"name": "Swing — 2.25σ dip above the 200-day", "rule": MR, "start": "2026-09-09",
              "min_n": 50, "pass_mean": 0.49, "pass_win": 60.0,
              "backtest": {"avg_pct": 0.97, "win_pct": 72.0, "note": "next-open entry, 244 names"}},
    "pullback": {"name": "Momentum pullback — back at the 10-day in an uptrend", "rule": MP, "start": "2026-09-01",
                 "min_n": 50, "pass_mean": 0.77, "pass_win": None,
                 "backtest": {"avg_pct": 1.53, "win_pct": 47.0, "note": "5,815 trades, MOMENTUM_GATES_V2"}},
}
ROTATION_START = "2026-11-13"
ROTATION_MIN_DECISIONS, ROTATION_MAX_BEHIND = 6, 3.0
INTRADAY_MIN_N, INTRADAY_PASS_MEAN = 50, 0.15


# ── verdicts ───────────────────────────────────────────────────────────


def verdict_daily(rec: dict, spec: dict, sessions_since_start: int | None = None) -> str:
    n = rec.get("trades", 0)
    hold = spec["rule"].MAX_HOLD if "rule" in spec else None
    if hold is not None and sessions_since_start is not None and sessions_since_start < hold:
        # AMENDMENT 21 Nov 2026 (SHADOW_BOOKS_V1.md): a record of CLOSED trades is
        # losers-first until the first entries could have timed out. The first
        # run read "pullback FAIL" on 77 closes with 61 trades still open at a
        # profit. Deferring is the only direction this can move a verdict.
        return f"deferred — record censored ({sessions_since_start} of {hold} sessions since start)"
    if n < spec["min_n"]:
        return f"too few ({n} of {spec['min_n']})"
    if rec["avg_pct"] <= 0:
        return "FAIL"
    if rec["avg_pct"] >= spec["pass_mean"] and (spec["pass_win"] is None or rec["win_pct"] >= spec["pass_win"]):
        return "PASS"
    return "below the mark — keep recording"


def verdict_intraday(rec: dict) -> str:
    n = rec.get("trades", 0)
    if n < INTRADAY_MIN_N:
        return f"too few ({n} of {INTRADAY_MIN_N})"
    if rec["avg_pct"] <= 0:
        return "FAIL"
    return "PASS" if rec["avg_pct"] >= INTRADAY_PASS_MEAN else "below the mark — keep recording"


def verdict_rotation(book: dict, control: dict) -> str:
    n = book["decisions"]
    if n < ROTATION_MIN_DECISIONS:
        return f"too few ({n} of {ROTATION_MIN_DECISIONS} decisions)"
    behind = control["since_start_pct"] - book["since_start_pct"]
    smaller_dd = book["worst_dd_pct"] > control["worst_dd_pct"]      # drawdowns are negative
    if smaller_dd and behind <= ROTATION_MAX_BEHIND:
        return "PASS"
    if behind > ROTATION_MAX_BEHIND and not smaller_dd:
        return "FAIL"
    return "below the mark — keep recording"


def summarise(rows: list[dict]) -> dict:
    if not rows:
        return {"trades": 0}
    p = np.array([r["pct"] for r in rows])
    return {"trades": len(rows), "win_pct": round(float((p > 0).mean() * 100), 1),
            "avg_pct": round(float(p.mean()), 3), "worst_pct": round(float(p.min()), 2),
            "total_usd": round(sum(r["usd"] for r in rows), 2)}


# ── conditions at the signal date (EVALUATION_CONDITIONS_V1 definitions) ──


def spy_tags(spy: pd.Series) -> dict[str, dict]:
    sma200 = spy.rolling(200).mean()
    vol = spy.pct_change().rolling(20).std() * math.sqrt(252)
    out = {}
    for t in spy.index:
        s, v = sma200.loc[t], vol.loc[t]
        out[str(t.date())] = {
            "market": None if pd.isna(s) else ("up" if spy.loc[t] >= s else "down"),
            "volatility": None if pd.isna(v) else ("calm" if v < 0.15 else "normal" if v <= 0.25 else "stressed")}
    return out


# ── the daily replay ───────────────────────────────────────────────────


def _adjusted(sym: str) -> pd.DataFrame | None:
    from .daily_bars import load_daily
    df = load_daily(sym, require_ohlc=True, lookback_days=720, fetched_by="shadow-books")
    if df is None or not len(df):
        return None
    f = df["adj_factor"].fillna(1.0).astype(float) if "adj_factor" in df.columns else 1.0
    out = pd.DataFrame({"open": df["open"].astype(float) * f, "close": df["close"].astype(float) * f,
                        "volume": df["volume"].astype(float) if "volume" in df.columns else np.nan})
    out.index = pd.to_datetime([str(t)[:10] for t in df.index])
    return out[~out.index.duplicated(keep="last")].sort_index()


def _censoring_caveat(closed: list[dict], open_: list[dict], spec: dict,
                      sessions_since_start: int | None = None) -> str | None:
    """A record of CLOSED trades is losers-first until the timeout has had time
    to land: a stop closes in days, a winner rides to the timeout. The first run
    (21 Nov 2026) showed the pullback book at -5.2%/trade, 13% wins — with 61
    trades still open at +0.4%. The number was right and the reading was wrong."""
    if not open_:
        return None
    hold = spec["rule"].MAX_HOLD
    if len(open_) >= len(closed) or not closed or (sessions_since_start is not None and sessions_since_start < hold):
        return (f"{len(open_)} trades are still OPEN (timeout {hold} sessions) against {len(closed)} closed; "
                f"the closed record is weighted to stops and trails until the first timeouts land")
    return None


def replay(rule, sym: str, dates: list[str], o: list[float], c: list[float], start: str,
           tags: dict[str, dict], kind: str = "sector_stock") -> tuple[list[dict], list[dict], list[str]]:
    """(closed, open, dropped) trades for one symbol. One position at a time;
    signal on close i → buy open i+1; exit on close j → sell open j+1."""
    closed, still_open, dropped = [], [], []
    n, i = len(c), 0
    while i < n - 1:
        if dates[i] < start or not rule.entry_signal(c, i):
            i += 1
            continue
        k = i + 1                                   # the fill bar
        fill = o[k]
        if not fill or fill <= 0 or abs(fill / c[i] - 1) > MAX_DAY_MOVE:
            dropped.append(f"{sym} {dates[i]}: entry open {fill} vs close {c[i]:.2f}")
            i += 1
            continue
        exit_j, why, bad = None, None, None
        for j in range(k, n):
            if abs(c[j] / c[j - 1] - 1) > MAX_DAY_MOVE:
                bad = f"{sym} {dates[j]}: one-day move {100 * (c[j] / c[j - 1] - 1):+.0f}%"
                break
            go, why = rule.exit_decision(c, j, fill_price=fill, bars_held=j - k)
            if go:
                exit_j = j
                break
        tag = tags.get(dates[i], {})
        base = {"symbol": sym, "kind": kind, "signal_date": dates[i], "entry_date": dates[k], "entry": round(fill, 2),
                "market": tag.get("market"), "volatility": tag.get("volatility")}
        if bad:
            dropped.append(bad)
            i = j + 1
            continue
        if exit_j is None or exit_j + 1 >= n:
            # Not exited, or exit signalled on the LAST close (sells at an open
            # that has not happened): open, marked at the latest close.
            pct = c[-1] / fill - 1 - COST
            still_open.append({**base, "mark": round(c[-1], 2), "mark_date": dates[-1],
                               "exit_signalled": why if exit_j is not None else None,
                               "pct": round(pct * 100, 3), "usd": round(NOTIONAL * pct, 2)})
            break
        px = o[exit_j + 1]
        if not px or px <= 0 or abs(px / c[exit_j] - 1) > MAX_DAY_MOVE:
            # The SELL fill is a bar too: a corrupt open books a fiction either way.
            dropped.append(f"{sym} {dates[exit_j + 1]}: exit open {px} vs close {c[exit_j]:.2f}")
            i = exit_j + 2
            continue
        pct = px / fill - 1 - COST
        closed.append({**base, "exit_date": dates[exit_j + 1], "exit": round(px, 2), "exit_why": why,
                       "sessions": exit_j + 1 - k, "pct": round(pct * 100, 3), "usd": round(NOTIONAL * pct, 2)})
        i = exit_j + 1
    return closed, still_open, dropped


def _no_sector_symbols() -> set[str]:
    """The repo's own split (momentum_rotation.load_universe): a harvested name
    with no sector label is an ETF or fund — and, honestly, also a non-S&P stock
    like MDB. Hence the kind is named "no_sector", not "etf"."""
    from .universe import load_universe
    return {r["symbol"] for r in load_universe()["symbols"] if not r.get("sector")}


def daily_books(symbols: list[str] | None = None) -> dict:
    from .universe import poison_check, universe_symbols
    syms = symbols or universe_symbols()
    spy = _adjusted("SPY")
    if spy is None:
        raise RuntimeError("SPY has no daily bars; no book can be tagged")
    tags = spy_tags(spy["close"])
    last_session = str(spy.index[-1].date())
    acc = {k: {"closed": [], "open": [], "dropped": []} for k in DAILY_BOOKS}
    missing, poisoned, stale = [], [], []
    no_sector = _no_sector_symbols()
    for s in syms:
        kind = "no_sector" if s in no_sector else "sector_stock"
        df = _adjusted(s)
        if df is None or len(df) < MR.MIN_BARS:
            missing.append(s)
            continue
        if not poison_check(df["close"].tolist(), df["volume"].fillna(0).tolist())[0]:
            poisoned.append(s)
            continue
        if str(df.index[-1].date()) < last_session:
            stale.append(f"{s} (bars to {df.index[-1].date()})")
        d = [str(t.date()) for t in df.index]
        o, c = df["open"].tolist(), df["close"].tolist()
        for key, spec in DAILY_BOOKS.items():
            cl, op, dr = replay(spec["rule"], s, d, o, c, spec["start"], tags, kind)
            acc[key]["closed"] += cl
            acc[key]["open"] += op
            acc[key]["dropped"] += dr
    books = {}
    for key, spec in DAILY_BOOKS.items():
        cl = sorted(acc[key]["closed"], key=lambda r: (r["exit_date"], r["symbol"]))
        rec = summarise(cl)
        since = int((spy.index >= pd.Timestamp(spec["start"])).sum())
        books[key] = {
            "name": spec["name"], "record_start": spec["start"], "record": rec,
            "sessions_since_start": since, "timeout_sessions": spec["rule"].MAX_HOLD,
            "verdict": verdict_daily(rec, spec, since),
            "pass_mark": {"min_n": spec["min_n"], "mean_pct": spec["pass_mean"], "win_pct": spec["pass_win"]},
            "backtest": spec["backtest"],
            "by_market": {m: summarise([r for r in cl if r["market"] == m]) for m in ("up", "down")},
            "by_volatility": {v: summarise([r for r in cl if r["volatility"] == v])
                              for v in ("calm", "normal", "stressed")},
            # ADDED AFTER THE FIRST RUN (21 Nov 2026), as a display split only — the
            # pass marks above are untouched. Four of the first closes were
            # SPY/VOO/IVV/VTI: one trade counted four times. No-sector = the repo's ETF rule.
            "by_kind": {k: summarise([r for r in cl if r["kind"] == k]) for k in ("sector_stock", "no_sector")},
            "open": sorted(acc[key]["open"], key=lambda r: r["entry_date"]),
            "open_summary": summarise(acc[key]["open"]),
            "caveat": _censoring_caveat(cl, acc[key]["open"], spec, since),
            "closed_recent": cl[-25:],
            "entered_last_session": [r for r in acc[key]["open"] + cl if r["entry_date"] == last_session],
            "dropped": acc[key]["dropped"],
        }
    return {"last_session": last_session, "universe_n": len(syms), "missing": missing,
            "poisoned": poisoned, "stale": stale, "books": books}


# ── the defensive rotation (V4 survivor) ───────────────────────────────


def _rotation_line(r: dict) -> dict:
    nav = r["nav"]
    dd = float((nav / nav.cummax() - 1).min() * 100) if len(nav) else 0.0
    last = r["rebalances"][-1] if r["rebalances"] else {}
    return {"since_start_pct": round((float(nav.iloc[-1]) - 1) * 100, 2) if len(nav) else None,
            "worst_dd_pct": round(dd, 2), "decisions": len(r["rebalances"]),
            "latest_picks": list(last.get("picks") or []),
            "time_in_cash_pct": r["activity"].get("time_in_cash_pct"),
            "market_exits": r["activity"].get("market_exits", 0),
            "stop_exits": r["activity"].get("trailing_stop_exits", 0),
            "as_of": str(nav.index[-1].date()) if len(nav) else None}


def rotation_defensive() -> dict:
    from . import momentum_rotation as M
    panels = M.cached_panels(None)
    ctl = _rotation_line(M.backtest(M.with_overrides(start=ROTATION_START), panels))
    dfn = _rotation_line(M.backtest(M.with_overrides(start=ROTATION_START, market_exit="spy50_daily"), panels))
    return {"name": "Rotation, defensive — sell everything when SPY closes below its 50-day",
            "record_start": ROTATION_START, "book": dfn, "control": ctl,
            "verdict": verdict_rotation(dfn, ctl),
            "pass_mark": {"min_decisions": ROTATION_MIN_DECISIONS, "max_points_behind": ROTATION_MAX_BEHIND,
                          "and": "worst drawdown smaller than the control's"},
            "backtest": {"cagr_pct": 6.42, "worst_dd_pct": -11.77, "control_cagr_pct": 9.51,
                         "control_worst_dd_pct": -14.36, "note": "V4, 2008–2026"}}


# ── intraday rules split by the SPY 200-day gate ───────────────────────


def intraday_gate() -> dict:
    from . import intraday_shadow as S
    a = S.build()
    spy = _adjusted("SPY")
    sma = spy["close"].rolling(200).mean()
    below: dict[str, bool] = {}
    idx = list(spy.index)
    for k in range(1, len(idx)):           # gate = the PRIOR session's close vs its 200-day
        prev = idx[k - 1]
        if not pd.isna(sma.loc[prev]):
            below[str(idx[k].date())] = bool(spy["close"].loc[prev] < sma.loc[prev])
    final = [t for t in a["trades"] if t.get("final")]
    out = {}
    for rule in ("rule2", "rule7"):
        rows = [t for t in final if t["rule"] == rule]
        g = [t for t in rows if below.get(t["date"]) is True]
        rec = summarise(g)
        out[rule] = {"name": a["rules"][rule]["name"], "gated_spy_below_200d": rec,
                     "ungated": summarise([t for t in rows if below.get(t["date"]) is False]),
                     "untagged": sum(1 for t in rows if t["date"] not in below),
                     "verdict": verdict_intraday(rec)}
    return {"record_start": a["record_start"], "gate": "SPY prior close below its 200-day",
            "pass_mark": {"min_n": INTRADAY_MIN_N, "mean_pct": INTRADAY_PASS_MEAN}, "rules": out}


# ── the scoreboard ─────────────────────────────────────────────────────


def build(symbols: list[str] | None = None) -> dict:
    out = {"as_of_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"), "version": VERSION,
           "errors": {}, "placed_orders": 0,
           "how": "Frozen rules replayed on settled bars from the date each was frozen. No order is ever sent."}
    for key, fn in (("daily", lambda: daily_books(symbols)), ("rotation_defensive", rotation_defensive),
                    ("intraday_gate", intraday_gate)):
        try:
            out[key] = fn()
        except Exception as exc:  # noqa: BLE001 — one book failing must not hide the others; it is NAMED
            out["errors"][key] = f"{type(exc).__name__}: {str(exc)[:300]}"
    out["scoreboard"] = scoreboard(out)
    return out


def scoreboard(a: dict) -> list[dict]:
    rows = []
    for key, b in (a.get("daily") or {}).get("books", {}).items():
        r = b["record"]
        rows.append({"book": key, "name": b["name"], "since": b["record_start"], "n": r.get("trades", 0),
                     "win_pct": r.get("win_pct"), "avg_pct": r.get("avg_pct"), "worst_pct": r.get("worst_pct"),
                     "open": b["open_summary"].get("trades", 0), "open_avg_pct": b["open_summary"].get("avg_pct"),
                     "open_usd": b["open_summary"].get("total_usd"), "closed_usd": r.get("total_usd"),
                     "sector_stocks_only": b["by_kind"]["sector_stock"], "verdict": b["verdict"], "caveat": b["caveat"]})
    rd = a.get("rotation_defensive")
    if rd:
        rows.append({"book": "rotation_defensive", "name": rd["name"], "since": rd["record_start"],
                     "n": rd["book"]["decisions"], "return_pct": rd["book"]["since_start_pct"],
                     "worst_dd_pct": rd["book"]["worst_dd_pct"],
                     "control_return_pct": rd["control"]["since_start_pct"],
                     "control_worst_dd_pct": rd["control"]["worst_dd_pct"], "verdict": rd["verdict"]})
    for rule, g in ((a.get("intraday_gate") or {}).get("rules") or {}).items():
        r = g["gated_spy_below_200d"]
        rows.append({"book": f"{rule}_spy_below_200d", "name": f"{g['name']} — only when SPY < 200-day",
                     "since": a["intraday_gate"]["record_start"], "n": r.get("trades", 0),
                     "win_pct": r.get("win_pct"), "avg_pct": r.get("avg_pct"), "verdict": g["verdict"]})
    for key in a.get("errors", {}):
        rows.append({"book": key, "name": key, "n": None, "verdict": f"NOT COMPUTED — {a['errors'][key]}"})
    return rows

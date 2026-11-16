"""The daily evaluation: one dated record per session for every strategy we run
(owner, 16 Nov 2026: "i just need every day evaluation", "and then we can
evaluate what is the best strategy").

Built after the US close from SETTLED data — daily closes in the golden store,
broker positions, OMS orders, the intraday forward record — and published to
today-setups/daily-evaluation, which the API archives one row per day. Nothing
here places, cancels or changes an order.

Every result carries the condition tags fixed in EVALUATION_CONDITIONS_V1.md,
so the record builds up by condition: a strategy can be judged on sector
sell-off days apart from quiet ones without anyone slicing results afterwards.

What is NOT claimed: a lesson. The record states what happened; drawing the
lesson is a judgement and is left to the reader (or a later, separate step).
"""
from __future__ import annotations

import math
from datetime import date, datetime, timezone

import pandas as pd

ROTATION_SID = "momentum_rotation_ibkr"
CONDITIONS_VERSION = "EVALUATION_CONDITIONS_V1"


# ── settled closes ─────────────────────────────────────────────────────


def _closes(sym: str, lookback_days: int = 420) -> pd.Series | None:
    from .daily_bars import load_bars
    df = load_bars(sym, "1d", lookback_days=lookback_days, fetched_by="daily-evaluation")
    if df is None or not len(df):
        return None
    f = df["adj_factor"].fillna(1.0).astype(float) if "adj_factor" in df.columns else 1.0
    c = (df["close"].astype(float) * f)
    c.index = pd.to_datetime([str(t)[:10] for t in c.index])
    return c[~c.index.duplicated(keep="last")].sort_index()


def _opens(sym: str, lookback_days: int = 60) -> pd.Series | None:
    from .daily_bars import load_bars
    df = load_bars(sym, "1d", lookback_days=lookback_days, fetched_by="daily-evaluation")
    if df is None or not len(df):
        return None
    f = df["adj_factor"].fillna(1.0).astype(float) if "adj_factor" in df.columns else 1.0
    o = (df["open"].astype(float) * f)
    o.index = pd.to_datetime([str(t)[:10] for t in o.index])
    return o[~o.index.duplicated(keep="last")].sort_index()


def _ret(c: pd.Series, n: int) -> float | None:
    return float(c.iloc[-1] / c.iloc[-1 - n] - 1) if len(c) > n else None


# ── conditions (EVALUATION_CONDITIONS_V1 — do not change in place) ─────


def conditions(session: date) -> dict:
    from .momentum_rotation import SECTOR_ETF
    out: dict = {"version": CONDITIONS_VERSION, "session": str(session)}
    spy, qqq = _closes("SPY"), _closes("QQQ")
    if spy is not None:
        spy = spy[spy.index <= pd.Timestamp(session)]
        if len(spy) >= 200:
            out["market_trend"] = "up" if spy.iloc[-1] >= spy.tail(200).mean() else "down"
        if len(spy) >= 21:
            vol = float(spy.pct_change().tail(20).std() * math.sqrt(252))
            out["spy_vol_20d_pct"] = round(vol * 100, 1)
            out["volatility"] = "calm" if vol < 0.15 else "normal" if vol <= 0.25 else "stressed"
        out["spy_day_pct"] = round((_ret(spy, 1) or 0) * 100, 2)
    if qqq is not None:
        qqq = qqq[qqq.index <= pd.Timestamp(session)]
        r = _ret(qqq, 1)
        if r is not None:
            out["qqq_day_pct"] = round(r * 100, 2)
            out["day_type"] = "Up" if r > 0.005 else "Down" if r < -0.005 else "Flat"
    sel = {}
    for sector, etf in SECTOR_ETF.items():
        c = _closes(etf, 60)
        if c is None:
            continue
        c = c[c.index <= pd.Timestamp(session)]
        r5 = _ret(c, 5)
        if r5 is not None:
            sel[sector] = {"etf": etf, "ret_5d_pct": round(r5 * 100, 2), "selloff": r5 <= -0.03}
    out["sectors"] = sel
    out["sectors_in_selloff"] = sorted(k for k, v in sel.items() if v["selloff"])
    smh = _closes("SMH", 60)
    if smh is not None:
        smh = smh[smh.index <= pd.Timestamp(session)]
        r5 = _ret(smh, 5)
        if r5 is not None:
            out["smh_ret_5d_pct"] = round(r5 * 100, 2)
            out["semis_selloff"] = r5 <= -0.03
    return out


# ── the momentum rotation, as the broker holds it ──────────────────────


def rotation(session: date, conds: dict, base: str, token: str) -> dict:
    """Per holding: shares (broker), our fill, the session's close, the day's
    P&L and the P&L since the buy — from settled closes, never a live print."""
    import requests

    from .momentum_rotation import SECTOR_ETF  # noqa: F401  (sector names)
    from .momentum_rotation import load_universe
    h = {"Authorization": f"Bearer {token}"} if token else {}
    board = requests.get(f"{base}/api/today-setups/momentum_rotation/latest", headers=h, timeout=60).json()["artifact"]
    orders = requests.get(f"{base}/api/oms/orders", params={"strategyId": ROTATION_SID}, headers=h, timeout=60).json()
    orders = orders if isinstance(orders, list) else orders.get("orders", [])
    acc = [a for a in requests.get(f"{base}/api/integrations/account-state", headers=h, timeout=60).json()["accounts"]
           if a.get("broker") == "IBKR_PAPER"]
    broker = {p["symbol"]: p for p in (acc[0]["positions"] if acc else [])}
    _, sectors = load_universe()

    held = [r["symbol"] for r in board.get("rows", []) if r.get("action") in ("HOLD", "SELL")]
    today_orders = [o for o in orders if str(o.get("createdAtUtc", ""))[:10] == str(session)]
    buys = {}
    for o in sorted(orders, key=lambda o: str(o.get("createdAtUtc"))):
        if o.get("side") == "BUY" and o.get("state") == "FILLED":
            buys[o["symbol"].split("_")[0]] = o
    rows, problems = [], []
    for s in held:
        c = _closes(s, 30)
        bp = broker.get(s)
        b = buys.get(s)
        if c is None or bp is None or b is None:
            problems.append(f"{s}: " + ("no closes" if c is None else "not held at the broker" if bp is None
                                        else "no filled BUY in the OMS"))
            continue
        c = c[c.index <= pd.Timestamp(session)]
        if not len(c) or c.index[-1].date() != session:
            problems.append(f"{s}: no settled close for {session} yet")
            continue
        qty, fill = float(bp["qty"]), float(b.get("avgFillPrice") or bp.get("avgCost"))
        bought_on = str(b["createdAtUtc"])[:10]
        if bought_on > str(session):
            # A close from BEFORE the buy is never a P&L price (16 Nov: the
            # first run valued Monday's buys at Friday's close, +2.98%).
            problems.append(f"{s}: bought {bought_on}, after the evaluated session {session} — not valued")
            continue
        close = float(c.iloc[-1])
        ref = fill if bought_on == str(session) else float(c.iloc[-2])
        sector = sectors.get(s)
        rows.append({"symbol": s, "sector": sector, "qty": qty, "fill": round(fill, 2), "bought_on": bought_on,
                     "close": round(close, 2), "day_pnl_usd": round(qty * (close - ref), 2),
                     "day_pct": round((close / ref - 1) * 100, 2),
                     "pnl_usd": round(qty * (close - fill), 2), "pnl_pct": round((close / fill - 1) * 100, 2),
                     "cost_usd": round(qty * fill, 2), "value_usd": round(qty * close, 2),
                     "in_sector_selloff": sector in set(conds.get("sectors_in_selloff", [])),
                     "broker_qty_matches_board": abs(qty - float(next((r.get("qty") or 0) for r in board["rows"]
                                                                       if r["symbol"] == s))) < 1e-6})
    cost = sum(r["cost_usd"] for r in rows)
    first_buy = min((r["bought_on"] for r in rows), default=None)
    # BENCHMARKS FROM THE SAME OPEN WE BOUGHT AT (16 Nov 2026). The first
    # version measured SPY/QQQ from the close BEFORE the first buy, so they
    # carried the opening gap the book never took: on a gap-down day the book
    # "beat" SPY by 1.3 points it had not earned. Same rule as every other
    # figure here: the starting price must come from AFTER the trade.
    bench = {}
    for b_ in ("SPY", "QQQ"):
        o_ = _opens(b_, 60)
        c = _closes(b_, 60)
        if c is not None and o_ is not None and first_buy and pd.Timestamp(first_buy) in o_.index:
            c = c[c.index <= pd.Timestamp(session)]
            bench[b_] = round((float(c.iloc[-1]) / float(o_.loc[pd.Timestamp(first_buy)]) - 1) * 100, 2)
    return {
        "strategy": "Momentum rotation (paper, IBKR)", "strategy_id": ROTATION_SID,
        "holdings": rows, "problems": problems,
        "book": {"names": len(rows), "cost_usd": round(cost, 2),
                 "value_usd": round(sum(r["value_usd"] for r in rows), 2),
                 "day_pnl_usd": round(sum(r["day_pnl_usd"] for r in rows), 2),
                 "pnl_usd": round(sum(r["pnl_usd"] for r in rows), 2),
                 "pnl_pct": round(sum(r["pnl_usd"] for r in rows) / cost * 100, 2) if cost else None,
                 "since": first_buy, "benchmarks_since_pct": bench,
                 "benchmark_basis": "SPY/QQQ from the open of the first buy day to the session close",
                 "in_sector_selloff": [r["symbol"] for r in rows if r["in_sector_selloff"]]},
        "orders_today": [{"symbol": o["symbol"].split("_")[0], "side": o["side"], "qty": o["qty"],
                          "type": o.get("orderType"), "state": o["state"], "fill": o.get("avgFillPrice")}
                         for o in today_orders],
        "next_decision": board.get("next_signal_date"),
    }


# ── the intraday forward record ────────────────────────────────────────


def intraday(session: date) -> dict:
    from . import intraday_shadow as S
    a = S.build()
    return {"latest_session": a["latest_session"], "latest_session_final": a["latest_session_final"],
            "trades_today": [t for t in a["trades"] if t["date"] == str(session)],
            "records": {k: {"name": r["name"], "record": r["record"], "backtest_avg_pct": r["backtest"]["avg_pct"],
                            "by_day": r["by_day"], "by_breakout_volume": r.get("by_breakout_volume")}
                        for k, r in a["rules"].items()},
            "missing": a["missing"], "stale": a["stale"]}


# ── the day ────────────────────────────────────────────────────────────


def session_to_evaluate() -> date:
    """The latest session SPY has a settled daily close for."""
    c = _closes("SPY", 15)
    if c is None or not len(c):
        raise RuntimeError("SPY has no daily bars; nothing can be evaluated")
    return c.index[-1].date()


class NotSettled(RuntimeError):
    """The closes for the session to evaluate are not in the store yet."""


def build(base: str, token: str, session: date | None = None) -> dict:
    explicit = session is not None
    session = session or session_to_evaluate()
    if not explicit and session < date.today() and datetime.now(timezone.utc).weekday() < 5 and \
            datetime.now(timezone.utc).hour >= 21:
        # After a weekday close the evaluation is of TODAY. An older SPY close
        # means the harvest has not written today's bars: say so, publish nothing.
        raise NotSettled(f"latest settled SPY close is {session}; today's closes are not in the store yet")
    conds = conditions(session)
    out = {"as_of_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
           "session": str(session), "conditions": conds, "strategies": {}, "errors": {}}
    for key, fn in (("momentum_rotation", lambda: rotation(session, conds, base, token)),
                    ("intraday_rules", lambda: intraday(session))):
        try:
            out["strategies"][key] = fn()
        except Exception as exc:  # noqa: BLE001 — one strategy failing must not hide the others; it is NAMED
            out["errors"][key] = str(exc)[:300]
    out["placed_orders"] = 0
    return out


def text_summary(e: dict) -> str:
    c = e["conditions"]
    lines = [f"Daily evaluation — session {e['session']}",
             f"Conditions ({c.get('version')}): market {c.get('market_trend')}, day {c.get('day_type')} "
             f"(QQQ {c.get('qqq_day_pct')}%, SPY {c.get('spy_day_pct')}%), volatility {c.get('volatility')} "
             f"({c.get('spy_vol_20d_pct')}%), sector sell-offs: {', '.join(c.get('sectors_in_selloff') or []) or 'none'}"
             + (f", semis sell-off: {c.get('semis_selloff')}" if 'semis_selloff' in c else ""), ""]
    r = e["strategies"].get("momentum_rotation")
    if r:
        b = r["book"]
        lines.append(f"MOMENTUM ROTATION — {b['names']} names, day {b['day_pnl_usd']:+,.0f} $, "
                     f"since {b['since']} {b['pnl_usd']:+,.0f} $ ({b['pnl_pct']:+.2f}%) vs "
                     + ", ".join(f"{k} {v:+.2f}%" for k, v in b["benchmarks_since_pct"].items()))
        for h in sorted(r["holdings"], key=lambda x: x["pnl_usd"]):
            lines.append(f"  {h['symbol']:6s} {h['qty']:>5.0f} sh  fill {h['fill']:>9.2f}  close {h['close']:>9.2f}  "
                         f"day {h['day_pnl_usd']:+7.0f} $  total {h['pnl_usd']:+7.0f} $ ({h['pnl_pct']:+.2f}%)"
                         + ("  [sector sell-off]" if h["in_sector_selloff"] else ""))
        for p in r["problems"]:
            lines.append(f"  ! {p}")
        lines.append(f"  orders today: {len(r['orders_today'])}; next decision {r['next_decision']}")
        lines.append("")
    i = e["strategies"].get("intraday_rules")
    if i:
        lines.append(f"INTRADAY RULES (forward record, no orders) — session {i['latest_session']}"
                     + ("" if i["latest_session_final"] else " (not final)"))
        for t in i["trades_today"]:
            lines.append(f"  {t['rule']} {t['symbol']} {t['side']} {t['entry_time']} {t['entry']} -> {t['exit']} "
                         f"{t['exit_why']} {t['pct']:+.2f}% ({t['usd']:+.0f} $)")
        for k, rr in i["records"].items():
            rec = rr["record"]
            lines.append(f"  {k} record: " + (f"{rec['trades']} trades, avg {rec['avg_pct']:+.3f}% "
                                               f"(backtest {rr['backtest_avg_pct']:+.2f}%), total {rec['total_usd']:+,.0f} $"
                                               if rec.get("trades") else "no finished trades yet"))
        lines.append("")
    for k, v in e["errors"].items():
        lines.append(f"! {k} could not be evaluated: {v}")
    lines.append("Nothing in this evaluation placed an order.")
    return "\n".join(lines)

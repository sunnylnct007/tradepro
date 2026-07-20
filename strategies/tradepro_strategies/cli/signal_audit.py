"""tradepro-signal-audit — Signal vs Position audit + the HONEST NLV P&L.

Two blind spots this surfaces (both caught by the trader on the live book):

1. EXITS NOT FIRING. For each strategy we read the BROKER-GOLDEN held positions
   and re-run the trader's STATEFUL Ichimoku signal (_equity_trader_signal) on
   each held name. If the signal says FLAT (exit) but we're still holding, that's
   an `exit_overdue` — a loser whose exit fired days ago and never executed
   (STRL/STX/AMKR...). If a held name isn't in the bar cache we're `blind` — we
   can't even evaluate its exit.

2. P&L THAT HIDES REALIZED LOSSES. The desk's P&L shows unrealised-on-held, which
   can read ~flat while the ACCOUNT is down. We anchor to NLV-vs-starting-capital
   so realized churn/cost/FX losses (the −£2k on the T212 control) can't hide:
     total = nlv − start ;  realized_and_costs = total − unrealised_on_held

    uv run tradepro-signal-audit --strategy ichimoku_equity --json
    uv run tradepro-signal-audit --strategy ichimoku_equity --push
"""
from __future__ import annotations

import argparse
import datetime as _dt
import glob
import logging
import os

from ..ticker_renames import canonical_ticker

log = logging.getLogger("tradepro.signal_audit")

# strategy → how to read its golden book + its starting capital.
_STRATEGIES = {
    "ichimoku_equity": {
        "broker": "T212_DEMO", "source": "t212", "start_capital": 50_000.0, "ccy": "GBP",
        "universes": ["large_50", "high_beta"],   # for missed-BUY detection
    },
    "ichimoku_equity_ibkr": {
        "broker": "IBKR_PAPER", "source": "account-state", "start_capital": 1_000_000.0, "ccy": "USD",
        "universes": ["large_50"],
    },
}


def _load_daily(cache_dir: str, sym: str):
    import pandas as pd

    files = sorted(glob.glob(f"{cache_dir}/{sym}/1d/*.parquet"))
    if not files:
        return None
    df = pd.concat([pd.read_parquet(f) for f in files])
    df.columns = [c.lower() for c in df.columns]
    df.index = pd.to_datetime(df.index)
    try:
        df.index = df.index.tz_localize(None)
    except (TypeError, AttributeError):
        pass
    df = df[~df.index.duplicated(keep="last")].sort_index()
    return df.rename(columns={"high": "High", "low": "Low", "close": "Close"})


def _signal(df):
    """(position 0/1, last exit-flip date or None). None position if too short."""
    from ..paper.strategies._equity_trader_signal import (
        compute_indicators, compute_position, MIN_BARS,
    )
    if df is None or len(df) < MIN_BARS:
        return None, None
    pos = compute_position(compute_indicators(df))["position"]
    cur = float(pos.iloc[-1])
    flat = pos[(pos == 0) & (pos.shift() != 0)]
    last_exit = flat.index[-1].date().isoformat() if len(flat) else None
    return cur, last_exit


def _fetch_book(base: str, headers: dict, cfg: dict) -> tuple[list[dict], dict]:
    """Return (positions, account) from the broker golden source.
    positions: [{symbol, qty, entry, price}]; account: {nlv, cash, invested_cost, unrealised}."""
    import requests

    if cfg["source"] == "t212":
        pj = requests.get(f"{base}/api/integrations/trading212/positions",
                          headers=headers, timeout=25).json()
        cj = requests.get(f"{base}/api/integrations/trading212/cash",
                          headers=headers, timeout=25).json()
        # Canonicalise corporate-action renames (broker reports LB/FB; the
        # universe/signal/bar-cache use BBWI/META) so held names match the
        # signal — else BBWI shows as a "missed buy" and LB/FB read "blind".
        positions = [
            {"symbol": canonical_ticker(p.get("yahooSymbol")), "qty": p.get("quantity") or 0,
             "entry": p.get("averagePricePaid"), "price": p.get("currentPrice")}
            for p in pj.get("positions", [])
        ]
        account = {
            "nlv": cj.get("total"), "cash": cj.get("free"),
            "invested_cost": cj.get("invested"), "unrealised": cj.get("ppl"),
        }
        return positions, account

    # account-state (IBKR): whole-account book + NLV
    aj = requests.get(f"{base}/api/integrations/account-state", headers=headers, timeout=25).json()
    acct = next((a for a in aj.get("accounts", []) if a.get("broker") == cfg["broker"]), None)
    if acct is None:
        return [], {}
    # equity only (skip options: 100x multiplier heuristic) for the signal audit
    positions = []
    inv_cost = 0.0
    for p in acct.get("positions", []):
        q = p.get("qty") or 0
        if q <= 0:
            continue
        mv, mark = p.get("marketValue"), p.get("mark")
        is_opt = mark and mv and q and abs(abs(mv) / (abs(q) * mark) - 100) < 10
        if is_opt:
            continue
        inv_cost += (p.get("avgCost") or 0) * q
        positions.append({"symbol": canonical_ticker(p.get("symbol")), "qty": q,
                          "entry": p.get("avgCost"), "price": p.get("mark")})
    account = {"nlv": acct.get("netLiquidation"), "cash": acct.get("totalCash"),
               "invested_cost": inv_cost, "unrealised": acct.get("unrealisedPnl")}
    return positions, account


def audit(strategy: str, base: str, headers: dict, cache_dir: str) -> dict:
    cfg = _STRATEGIES[strategy]
    positions, account = _fetch_book(base, headers, cfg)

    rows = []
    for p in positions:
        sym, qty, entry, price = p["symbol"], p["qty"], p["entry"], p["price"]
        pos, last_exit = _signal(_load_daily(cache_dir, sym)) if sym else (None, None)
        pnl_pct = ((price / entry - 1) * 100) if (entry and price) else None
        if pos is None:
            cls = "blind"        # held but we have no bars → can't evaluate the exit
        elif pos == 0.0:
            cls = "exit_overdue"  # signal says SELL, still held
        else:
            cls = "hold"          # signal still long — correctly held
        days_overdue = None
        if cls == "exit_overdue" and last_exit:
            d = (_dt.date.today() - _dt.date.fromisoformat(last_exit)).days
            days_overdue = d
        rows.append({
            "symbol": sym, "qty": qty,
            "entry": round(entry, 2) if entry else None,
            "price": round(price, 2) if price else None,
            "pnl_pct": round(pnl_pct, 1) if pnl_pct is not None else None,
            "classification": cls,
            "exit_fired": last_exit if cls == "exit_overdue" else None,
            "days_overdue": days_overdue,
        })

    # Missed BUYs: universe names whose signal says LONG but we're FLAT (not held) —
    # the ENTRY half of the signal-execution gap. Surfaced even when auto-entry is
    # gated off (--reconcile-entries), so the trader SEES what the strategy would buy.
    import requests as _rq
    held_syms = {p["symbol"] for p in positions if p.get("symbol")}
    missed_buys: list[dict] = []
    for uname in cfg.get("universes", []):
        try:
            usyms = [s["ticker"] for s in _rq.get(
                f"{base}/api/universes/{uname}", headers=headers, timeout=15
            ).json().get("symbols", []) if s.get("effective", True)]
        except Exception:  # noqa: BLE001
            usyms = []
        for us in usyms:
            if us in held_syms or any(m["symbol"] == us for m in missed_buys):
                continue
            pos, _ = _signal(_load_daily(cache_dir, us))
            if pos == 1.0:  # signal LONG but we hold none → missed buy candidate
                missed_buys.append({"symbol": us, "universe": uname})
    missed_buys.sort(key=lambda m: m["symbol"])

    # Honest P&L: NLV vs start, splitting realized+costs from unrealised-on-held.
    start = cfg["start_capital"]
    nlv = account.get("nlv")
    unreal = account.get("unrealised")
    total_pnl = (nlv - start) if nlv is not None else None
    realized_and_costs = (total_pnl - unreal) if (total_pnl is not None and unreal is not None) else None

    def n(c): return sum(r["classification"] == c for r in rows)
    overdue = [r for r in rows if r["classification"] == "exit_overdue"]
    overdue.sort(key=lambda r: r["pnl_pct"] if r["pnl_pct"] is not None else 0)

    return {
        "kind": "signal_audit", "strategy": strategy, "broker": cfg["broker"],
        "currency": cfg["ccy"],
        "as_of_utc": _dt.datetime.now(_dt.UTC).isoformat(),
        "pnl": {
            "start_capital": start, "nlv": nlv, "cash": account.get("cash"),
            "invested_cost": account.get("invested_cost"),
            "unrealised_on_held": unreal, "total_pnl": total_pnl,
            "realized_and_costs": realized_and_costs,
            "total_pnl_pct": round(total_pnl / start * 100, 2) if total_pnl is not None else None,
        },
        "counts": {"held": len(rows), "hold": n("hold"),
                   "exit_overdue": n("exit_overdue"), "blind": n("blind"),
                   "missed_buys": len(missed_buys)},
        "exit_overdue": overdue,
        "missed_buys": missed_buys,
        "blind": [r["symbol"] for r in rows if r["classification"] == "blind"],
        "positions": rows,
        "note": ("Signal = trader's stateful Ichimoku (exit when Close<cloud_bottom OR "
                 "tenkan<kijun). exit_overdue = signal says SELL but still held. missed_buys "
                 "= signal says LONG but we're flat (entry gap). P&L is NLV-vs-start "
                 "(realized+costs NOT hidden). blind = no bars."),
    }


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    p = argparse.ArgumentParser(prog="tradepro-signal-audit")
    p.add_argument("--strategy", default="ichimoku_equity", choices=list(_STRATEGIES))
    p.add_argument("--api-base", default=None)
    p.add_argument("--cache-dir", default=os.path.expanduser("~/.tradepro/bar_cache/us_etf"))
    p.add_argument("--json", action="store_true")
    p.add_argument("--push", action="store_true")
    args = p.parse_args()

    from . import push_to_api as _pta
    base, token = args.api_base, None
    if not base:
        base, token = _pta.load_credentials()
    base = base.rstrip("/")
    headers = {"Authorization": f"Bearer {token}"} if token else {}

    art = audit(args.strategy, base, headers, args.cache_dir)

    pnl = art["pnl"]
    log.info("%s: NLV %s vs start %s = total %s (%s%%) | held-unrealised %s | realized+costs %s",
             args.strategy, pnl["nlv"], pnl["start_capital"], pnl["total_pnl"],
             pnl["total_pnl_pct"], pnl["unrealised_on_held"], pnl["realized_and_costs"])
    log.info("counts: %s", art["counts"])
    for r in art["exit_overdue"]:
        log.info("  EXIT-OVERDUE %-6s entry=%s now=%s %s%% fired=%s (%s d overdue)",
                 r["symbol"], r["entry"], r["price"], r["pnl_pct"], r["exit_fired"], r["days_overdue"])
    if art["blind"]:
        log.info("  BLIND (no bars): %s", art["blind"])

    if args.json:
        import json
        print(json.dumps(art, indent=2))
    if args.push:
        if not token:
            _, token = _pta.load_credentials()
        _pta.push("signal-audit", {"strategy": args.strategy, "label": "latest",
                  "uploaded_by": os.uname().nodename, "artifact": art}, base, token)
        log.info("pushed signal-audit for %s", args.strategy)
        try:
            from ..run_log import log_run
            c = art["counts"]
            log_run("signal-audit", "audit", "ok", base=base, token=token,
                    summary=(f"{args.strategy}: {c.get('exit_overdue',0)} exit-overdue, "
                             f"{c.get('missed_buys',0)} missed-buys, {c.get('blind',0)} blind"))
        except Exception:  # noqa: BLE001 — heartbeat must never fail the run
            pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

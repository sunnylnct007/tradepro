"""Forward-test scorecard — did the signal become a trade, and at what price?

Owner, 11 Sep 2026: "i want trustworthy signals." Trust is not the backtest;
the backtest is a claim. Trust is the LIVE record agreeing with it, and until
now that record existed only in logs, which is precisely how a two-day
execution outage hid behind healthy-looking output.

So this answers three questions and refuses to answer any others:

  1. THE FUNNEL — of every order the strategy raised, how many reached the
     broker, how many filled, and NAMED reasons for the ones that did not.
     A lane that proposes 47 orders and fills 6 is not a lane you can judge
     on win rate yet, and the screen must say so rather than average six
     trades into a percentage.

  2. SLIPPAGE — what the screen said (signalRefPrice) versus what we paid
     (avgFillPrice). The swing backtest's achievable baseline is entry at
     the next open (+0.769%/trade); if live entries run worse than that,
     the edge is being handed to the market and no amount of backtest is
     going to rescue it. SNOW filled +20.14% above its signal on 3 Sep —
     one number that mattered more than any win rate.

  3. WHAT IS STILL OPEN — filled entries with no exit yet, marked as such,
     because an unrealised position is not a result.

Everything comes from the OMS order record (broker-confirmed fields), never
from a strategy's own log. No P&L is invented: a trade counts as closed only
when a SELL is recorded against the same symbol.
"""
from __future__ import annotations

import argparse
import collections
import datetime as dt
import json
import logging
import statistics

import requests

log = logging.getLogger("tradepro.scorecard")

# Achievable live baselines from each strategy's committed harness. A live
# mean below its baseline is the signal degrading on contact with reality.
BASELINES = {
    "mean_reversion_swing_ibkr": {
        "label": "Swing (mean reversion)",
        "harness": "backtests/studies/mean_reversion_v2.py",
        "gates_doc": "MEAN_REVERSION_GATES_V1.md",
        "backtest_mean_pct": 1.06,
        "achievable_mean_pct": 0.769,   # next-open entry, the honest baseline
        "backtest_win_pct": 72.8,
        "backtest_n": 2310,
    },
    "ichimoku_equity_ibkr": {
        "label": "Ichimoku equity",
        "harness": "backtests/studies/ichimoku_equity.py",
        "gates_doc": "ICH_EXIT_GATES_V1.md",
        "backtest_mean_pct": None,
        "achievable_mean_pct": None,
        "backtest_win_pct": None,
        "backtest_n": None,
    },
}

# Reasons an order died, in plain English. Anything unmapped is shown raw —
# never silently bucketed as "other", because the unknown reason is the one
# worth reading.
REASON_ENGLISH = {
    "stale_pending_auto_clean": "built before the market opened and swept "
                                "before it could be placed",
    "superseded by newer order": "replaced by a fresher order for the same "
                                 "signal (the daemon re-runs every 15 min)",
}


def _english_reason(state: str, raw: str | None) -> str:
    raw = (raw or "").strip()
    if not raw:
        return "no reason recorded"
    for key, text in REASON_ENGLISH.items():
        if key in raw:
            return text
    if "No Trading Permission" in raw or "Ineligible" in raw:
        return "this account is not permitted to trade that symbol"
    if "market_closed" in raw:
        return "the market was closed"
    if "risk gate" in raw:
        return f"refused by a pre-trade risk check — {raw[:90]}"
    return raw.replace("\n", " ")[:120]


def _fetch_orders(base: str, token: str, limit: int = 500) -> list[dict]:
    r = requests.get(f"{base}/api/oms/orders?limit={limit}",
                     headers={"Authorization": f"Bearer {token}"}, timeout=60)
    r.raise_for_status()
    j = r.json()
    return j if isinstance(j, list) else (j.get("orders") or j.get("items") or [])


def build(orders: list[dict], strategy_id: str) -> dict:
    rows = [o for o in orders if o.get("strategyId") == strategy_id]
    buys = [o for o in rows if str(o.get("side", "")).upper() == "BUY"]
    sells = [o for o in rows if str(o.get("side", "")).upper() == "SELL"]
    filled = [o for o in buys if o.get("state") == "FILLED"]
    reached_broker = [o for o in buys if o.get("brokerOrderId")]

    # -- the funnel, with named reasons -----------------------------------
    lost = collections.Counter()
    for o in buys:
        if o.get("state") in ("FILLED",):
            continue
        lost[(o.get("state"), _english_reason(o.get("state"), o.get("cancelledReason")))] += 1
    funnel_losses = [
        {"state": st, "reason": why, "count": n}
        for (st, why), n in lost.most_common()
    ]

    # -- slippage: what the screen said vs what we paid ---------------------
    slips, fills_detail = [], []
    for o in sorted(filled, key=lambda x: str(x.get("createdAtUtc"))):
        ref, avg = o.get("signalRefPrice"), o.get("avgFillPrice")
        slip = round(100 * (avg / ref - 1), 2) if ref and avg else None
        if slip is not None:
            slips.append(slip)
        sym = str(o.get("symbol", "")).replace("_US_EQ", "")
        sold = next((s for s in sells
                     if s.get("symbol") == o.get("symbol")
                     and s.get("state") == "FILLED"
                     and str(s.get("createdAtUtc")) > str(o.get("createdAtUtc"))), None)
        result_pct = None
        if sold and sold.get("avgFillPrice") and avg:
            result_pct = round(100 * (sold["avgFillPrice"] / avg - 1), 2)
        fills_detail.append({
            "date": str(o.get("createdAtUtc"))[:10],
            "symbol": sym,
            "signal_price": ref,
            "fill_price": avg,
            "slippage_pct": slip,
            "target": o.get("signalTargetPrice"),
            "stop": o.get("signalStopPrice"),
            "broker_order_id": o.get("brokerOrderId"),
            "status": "closed" if result_pct is not None else "still open",
            "result_pct": result_pct,
        })

    closed = [f for f in fills_detail if f["result_pct"] is not None]
    base = BASELINES.get(strategy_id, {})
    live_mean = round(statistics.fmean(f["result_pct"] for f in closed), 2) if closed else None

    # -- the honesty line: is this enough to judge anything? ---------------
    n_closed = len(closed)
    if n_closed == 0:
        verdict = ("NOT JUDGEABLE YET — no round trip has completed. "
                   "Entries are being captured; results need exits.")
    elif n_closed < 30:
        verdict = (f"NOT JUDGEABLE YET — {n_closed} closed trade(s). A win rate "
                   f"on this many is noise; the backtest used "
                   f"{base.get('backtest_n') or 'thousands of'} trades.")
    else:
        verdict = (f"{n_closed} closed trades — comparable to the backtest's "
                   f"{base.get('backtest_mean_pct')}%/trade claim.")

    return {
        "strategy_id": strategy_id,
        "label": base.get("label", strategy_id),
        "as_of_utc": dt.datetime.now(dt.UTC).isoformat(),
        "verdict": verdict,
        "funnel": {
            "orders_raised": len(buys),
            "reached_broker": len(reached_broker),
            "filled": len(filled),
            "never_placed": len(buys) - len(reached_broker),
            "losses": funnel_losses,
        },
        "slippage": {
            "n": len(slips),
            "mean_pct": round(statistics.fmean(slips), 2) if slips else None,
            # MEDIAN as well as mean, because one uncapped fill dominates the
            # average: SNOW's +20.14% on 3 Sep drags a set whose typical
            # member is around +1%. Reporting only the mean would libel the
            # entry cap; reporting only the median would hide the tail.
            "median_pct": round(statistics.median(slips), 2) if slips else None,
            "worst_pct": max(slips) if slips else None,
            "best_pct": min(slips) if slips else None,
            "note": ("Positive means we paid MORE than the screen said. The "
                     "entry cap is meant to hold this under the configured "
                     "chase band."),
        },
        "results": {
            "closed_trades": n_closed,
            "still_open": len(fills_detail) - n_closed,
            "live_mean_pct": live_mean,
            "backtest_mean_pct": base.get("backtest_mean_pct"),
            "achievable_mean_pct": base.get("achievable_mean_pct"),
            "backtest_win_pct": base.get("backtest_win_pct"),
            "backtest_n": base.get("backtest_n"),
            "harness": base.get("harness"),
            "gates_doc": base.get("gates_doc"),
        },
        "fills": fills_detail,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--strategy", default="mean_reversion_swing_ibkr")
    ap.add_argument("--push", action="store_true")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    from .push_to_api import load_credentials
    base, token = load_credentials()
    base = base.rstrip("/")
    orders = _fetch_orders(base, token)
    card = build(orders, args.strategy)

    if args.json:
        print(json.dumps(card, indent=1))
    else:
        f, s, r = card["funnel"], card["slippage"], card["results"]
        print(f"\n{card['label']} — forward-test scorecard")
        print(f"  {card['verdict']}\n")
        print(f"  orders raised     {f['orders_raised']}")
        print(f"  reached broker    {f['reached_broker']}")
        print(f"  FILLED            {f['filled']}")
        for L in f["losses"]:
            print(f"     -{L['count']:<3} {L['state']:10} {L['reason']}")
        if s["n"]:
            print(f"\n  entry slippage    median {s['median_pct']:+.2f}%  "
                  f"mean {s['mean_pct']:+.2f}%  worst {s['worst_pct']:+.2f}%  "
                  f"(n={s['n']})")
        print(f"\n  closed trades     {r['closed_trades']}   still open {r['still_open']}")
        if r["live_mean_pct"] is not None:
            print(f"  live mean/trade   {r['live_mean_pct']:+.2f}%  vs backtest "
                  f"{r['backtest_mean_pct']}%  (achievable {r['achievable_mean_pct']}%)")
        print()

    if args.push:
        r = requests.post(
            f"{base}/api/ingest/today-setups",
            json={"universe": f"scorecard-{args.strategy}", "label": "latest",
                  "uploaded_by": "forward-scorecard", "artifact": card},
            headers={"Authorization": f"Bearer {token}"}, timeout=45)
        log.info("scorecard push → HTTP %s", r.status_code)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

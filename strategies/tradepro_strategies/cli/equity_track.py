"""tradepro-equity-track — is the live Ichimoku-equity clone tracking the backtest?

Pulls the clone's live realised+open P&L from /api/pnl/by-strategy, computes the
return on deployed capital since the clean-config date, and prints an honest
tracking verdict against the backtest expectation (8.9% CAGR / −13% max DD).

Conservative by design: <20 sessions of clean-config history → TOO_EARLY (no
verdict on noise). Run it through the week as live data accumulates.

    uv run tradepro-equity-track --start 2026-06-26
"""
from __future__ import annotations

import argparse
import datetime as _dt
import logging

from ..quant_engine.equity_tracking import assess_tracking

log = logging.getLogger("tradepro.equity_track")

# Backtest expectation for the clean large_50 config (through-cycle 2022→2026).
_EXPECTED_CAGR_PCT = 8.9
_DRAWDOWN_BOUND_PCT = -13.0


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    p = argparse.ArgumentParser(prog="tradepro-equity-track")
    p.add_argument("--strategy", default="ichimoku_equity_ibkr",
                   help="strategy_id of the trustworthy IBKR clone")
    p.add_argument("--start", required=True,
                   help="clean-config start date YYYY-MM-DD (when large_50-only went live)")
    p.add_argument("--capital", type=float, default=50_000.0,
                   help="deployed capital (from the clone's capital_usd)")
    p.add_argument("--api-base", default=None, help="API base (else from credentials)")
    args = p.parse_args()

    import requests

    from . import push_to_api as _pta
    base = args.api_base
    tok = None
    if not base:
        base, tok = _pta.load_credentials()
    if not base:
        print("ERROR: no api-base (pass --api-base or set credentials)")
        return 2

    try:
        r = requests.get(f"{base.rstrip('/')}/api/pnl/by-strategy", params={"days": 3650},
                         headers={"Authorization": f"Bearer {tok}"} if tok else {}, timeout=20)
        r.raise_for_status()
        rows = r.json().get("rows", [])
    except Exception as e:  # noqa: BLE001
        print(f"ERROR: could not reach API (EC2 may be asleep): {e}")
        return 2

    row = next((x for x in rows if x.get("strategyId") == args.strategy), None)
    if row is None:
        print(f"ERROR: strategy {args.strategy} not found in pnl/by-strategy")
        return 1
    realised = row.get("realisedLtd") or 0.0
    open_pnl = row.get("openPnl") or 0.0
    total = realised + open_pnl
    actual_pct = total / args.capital * 100.0 if args.capital else 0.0

    start = _dt.date.fromisoformat(args.start)
    elapsed = (_dt.datetime.now(_dt.UTC).date() - start).days

    v = assess_tracking(actual_pct, elapsed,
                        expected_cagr_pct=_EXPECTED_CAGR_PCT,
                        drawdown_bound_pct=_DRAWDOWN_BOUND_PCT)

    ccy = row.get("currency", "")
    print(f"\n  {args.strategy} — live-vs-backtest tracking")
    print(f"  realised {realised:+.0f} + open {open_pnl:+.0f} = {total:+.0f} {ccy} "
          f"on {args.capital:,.0f} cap  → {actual_pct:+.1f}%")
    print(f"  STATUS: {v.status}")
    print(f"  {v.note}\n")
    return 0

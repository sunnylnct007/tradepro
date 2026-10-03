"""tradepro-signal-replay — would the published signals have made money?

The product form of the owner's question (3 Oct 2026). Reads the signal
archive, replays each first-appearance signal through the strategy's OWN exit
rules against the golden bar store, and prints closed trades, open marks and
the honesty counters. Never touches the broker.
"""
from __future__ import annotations

import argparse
import json
import os
import sys

from .. import signal_replay as R


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--strategy", default="all",
                   choices=["all", *sorted(R.STRATEGY_MODULES)])
    p.add_argument("--capital", type=float, default=R.DEFAULT_CAPITAL)
    p.add_argument("--pct", type=float, default=R.DEFAULT_PCT)
    p.add_argument("--days", type=int, default=None,
                   help="limit to the most recent N archive days")
    p.add_argument("--json", action="store_true", help="machine output")
    a = p.parse_args()

    base = (os.environ.get("TRADEPRO_API_BASE_URL")
            or os.environ.get("TRADEPRO_API_URL") or "").rstrip("/")
    if not base:
        print("set TRADEPRO_API_BASE_URL", file=sys.stderr)
        return 2
    token = os.environ.get("TRADEPRO_API_TOKEN")

    strategies = sorted(R.STRATEGY_MODULES) if a.strategy == "all" else [a.strategy]
    results = []
    for s in strategies:
        try:
            results.append(R.run(s, base, token, capital=a.capital,
                                 pct=a.pct, days=a.days))
        except Exception as exc:  # noqa: BLE001 — one strategy failing must not hide the other
            results.append({"strategy": s, "error": str(exc)})

    if a.json:
        print(json.dumps(results, indent=2))
        return 0

    for r in results:
        print("=" * 76)
        if r.get("error"):
            print(f"{r['strategy'].upper()}: FAILED — {r['error']}")
            continue
        s = r["summary"]
        print(f"{r['strategy'].upper()} — archive {r['archive_window']} "
              f"({r['archive_days']} day(s)) · sized {s['sizing']['pct_per_position']:.0%} "
              f"of {s['sizing']['capital']:,.0f}")
        if r["closed"]:
            print(f"  CLOSED ({s['closed']}, {s['closed_wins']} win):")
            for t in r["closed"]:
                print(f"    {t['symbol']:7s} {t['signal_bar']} -> {t['exit_date']} "
                      f"{t['reason']:7s} {t['ret_pct']:+7.2f}%  {t['pnl']:+10.2f}")
            print(f"    realised: {s['realised_pnl']:+,.2f}")
        else:
            print("  CLOSED: none — no exit rule has fired yet")
        print(f"  OPEN ({s['open']}): mark-to-market {s['open_pnl']:+,.2f}")
        if r["unmeasured"]:
            print(f"  UNMEASURED ({s['unmeasured']}):")
            for u in r["unmeasured"]:
                print(f"    {u['symbol']:7s} {u['why']}")
        for c in r["caveats"]:
            print(f"  ! {c}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

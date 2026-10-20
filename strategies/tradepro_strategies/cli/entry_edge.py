"""tradepro-entry-edge — does an ENTRY have edge, before any exit?

    tradepro-entry-edge --strategy swing          # from the signal archive
    tradepro-entry-edge --signals my_rule.csv     # any (symbol,date) list

The second form is the point. `tradepro-signal-replay` can only grade
strategies whose exit module is already coded here; this grades ANY list of
entries, including a strategy nobody has built yet.

Owner, 20 Oct 2026: *"we basically need to capture trading signals so we can
evaluate any future strategy easily"*.
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import sys

from ..entry_edge import (
    BENCHMARK, HORIZONS, evaluate, first_appearances, format_table,
)


def _from_csv(path: str) -> list[tuple[str, str]]:
    """symbol,date — header optional, extra columns ignored."""
    out: list[tuple[str, str]] = []
    with open(path, newline="") as fh:
        for row in csv.reader(fh):
            if len(row) < 2:
                continue
            sym, day = row[0].strip().upper(), row[1].strip()[:10]
            if sym.lower() in ("symbol", "ticker"):   # header
                continue
            out.append((sym, day))
    # FIRST APPEARANCE ONLY, same rule the archive path uses — a signal listed
    # on many days would otherwise be weighted by how long the rule liked it.
    seen: set[str] = set()
    uniq = []
    for sym, day in sorted(out, key=lambda x: x[1]):
        if sym not in seen:
            seen.add(sym)
            uniq.append((sym, day))
    return uniq


def main() -> int:
    ap = argparse.ArgumentParser(prog="tradepro-entry-edge")
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--strategy", help="archived board name (swing, momentum, ...)")
    src.add_argument("--signals", help="CSV of symbol,date")
    ap.add_argument("--benchmark", default=BENCHMARK,
                    help=f"control B symbol (default {BENCHMARK}; '' to disable)")
    ap.add_argument("--horizons", default=",".join(str(h) for h in HORIZONS))
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    horizons = tuple(int(x) for x in args.horizons.split(",") if x.strip())

    from ..signal_replay import fetch_boards, golden_loader
    load = golden_loader()

    if args.strategy:
        base = os.environ.get("TRADEPRO_API_BASE_URL")
        if not base:
            print("set TRADEPRO_API_BASE_URL", file=sys.stderr)
            return 2
        boards = fetch_boards(args.strategy, base,
                              os.environ.get("TRADEPRO_API_TOKEN"))
        signals = first_appearances(boards)
        title = f"{args.strategy.upper()} — {len(signals)} first-appearance signals"
    else:
        signals = _from_csv(args.signals)
        title = f"{os.path.basename(args.signals)} — {len(signals)} signals"

    if not signals:
        print("no signals to grade", file=sys.stderr)
        return 1

    res = evaluate(signals, load, horizons=horizons,
                   benchmark=(args.benchmark or None))
    if args.json:
        print(json.dumps(res, indent=2, default=str))
    else:
        print(format_table(res, title))
    return 0

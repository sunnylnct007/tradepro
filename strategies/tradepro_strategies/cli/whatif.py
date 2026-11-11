"""tradepro-whatif — ask the bar store a conditional question, against a null.

    tradepro-whatif own_dd_ge:10 sector_stressed --horizons 20,40
    tradepro-whatif trend_up beta_ge:1.3 market_stressed --universe all
    tradepro-whatif --list

Every answer is archived under ~/.tradepro/research/whatif/ with its date and
question. It emits NO signals; see tradepro_strategies.whatif.
"""
from __future__ import annotations

import argparse
import sys

from ..whatif import Question, archive, describe, format_result, run


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("conditions", nargs="*", help="conditions, ANDed (see --list)")
    ap.add_argument("--horizons", default="5,10,20,40,60")
    ap.add_argument("--universe", default="harvest", help="harvest | all | SYM,SYM,…")
    ap.add_argument("--list", action="store_true", help="print the condition registry and exit")
    ap.add_argument("--no-archive", action="store_true")
    a = ap.parse_args()
    if a.list:
        for row in describe():
            print(f"  {row['condition']:<24} {row['meaning']}")
        return 0
    if not a.conditions:
        ap.error("give at least one condition, or --list")
    q = Question(conditions=a.conditions,
                 horizons=tuple(int(x) for x in a.horizons.split(",")),
                 universe=a.universe)
    r = run(q)
    print(format_result(r))
    if not a.no_archive:
        print(f"\narchived: {archive(r, q)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

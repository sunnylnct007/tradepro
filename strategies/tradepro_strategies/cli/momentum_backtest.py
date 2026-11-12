"""Backtest the monthly momentum rotation (MOMENTUM_ROTATION_GATES_V1.md).

    uv run tradepro-momentum-backtest                 # the decided design
    uv run tradepro-momentum-backtest --robustness    # + every pre-registered variant
    uv run tradepro-momentum-backtest --set top_n=12 --set trailing_stop=null
"""
from __future__ import annotations

import argparse
import json
import logging
import sys

from .. import momentum_rotation as M


def _parse_set(items: list[str]) -> dict:
    out = {}
    for it in items or []:
        k, _, v = it.partition("=")
        out[k.strip()] = json.loads(v) if v.strip() not in ("",) else None
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--set", action="append", help="override, e.g. top_n=12 (JSON values)")
    ap.add_argument("--robustness", action="store_true")
    ap.add_argument("--out", help="write the full JSON report here")
    a = ap.parse_args(argv)
    logging.basicConfig(level=logging.WARNING)
    report = {"main": M.run(_parse_set(a.set))}
    if a.robustness:
        report["robustness"] = M.compare([{}] + M.ROBUSTNESS)
        report["robustness"]["not_yet_run"] = M.NOT_YET_RUN
    text = json.dumps(report, indent=1, default=str)
    if a.out:
        open(a.out, "w").write(text)
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())

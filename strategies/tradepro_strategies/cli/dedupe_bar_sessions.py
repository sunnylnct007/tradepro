"""Repair partitions that hold the SAME SESSION twice.

WHY THIS EXISTS (25 Aug 2026). The bar-cache delta merge de-duplicated on the
exact timestamp:

    fresh = df[~df.index.isin(existing.index)]

Providers do not agree on what instant stamps a daily bar — ibkr_web uses the
US cash open (13:30 UTC), yfinance uses 04:00 UTC. So the same session arrives
with two different instants, `isin` correctly calls it new, and the partition
ends up holding one trading day twice with two different closes.

It stayed rare while IBKR answered. The night of 24 Aug the scheduled daily
harvest died (a `uv run` that had drifted above its `cd`, so launchd's cwd
made it ModuleNotFoundError), every strategy fell through to yfinance, and
**103 symbols acquired a duplicate 2026-08-24 bar in one night**.

The damage is not cosmetic: a 20-day window over an affected date holds 21
bars, one a phantom with a different close. It moved TXN from 2.53σ to under
the 2.5σ trigger, so the Swing screen published a candidate at 00:15 and
withdrew it at 02:17 having learned nothing new.

The write path is fixed in `bar_cache/store.py::_dedupe_sessions`, so this
cannot recur. This repairs what is already on disk.

Policy — identical to the write path, deliberately, so a repair and a merge
can never disagree:
  * one row per UTC calendar date,
  * prefer a golden (IBKR) source over a fallback,
  * on a tie prefer the row already earlier in the file.

Dry-run by default. `--apply` writes, atomically (tmp + rename), and only for
partitions that actually change.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

from tradepro_strategies.bar_cache.store import _dedupe_sessions


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--root", default=str(Path.home() / ".tradepro" / "bar_cache"))
    ap.add_argument("--resolution", default="1d",
                    help="only daily-and-coarser partitions can have this defect")
    ap.add_argument("--apply", action="store_true", help="write the repair (default: dry run)")
    a = ap.parse_args(argv)

    root = Path(a.root)
    files = sorted(root.glob(f"*/*/{a.resolution}/*.parquet"))
    if not files:
        print(f"no {a.resolution} partitions under {root}", file=sys.stderr)
        return 1

    scanned = repaired = rows_dropped = 0
    affected: list[tuple[str, str, int]] = []
    for f in files:
        scanned += 1
        try:
            df = pd.read_parquet(f)
        except Exception as exc:  # noqa: BLE001
            print(f"  SKIP {f}: unreadable ({exc})")
            continue
        if df.empty:
            continue
        kept, dropped = _dedupe_sessions(df, a.resolution, label=str(f))
        if not dropped:
            continue
        sym = f.parts[-3] if len(f.parts) >= 3 else f.stem
        affected.append((sym, f.parts[-1], dropped))
        repaired += 1
        rows_dropped += dropped
        if a.apply:
            tmp = f.with_suffix(f.suffix + ".tmp")
            kept.to_parquet(tmp)
            tmp.replace(f)

    verb = "REPAIRED" if a.apply else "would repair"
    print(f"\nscanned {scanned} {a.resolution} partitions")
    print(f"{verb} {repaired} partition(s), dropping {rows_dropped} duplicate row(s)")
    for sym, part, n in affected[:200]:
        print(f"  {sym:<8} {part}  -{n}")
    if len(affected) > 200:
        print(f"  … and {len(affected) - 200} more")
    if not a.apply and repaired:
        print("\nDRY RUN — re-run with --apply to write.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

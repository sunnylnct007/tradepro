"""A seam bar must be a source MINORITY among its neighbours.

Written 25 Aug 2026, checking the data lane's `ISOLATED_SEAM_BARS.json` before
using it.

THE MANIFEST'S CLAIM. yfinance rows are adjusted and ibkr rows are raw, so a
single yfinance bar landing inside a run of ibkr bars shows the whole
cumulative dividend adjustment as a one-day spike. HYG 2021-08-31 stored at
66.65 against raw neighbours of 87.98 is exactly that: 87.98 x 0.7575, a
plausible four-year dividend factor for a bond ETF.

That mechanism is real and the detector is well built — it uses MAD over a
local window rather than a naive standard deviation (which is inflated by the
very bars being detected: HYG naive sigma 1.889% against a robust 0.299%), and
it has a reversal test to separate seams from genuine crashes.

WHAT IT MISSES. **A seam requires a boundary.** The detector measures the SIZE
and SHAPE of a move, and never checks whether the bar's source actually differs
from its neighbours'. If a bar and everything around it come from the same
provider, there is no convention change to produce a spike, and a large move is
simply a large move.

Applied to the 71-row manifest: **28 rows have all-same-source neighbours**,
and they fall on exactly three dates —

    2020-03-12   x12
    2020-03-13   x12
    2020-03-16    x4

— the three most violent days of the COVID crash, across SPY, VOO, IVV, DIA,
VTI, XLF, XLP, XLU, SCHD, QUAL, USMV, WFC, MS, PEP, T, D, DUK, USB. SPY's
week reads -9.57%, +8.55%, -10.94%, +5.40%, -5.06% and every bar in it is
yfinance. That is March 2020, not a units seam.

WHY IT MATTERS MORE THAN THE COUNT SUGGESTS. "Correcting" those bars would
delete the COVID crash from the store for 22 symbols — and March 2020 is one of
only two regime stresses in the whole record (2022 being the other, and the
only losing year Swing has). Removing them would make every strategy graded on
this store look BETTER than it is, which is the dangerous direction.

The manifest's own reversal test should have caught them: a genuine crash that
bounces reverses by LESS than it fell, and SPY's -9.57% is followed by +8.55%.
The test exists and did not fire. The source-boundary check is cheaper and
catches them outright, so the two belong together rather than either alone.

Exits non-zero if any row lacks a boundary.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

from tradepro_strategies.cli.build_universe import _load

NEIGHBOURHOOD = 3          # bars either side


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--manifest", default="ISOLATED_SEAM_BARS.json")
    a = ap.parse_args(argv)

    raw = json.loads(Path(a.manifest).read_text())
    rows = raw if isinstance(raw, list) else (
        raw.get("rows") or raw.get("bars") or next(iter(raw.values())))

    genuine, suspect, unchecked = [], [], []
    for r in rows:
        d = _load(r["symbol"])
        if d is None or "source" not in d.columns:
            unchecked.append(r); continue
        dts = [str(x)[:10] for x in d.index]
        if r["date"] not in dts:
            unchecked.append(r); continue
        i = dts.index(r["date"])
        src = d["source"].astype(str).tolist()
        nb = [src[k] for k in range(max(0, i - NEIGHBOURHOOD),
                                    min(len(src), i + NEIGHBOURHOOD + 1)) if k != i]
        same = sum(1 for x in nb if x == src[i])
        (suspect if same else genuine).append((r, same, len(nb)))

    print(f"manifest {a.manifest}: {len(rows)} rows")
    print(f"  genuine seam (source isolated among neighbours): {len(genuine)}")
    print(f"  NO SOURCE BOUNDARY — not a seam:                 {len(suspect)}")
    if unchecked:
        print(f"  unchecked (symbol or date not in the store):     {len(unchecked)}")
    if suspect:
        print("\nrows with no boundary, by date:")
        for dt, n in Counter(r["date"] for r, _, _ in suspect).most_common():
            print(f"   {dt}   x{n}")
        print(f"\n{'sym':<7}{'date':<12}{'source':<10}{'move%':>9}{'factor':>9}  neighbours")
        for r, same, tot in suspect[:40]:
            print(f"{r['symbol']:<7}{r['date']:<12}{r.get('source','?'):<10}"
                  f"{r.get('move_pct', 0):>8.2f}%{r.get('implied_factor', 0):>9.4f}"
                  f"  {same}/{tot} share its source")
        print("\nA seam needs a BOUNDARY. Where every neighbour is the same source "
              "there is no\nconvention change to produce a spike, and a large move "
              "is just a large move.")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

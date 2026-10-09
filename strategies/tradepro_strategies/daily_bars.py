"""ONE way to read a daily bar series from the golden store.

Owner, 9 Oct 2026: *"improvement rather than more feature addition"*, after a
week in which the same fix had to be applied twice in a day, twice.

WHY THIS FILE EXISTS. Four modules each carried their own `_load`, all
globbing the same directory, all nearly identical and quietly different:

    cli/build_universe.py       glob + dedupe
    cli/name_context.py         glob + dedupe            (identical, renamed BASE)
    cli/momentum_candidates.py  glob + dedupe + >=220 bars + 'open' column
    cli/swing_candidates.py     glob + dedupe + >=220 bars + 'open' column

Four copies is four places a fix has to land. On 9 Oct `poison_check` was
corrected, shipped, and the fictional +$50,742 did not move — because the
replay read bars by a path that never called it. That is the whole cost of
duplication, in one afternoon.

WHAT THIS DELIBERATELY DOES NOT CHANGE. It globs the local parquet exactly as
the four copies did. `cli/put_check.py` reads through BarStore instead, and
its docstring is right that globbing "silently under-reads everywhere the
local cache is incomplete" — but switching every screen to BarStore changes
what the live boards see, and that is a behaviour change dressed as a
cleanup. Recorded here as the next step, not smuggled into this one.

poison_check is OPT-IN rather than automatic, because the universe builder
must see poisoned series in order to exclude them; a loader that hid them
would break the thing that detects them.
"""
from __future__ import annotations

import glob as _glob
import os

BASE_DIR = os.path.expanduser("~/.tradepro/bar_cache/us_etf")

#: Bars a 200-SMA rule needs before it can say anything, plus headroom. The
#: screens have always applied this; it lives here so they cannot drift apart.
MIN_BARS = 220


def load_daily(sym: str, *, min_bars: int | None = None,
               require_ohlc: bool = False, check_poison: bool = False):
    """Daily bars for one symbol, or None.

    min_bars      — reject a series shorter than this (screens pass MIN_BARS).
    require_ohlc  — reject a series with no 'open' column.
    check_poison  — reject a series poison_check refuses. OFF by default: the
                    universe builder needs to SEE poisoned series to exclude
                    them, so a loader that silently hid them would disable the
                    detector. Consumers that only consume (screens, replay)
                    should pass True.
    """
    fs = sorted(_glob.glob(f"{BASE_DIR}/{sym}/1d/*.parquet"))
    if not fs:
        return None
    import pandas as pd
    try:
        df = pd.concat([pd.read_parquet(f) for f in fs]).sort_index()
    except Exception:  # noqa: BLE001 — an unreadable series is "no series"
        return None
    df = df[~df.index.duplicated(keep="last")]
    if df is None or len(df) == 0:
        return None
    if min_bars is not None and len(df) < min_bars:
        return None
    if require_ohlc and "open" not in df.columns:
        return None
    if check_poison:
        from .universe import poison_check
        closes = [float(x) for x in df["close"].tolist()]
        vols = ([float(x or 0) for x in df["volume"].tolist()]
                if "volume" in df.columns else None)
        if not poison_check(closes, vols)[0]:
            return None
    return df

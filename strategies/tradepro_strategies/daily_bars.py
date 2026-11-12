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

THROUGH THE STORE, 12 Nov 2026 (DATA_ARCHITECTURE step 5). This used to glob
the local parquet, as did six private copies elsewhere that never came here.
Globbing reads only what THIS machine holds. On Lambda that is nothing: the
today-setups job scanned 0 of 113 names on 11 Nov and published every one as
"missing", with nothing failing. `BarStore.read` serves the local copy and
falls through to the shared S3 copy, and never calls a provider, so this stays
a pure read. On the harvesting Mac the two give byte-identical frames (checked
on every symbol before the switch).

poison_check is OPT-IN rather than automatic, because the universe builder
must see poisoned series in order to exclude them; a loader that hid them
would break the thing that detects them.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

#: Bars a 200-SMA rule needs before it can say anything, plus headroom. The
#: screens have always applied this; it lives here so they cannot drift apart.
MIN_BARS = 220

#: Enough history for a 200-SMA plus a two-year signal state, for jobs that
#: run where each month is an S3 read (Lambda). Full history stays the default.
RECENT_LOOKBACK_DAYS = 800

#: The one tree equities and ETFs live in. The resolver maps us_equity here.
DEFAULT_ASSET_CLASS = "us_etf"


def _store():
    from .ibkr_bars import bar_store
    return bar_store()


def load_bars(sym: str, resolution: str = "1d", *,
              lookback_days: int | None = None,
              asset_class: str = DEFAULT_ASSET_CLASS,
              fetched_by: str = "daily_bars"):
    """Stored bars for one symbol at one resolution, or None.

    lookback_days — calendar days back from today. None reads every month the
                    store holds. A Lambda job should pass a lookback: each
                    month not on local disk is one S3 read.
    Never fetches from a provider. An unreadable series answers None, the same
    as an absent one — callers already report both as "no bars".
    """
    since = None
    if lookback_days is not None:
        # From MIDNIGHT, so a lookback never cuts its first session in half.
        since = (datetime.now(timezone.utc) - timedelta(days=lookback_days)).replace(
            hour=0, minute=0, second=0, microsecond=0)
    try:
        frame = _store().read(sym, asset_class, resolution, since=since,
                              fetched_by=fetched_by)
    except Exception:  # noqa: BLE001 — an unreadable series is "no series"
        return None
    df = frame.df
    if df is None or len(df) == 0:
        return None
    return df[~df.index.duplicated(keep="last")].sort_index()


def load_daily(sym: str, *, min_bars: int | None = None,
               require_ohlc: bool = False, check_poison: bool = False,
               lookback_days: int | None = None,
               fetched_by: str = "daily_bars"):
    """Daily bars for one symbol, or None.

    min_bars      — reject a series shorter than this (screens pass MIN_BARS).
    require_ohlc  — reject a series with no 'open' column.
    check_poison  — reject a series poison_check refuses. OFF by default: the
                    universe builder needs to SEE poisoned series to exclude
                    them, so a loader that silently hid them would disable the
                    detector. Consumers that only consume (screens, replay)
                    should pass True.
    lookback_days — see load_bars.
    """
    df = load_bars(sym, "1d", lookback_days=lookback_days, fetched_by=fetched_by)
    if df is None:
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


def latest_intraday_session(sym: str, *, lookback_days: int = 10):
    """The newest session's 5-minute bars for one symbol, or None.

    DISPLAY ONLY — screens quote today's price beside a signal computed on the
    settled daily bar. Two screens carried identical copies of this read.
    Those copies read EVERY month of 5m history to find the last day, and
    would quote a price weeks old for a name the 5m lane no longer harvests.
    A session older than `lookback_days` is not "latest": this returns None and
    the screen shows no intraday price rather than a stale one.
    """
    df = load_bars(sym, "5m", lookback_days=lookback_days,
                   fetched_by="latest_intraday")
    if df is None:
        return None
    day = str(df.index[-1])[:10]
    rows = df[[str(x)[:10] == day for x in df.index]]
    return None if rows.empty else rows


def local_symbols(asset_class: str = DEFAULT_ASSET_CLASS) -> list[str]:
    """Symbols the harvesting machine's store holds — the universe builder's
    candidate list."""
    return _store().local_symbols(asset_class)

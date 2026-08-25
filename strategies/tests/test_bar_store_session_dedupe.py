"""One SESSION, one row.

Regression cover for the 25 Aug 2026 corruption: the delta merge de-duplicated
on the exact TIMESTAMP, and providers disagree about what instant stamps a
daily bar (ibkr_web 13:30 UTC, yfinance 04:00 UTC). The same session therefore
sailed through as "new" and 142 symbols acquired a duplicate 2026-08-24 bar in
one night, each with a different close.

The cost was a FALSE NEGATIVE, which is the expensive direction: a 20-day
window over an affected date held 21 bars, and the phantom moved TXN from
2.53σ to under the 2.5σ trigger. The Swing screen published TXN at 00:15 and
withdrew it at 02:17 having been given no new information.
"""
from __future__ import annotations

import pandas as pd

from tradepro_strategies.bar_cache.store import _dedupe_sessions


def _frame(rows):
    idx = pd.DatetimeIndex([r[0] for r in rows])
    return pd.DataFrame(
        {"close": [r[1] for r in rows], "source": [r[2] for r in rows]}, index=idx)


def test_same_session_two_providers_collapses_to_the_golden_row():
    """The exact TXN case, with the real closes."""
    df = _frame([
        ("2026-08-21T13:30Z", 264.36, "ibkr_web"),
        ("2026-08-24T04:00Z", 258.94, "yfinance"),   # Yahoo's stamp
        ("2026-08-24T13:30Z", 256.59, "ibkr_web"),   # the US cash open
    ])
    kept, dropped = _dedupe_sessions(df, "1d")
    assert dropped == 1
    assert len(kept) == 2
    assert list(kept["source"]) == ["ibkr_web", "ibkr_web"]
    # The IBKR close survives — it is the golden source, per the standing rule.
    assert kept["close"].iloc[-1] == 256.59


def test_order_of_arrival_does_not_decide_the_winner():
    """Golden wins whether it was cached first or arrived second. The merge
    concatenates existing-then-fresh, so a naive keep='first' would hand the
    session to whichever provider happened to get there first."""
    a = _frame([("2026-08-24T04:00Z", 258.94, "yfinance"),
                ("2026-08-24T13:30Z", 256.59, "ibkr_web")])
    b = _frame([("2026-08-24T13:30Z", 256.59, "ibkr_web"),
                ("2026-08-24T04:00Z", 258.94, "yfinance")])
    for df in (a, b):
        kept, dropped = _dedupe_sessions(df, "1d")
        assert dropped == 1
        assert kept["source"].iloc[0] == "ibkr_web"
        assert kept["close"].iloc[0] == 256.59


def test_intraday_bars_an_hour_apart_are_two_real_bars():
    """The defect is specific to daily-and-coarser. Collapsing intraday bars
    to one per day would destroy the 5m lane."""
    df = _frame([("2026-08-24T13:30Z", 256.59, "ibkr_web"),
                 ("2026-08-24T14:30Z", 257.10, "ibkr_web"),
                 ("2026-08-24T15:30Z", 255.80, "ibkr_web")])
    kept, dropped = _dedupe_sessions(df, "5m")
    assert dropped == 0
    assert len(kept) == 3


def test_clean_daily_frame_is_returned_untouched():
    df = _frame([("2026-08-20T13:30Z", 265.60, "ibkr_web"),
                 ("2026-08-21T13:30Z", 264.36, "ibkr_web"),
                 ("2026-08-24T13:30Z", 256.59, "ibkr_web")])
    kept, dropped = _dedupe_sessions(df, "1d")
    assert dropped == 0
    assert kept.equals(df)


def test_two_fallback_rows_for_one_session_keeps_the_cached_one():
    """No golden row available: prefer-existing still applies, so a partition
    cannot flip its close every time a fallback is re-queried."""
    df = _frame([("2026-08-24T04:00Z", 258.94, "yfinance"),
                 ("2026-08-24T20:00Z", 259.90, "ig")])
    kept, dropped = _dedupe_sessions(df, "1d")
    assert dropped == 1
    assert kept["close"].iloc[0] == 258.94


def test_a_duplicated_session_does_not_inflate_the_window():
    """The actual harm, stated as a test: a 20-session window must contain 20
    sessions. With the phantom present it held 21 and the mean moved."""
    rows = [(f"2026-07-{d:02d}T13:30Z", 100.0, "ibkr_web") for d in range(1, 21)]
    rows.append(("2026-07-20T04:00Z", 130.0, "yfinance"))   # phantom duplicate
    df = _frame(rows)
    assert len(df) == 21 and df["close"].mean() > 100.0
    kept, _ = _dedupe_sessions(df, "1d")
    assert len(kept) == 20
    assert kept["close"].mean() == 100.0

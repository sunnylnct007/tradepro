"""A settled daily bar must replace the partial one written earlier that day.

Same session, same provider is a tie on provenance, and prefer-existing then
keeps whichever row arrived FIRST. During a live session that is the PARTIAL
bar: any 1d fetch before 20:00 UTC captures the day so far, and the settled bar
fetched after the close could never replace it.

Measured 2026-08-26 against the API, the 25 Aug session, 10 of 10 symbols wrong:

    NVDA  stored 211.05 vs true 213.05  (0.94%)   volume 55% of true
    MSFT  stored 488.70 vs true 491.71  (0.61%)   volume 45%
    SPY   stored 765.09 vs true 765.91  (0.11%)   volume 39%

That is not a rounding difference. 0.95% is the whole gap between a 2.53-sigma
Swing signal and a 2.32-sigma one, so the screen was computing entries on a
price the market never closed at.

The discriminator needs no fetch-time metadata: for one session, the more
complete bar has MORE VOLUME — a partial day cannot have traded more than the
full day it belongs to.
"""
from __future__ import annotations

import pandas as pd
import pytest

from tradepro_strategies.bar_cache.store import _dedupe_sessions


def _frame(rows):
    idx = pd.to_datetime([r[0] for r in rows], utc=True)
    return pd.DataFrame(
        {"close": [r[1] for r in rows],
         "volume": [r[2] for r in rows],
         "source": [r[3] for r in rows]},
        index=idx)


def test_settled_bar_replaces_the_partial_written_earlier_that_day():
    """THE regression: partial cached first, settled arrives after the close."""
    df = _frame([
        ("2026-08-25T13:30:00Z", 765.09, 18_586_252, "ibkr_web"),   # partial, cached first
        ("2026-08-25T13:30:00Z", 765.91, 47_756_947, "ibkr_web"),   # settled, after close
    ])
    kept, dropped = _dedupe_sessions(df, "1d", label="SPY")
    assert dropped == 1
    assert float(kept["close"].iloc[0]) == pytest.approx(765.91)
    assert int(kept["volume"].iloc[0]) == 47_756_947


def test_a_golden_source_still_beats_a_fallback_with_more_volume():
    """Provenance outranks volume — a yfinance bar does not win on size."""
    df = _frame([
        ("2026-08-25T13:30:00Z", 700.00, 99_000_000, "yfinance"),
        ("2026-08-25T13:30:00Z", 765.91, 47_756_947, "ibkr_web"),
    ])
    kept, _ = _dedupe_sessions(df, "1d", label="SPY")
    assert kept["source"].iloc[0] == "ibkr_web"
    assert float(kept["close"].iloc[0]) == pytest.approx(765.91)


def test_equal_volume_falls_back_to_prefer_existing():
    """A genuine re-fetch of identical data must change nothing."""
    df = _frame([
        ("2026-08-25T13:30:00Z", 765.91, 47_756_947, "ibkr_web"),
        ("2026-08-25T13:30:00Z", 765.91, 47_756_947, "ibkr_web"),
    ])
    kept, dropped = _dedupe_sessions(df, "1d", label="SPY")
    assert dropped == 1 and len(kept) == 1


def test_single_row_sessions_are_untouched():
    df = _frame([
        ("2026-08-24T13:30:00Z", 763.47, 50_972_937, "ibkr_web"),
        ("2026-08-25T13:30:00Z", 765.91, 47_756_947, "ibkr_web"),
    ])
    kept, dropped = _dedupe_sessions(df, "1d", label="SPY")
    assert dropped == 0 and len(kept) == 2

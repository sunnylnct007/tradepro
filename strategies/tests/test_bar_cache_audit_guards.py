"""Three guards on the audit's OUTPUT, each catching a different bug.

They are deliberately separate. The 24 Sep incident was a reader sweeping four
appended runs as one and reporting 6,805 defects against a stated 2,645 — and
the total-vs-breakdown assertion would have passed all four times, because
every RUN was internally consistent. The scope was wrong, not the sum. Shipping
both and implying they do the same job would be the same overstatement.
"""
from __future__ import annotations

import pandas as pd

from tradepro_strategies.cli.bar_cache_audit import find_garbage, reports_volume


def _flat_month(symbol_has_volume: bool = True, n: int = 21) -> pd.DataFrame:
    """A month of identical closes on zero volume — the dead-partition shape."""
    idx = pd.date_range("2026-01-02", periods=n, freq="B", tz="UTC")
    return pd.DataFrame(
        {"open": [100.0] * n, "high": [100.0] * n, "low": [100.0] * n,
         "close": [100.0] * n, "volume": [0] * n}, index=idx)


def test_indices_and_futures_do_not_report_volume():
    for sym in ("^VIX", "^TNX", "^GSPC", "PL=F", "PA=F", "CL=F"):
        assert not reports_volume(sym), f"{sym} has no volume by construction"
    for sym in ("OKE", "XOM", "SWDA.L", "BRK.B"):
        assert reports_volume(sym), f"{sym} is a traded listing"


def test_the_volume_test_does_not_fire_at_an_index():
    """26 of 27 findings dated 2026 were this, and ^TNX alone was 33 of the
    100 since 2024. The one genuine finding was sitting inside that noise."""
    df = _flat_month()
    assert find_garbage(df, symbol="^TNX") == [], (
        "an index has no volume; a zero-volume month is not evidence of "
        "anything and must not be reported")
    assert find_garbage(df, symbol="PA=F") == [], "same for continuous futures"


def test_the_volume_test_still_fires_at_a_traded_listing():
    """The guard must not become a hole. A real listing with a dead month is
    still the wrong-contract signature that caught MTUM/QUAL/USMV/VLUE."""
    assert find_garbage(_flat_month(), symbol="OKE"), (
        "a traded US listing never has a median-zero-volume month")


def test_no_symbol_supplied_keeps_the_old_behaviour():
    """Callers that predate the symbol argument must be unaffected."""
    assert find_garbage(_flat_month())


def test_a_real_spike_is_still_caught_on_a_traded_name():
    """OKE's actual row: open=high=low=close=1635.00 on zero volume, between
    97.51 and 95.82. Four identical prices and no volume is a FABRICATED row,
    not a bad tick — a tick moves one field."""
    n = 21
    idx = pd.date_range("2026-09-01", periods=n, freq="B", tz="UTC")
    close = [96.0] * n
    close[5] = 1635.0
    df = pd.DataFrame({"open": close, "high": close, "low": close,
                       "close": close, "volume": [1_000_000] * n}, index=idx)
    df.iloc[5, df.columns.get_loc("volume")] = 0
    assert find_garbage(df, symbol="OKE"), "a 16.8x fabricated row must be caught"


def test_the_volume_tests_never_fire_on_intraday_bars():
    """A FLAT ZERO-VOLUME 5m BAR IS NORMAL — it is an untraded interval.

    Swept across the store for Jun-Sep 2026: 17,412 such bars at 5m and 3,146
    at 1m, against 93 at 1d. Any o=h=l=c-on-zero-volume test applied without a
    resolution condition would fire 20,558 times on ordinary intraday data and
    rebuild the exact noise floor the volume guard just removed.

    The store holds 2,632 5m and 438 1m partitions today and the audit reports
    ZERO findings on them — every one of the 2,021 is 1d. That holds because
    of `daily_spaced` (median index spacing >= 20h), which is easy to weaken by
    accident. This pins the property rather than the implementation.
    """
    n = 78
    idx = pd.date_range("2026-09-09 13:30", periods=n, freq="5min", tz="UTC")
    df = pd.DataFrame(
        {"open": [100.0] * n, "high": [100.0] * n, "low": [100.0] * n,
         "close": [100.0] * n, "volume": [0] * n}, index=idx)
    assert find_garbage(df, symbol="OKE") == [], (
        "a flat zero-volume 5m bar is an untraded interval, not a defect")

    idx1 = pd.date_range("2026-09-09 13:30", periods=n, freq="1min", tz="UTC")
    assert find_garbage(df.set_index(idx1), symbol="OKE") == [], "same at 1m"

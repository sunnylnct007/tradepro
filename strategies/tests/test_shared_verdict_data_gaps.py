"""Fail-visible data-gap integrity — NO FALSE POSITIVES.

A could-not-compute on a key check must surface + cap confidence, never read as
"all clear". Pins feedback_no_false_positives at the unit level (box not needed).
"""
from tradepro_strategies.shared_verdict import (
    apply_data_gap_integrity,
    collect_data_gaps,
)


def test_collect_only_flags_observed_failures():
    # Absent keys are NOT gaps (caller didn't check them); present+False are.
    gaps = collect_data_gaps(available={"earnings": False, "valuation": True})
    assert gaps == ["earnings"]
    assert collect_data_gaps(available={}) == []


def test_no_gaps_passes_through_unchanged():
    b, c, r, capped = apply_data_gap_integrity(
        bucket="BUY", conviction="HIGH", reason="trend ok", gaps=[])
    assert (b, c, capped) == ("BUY", "HIGH", False)
    assert r == "trend ok"


def test_blocking_gap_caps_buy_to_wait_low():
    # Earnings calendar couldn't load → a BUY is UNVERIFIED → WAIT + LOW.
    b, c, r, capped = apply_data_gap_integrity(
        bucket="BUY", conviction="HIGH", reason="trend ok", gaps=["earnings"])
    assert (b, c, capped) == ("WAIT", "LOW", True)
    assert "earnings calendar unavailable" in r  # gap is SHOWN, not hidden


def test_valuation_and_bars_are_blocking_too():
    for g in ("valuation", "bars"):
        b, c, _, _ = apply_data_gap_integrity(
            bucket="BUY", conviction="MEDIUM", reason="", gaps=[g])
        assert (b, c) == ("WAIT", "LOW"), g


def test_nonblocking_gap_keeps_bucket_but_caps_high():
    # Volume couldn't compute → BUY stays, but can't be HIGH conviction.
    b, c, r, capped = apply_data_gap_integrity(
        bucket="BUY", conviction="HIGH", reason="trend ok", gaps=["volume"])
    assert (b, c, capped) == ("BUY", "MEDIUM", True)
    assert "volume confirmation unavailable" in r


def test_blocking_gap_on_nonbuy_does_not_invent_a_buy():
    # A WAIT/AVOID never gets upgraded; HIGH conviction still capped.
    b, c, _, capped = apply_data_gap_integrity(
        bucket="AVOID", conviction="HIGH", reason="downtrend", gaps=["valuation"])
    assert (b, c, capped) == ("AVOID", "MEDIUM", True)

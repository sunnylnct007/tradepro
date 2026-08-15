"""Option-quote invariants (14 Aug 2026). These are IDENTITIES, not opinions:
a violation means the quote cannot be true, so the caller must block rather
than rank. Anchored on the two real failures that motivated the module."""
from __future__ import annotations

import math

from tradepro_strategies.quant_engine.options.quote_sanity import (
    check_bid_ask, check_freshness, check_intrinsic_floor, check_iv_band,
    check_put_call_parity, sanity_report)


def test_the_mu_case_put_priced_above_call_breaks_parity():
    """The worked failure: a 960 put marked ABOVE the 960 call at spot 962,
    caused by a stock quote 2.4h newer than the option marks."""
    v = check_put_call_parity(call_mid=20.0, put_mid=33.0, spot=962.0,
                              strike=960.0, dte=30)
    assert v, "parity breach must be detected"
    assert v[0].check == "put_call_parity"
    assert "different moments" in v[0].detail
    assert v[0].severity == "block"


def test_parity_holds_for_a_consistent_quote():
    spot, strike, dte, r = 962.0, 960.0, 30, 0.04
    t = dte / 365.0
    # construct a call/put pair that satisfies parity exactly
    put_mid = 25.0
    call_mid = put_mid + spot - strike * math.exp(-r * t)
    assert check_put_call_parity(call_mid, put_mid, spot, strike, dte=dte) == []


def test_option_cannot_trade_below_intrinsic():
    # ITM put: strike 60, spot 50 -> intrinsic 10; a 7.00 mid is impossible
    v = check_intrinsic_floor("put", strike=60.0, spot=50.0, mid=7.0)
    assert v and v[0].check == "intrinsic_floor"
    # a mid above intrinsic is fine
    assert check_intrinsic_floor("put", strike=60.0, spot=50.0, mid=11.0) == []
    # calls use the mirrored identity
    assert check_intrinsic_floor("call", strike=50.0, spot=60.0, mid=7.0)
    assert check_intrinsic_floor("call", strike=50.0, spot=60.0, mid=11.0) == []


def test_crossed_book_is_a_violation():
    assert check_bid_ask(1.40, 1.30)[0].check == "book_not_crossed"
    assert check_bid_ask(1.30, 1.40) == []


def test_zero_or_absurd_iv_blocks():
    assert check_iv_band(0.0)[0].check == "iv_positive"
    assert check_iv_band(9.0)[0].check == "iv_band"
    assert check_iv_band(0.42) == []
    assert check_iv_band(None) == []          # absent is not a violation


def test_freshness_skew_catches_mixed_sampling():
    # spot 1 minute old, option marks 2.4 hours old — the MU signature
    v = check_freshness({"spot": 60.0, "option_mid": 8640.0})
    assert any(x.check == "freshness_skew" for x in v)
    # everything sampled together is fine
    assert [x for x in check_freshness({"spot": 60.0, "option_mid": 90.0})
            if x.severity == "block"] == []


def test_report_blocks_only_on_impossible_not_on_merely_old():
    stale_only = sanity_report(kind="put", strike=54.0, spot=57.5, bid=1.30,
                               ask=1.34, mid=1.32, iv=0.45, dte=39,
                               field_ages_sec={"spot": 7200.0, "mid": 7300.0})
    assert stale_only.ok, "old-but-consistent data warns, never blocks"
    assert any(v.check == "staleness" for v in stale_only.violations)

    impossible = sanity_report(kind="put", strike=960.0, spot=962.0,
                               bid=32.0, ask=34.0, mid=33.0, iv=0.4, dte=30,
                               paired_mid=20.0)
    assert not impossible.ok


def test_clean_quote_produces_no_violations():
    r = sanity_report(kind="put", strike=54.0, spot=57.5, bid=1.30, ask=1.34,
                      mid=1.32, iv=0.459, dte=39,
                      field_ages_sec={"spot": 30.0, "mid": 45.0})
    assert r.ok and r.violations == []

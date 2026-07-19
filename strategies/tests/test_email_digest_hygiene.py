"""Digest hygiene: never advertise a BUY count we can't stand behind, and never
surface a non-tradeable symbol (index / future / foreign listing) as a
candidate. Guards the "11 BUY → none passed verification" garbage email.
"""
from tradepro_strategies.email_digest import _is_tradeable, _publishable


def test_non_tradeable_symbols_dropped():
    for sym in ["^GDAXI", "^GSPC", "CL=F", "ES=F", "ROG.SW", "CBA.AX", "BP.L", "SAP.DE"]:
        assert _is_tradeable(sym) is False, sym


def test_tradeable_us_names_kept():
    for sym in ["META", "AAPL", "MA", "AXP", "XLV", "BBWI", "SPY"]:
        assert _is_tradeable(sym) is True, sym


def test_none_and_blank_not_tradeable():
    assert _is_tradeable(None) is False
    assert _is_tradeable("") is False


def test_publishable_suppresses_failed_computation():
    ok, why = _publishable({"symbol": "META", "rationale": "unverified — couldn't compute stats"})
    assert ok is False
    assert why == "rationale/computation failed"


def test_publishable_keeps_clean_row():
    ok, _ = _publishable({"symbol": "BBWI", "rationale": "above cloud, TK bullish", "max_drawdown_pct": -13})
    assert ok is True


def test_publishable_rejects_impossible_recovery():
    # A -90% DD needs +900% to recover — corrupt-bar artifact, not a real buy.
    ok, why = _publishable({"symbol": "USMV", "max_drawdown_pct": -90})
    assert ok is False
    assert "implausible" in why


def test_verified_count_is_zero_when_all_unverified():
    # The count the digest advertises must be the publishable count.
    buys = [
        {"symbol": "META", "rationale": "unverified"},
        {"symbol": "MA", "rationale": "couldn't compute"},
        {"symbol": "AXP", "rationale": "rationale failed"},
    ]
    n_buy = sum(1 for b in buys if _publishable(b)[0])
    assert n_buy == 0

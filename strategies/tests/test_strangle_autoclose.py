"""The exit half of the index strangle — what it closes, and what it MUST NOT.

Owner, 31 Aug 2026: "a auto close one on either profit or end of day", and
"lets get in and out at end".

These guard the two ways an auto-close goes wrong: failing to close what it
should (leaving an overnight short the published evidence does not describe),
and closing something it was never meant to touch.
"""
import datetime as _dt

from tradepro_strategies.cli.index_strangle_close import (
    EOD_MINUTES_BEFORE_CLOSE, TARGET_PCT, decide_close, parse_occ, _market_for,
)
from tradepro_strategies.cli.index_strangle_paper import MARKETS


def test_occ_parses_the_contracts_actually_held():
    # The three puts open in the paper account on 31 Aug 2026.
    got = parse_occ("SPY    SEP2026 759 P [SPY   260918P00759000 100]")
    assert got == {"symbol": "SPY", "expiry": "2026-09-18",
                   "right": "P", "strike": 759.0}
    call = parse_occ("SPY    SEP2026 800 C [SPY   260918C00800000 100]")
    assert call["right"] == "C" and call["strike"] == 800.0


def test_an_unparseable_contract_returns_none_rather_than_guessing():
    # The caller LEAVES IT ALONE on None. Closing the wrong contract is worse
    # than closing nothing.
    assert parse_occ("AAPL common stock") is None
    assert parse_occ("") is None


def test_it_only_recognises_configured_strangle_markets():
    # The account also holds wheel and hand-placed positions. A close job that
    # swept every short would flatten those too — which is why this uses the
    # single-leg close, not /options/flatten.
    assert _market_for("SPY", MARKETS)[0] == "SPY"
    assert _market_for("MRVL", MARKETS) is None


def _cfg(close_local="16:00"):
    return {"tz": "America/New_York", "open_local": "09:30",
            "close_local": close_local, "index": "SPY"}


def _at(hh, mm):
    """A UTC instant corresponding to a New York wall-clock time (EDT, -4)."""
    return _dt.datetime(2026, 9, 1, hh + 4, mm, tzinfo=_dt.UTC)


def test_time_exit_fires_even_at_a_loss():
    # Load-bearing: the strikes sit ~2.4 SD away for ONE day but ~0.92 across a
    # week. Carried overnight the geometry changes and none of the published
    # evidence describes the position any more.
    v = decide_close({"credit": 6.0, "current_cost": 9.0}, _cfg(),
                     _at(15, 50))  # 10 min to the bell, deeply underwater
    assert v["close"] is True
    assert v["trigger"] == "end_of_day"


def test_profit_target_banks_it_mid_session():
    v = decide_close({"credit": 6.0, "current_cost": 3.0}, _cfg(), _at(13, 0))
    assert v["close"] is True
    assert v["trigger"] == "profit_target"
    assert v["decayed_pct"] == 50.0


def test_it_holds_when_the_target_is_not_met():
    v = decide_close({"credit": 6.0, "current_cost": 5.0}, _cfg(), _at(13, 0))
    assert v["close"] is False
    assert "target" in v["reason"]


def test_it_does_nothing_when_the_market_is_shut():
    v = decide_close({"credit": 6.0, "current_cost": 1.0}, _cfg(), _at(6, 0))
    assert v["close"] is False
    assert "not open" in v["reason"]


def test_every_verdict_states_a_reason():
    # A close decision with no stated reason cannot be graded later, which is
    # the entire point of recording these.
    for cost, when in ((9.0, _at(15, 50)), (3.0, _at(13, 0)),
                       (5.0, _at(13, 0)), (1.0, _at(6, 0))):
        v = decide_close({"credit": 6.0, "current_cost": cost}, _cfg(), when)
        assert v.get("reason")


def test_the_eod_window_is_wide_enough_to_get_filled():
    assert EOD_MINUTES_BEFORE_CLOSE >= 10
    assert 0 < TARGET_PCT < 1


# ---------------------------------------------------------------------------
# The profit target is judged on the PAIR, never on one leg.
#
# Options level 4 was granted on the evening of 31 Aug 2026, so from the next
# session both legs of a strangle actually fill. A per-leg target would buy
# back whichever leg had decayed and leave the other — the losing one — open
# and NAKED. Strictly worse than holding or closing.
# ---------------------------------------------------------------------------

def test_a_leg_at_target_does_not_close_when_the_pair_is_not():
    # Put decayed 6.00 -> 2.00 (67%, past target on its own).
    # Call moved against us 2.00 -> 5.00.
    # Pair: credit 8.00, cost 7.00 = 12.5% decayed. NOWHERE NEAR the target.
    pair = decide_close({"credit": 8.0, "current_cost": 7.0}, _cfg(), _at(13, 0))
    assert pair["close"] is False

    # The winning leg alone WOULD have closed — this is the trap.
    leg = decide_close({"credit": 6.0, "current_cost": 2.0}, _cfg(), _at(13, 0))
    assert leg["close"] is True
    assert leg["trigger"] == "profit_target"


def test_the_pair_closes_when_both_legs_have_decayed_together():
    pair = decide_close({"credit": 8.0, "current_cost": 3.5}, _cfg(), _at(13, 0))
    assert pair["close"] is True
    assert pair["trigger"] == "profit_target"


def test_an_unmarkable_pair_holds_rather_than_guessing():
    # One leg with no live mark makes the PAIR unmarkable. Half-counting it
    # would understate the cost and fire the target early.
    v = decide_close({"credit": None, "current_cost": None}, _cfg(), _at(13, 0))
    assert v["close"] is False


def test_time_exit_still_fires_on_an_unmarkable_pair():
    # The overnight rule cannot depend on having a mark.
    v = decide_close({"credit": None, "current_cost": None}, _cfg(), _at(15, 50))
    assert v["close"] is True
    assert v["trigger"] == "end_of_day"

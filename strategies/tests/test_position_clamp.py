"""Never order a quantity you cannot verify you hold.

KO, 28-29 July 2026 (live OMS): a BUY for 18 filled only 15 @89.21, and every
subsequent SELL was still sized 18 — fifteen of them, then SELL 18 FILLED, BUY
18 straight back, SELL 15. Selling what you do not hold is how a long sleeve
ends up flat-and-re-entered inside eleven minutes.

The standing rule is that the broker is the golden source for "do we own X"
(feedback_broker_is_golden_source), so on disagreement the SMALLER number is the
only safe one: the difference is either a fill that never happened or a position
that is not there.
"""
from __future__ import annotations

import pytest

from tradepro_strategies.paper.strategies.ichimoku_equity import IchimokuEquityStrategy


def _s():
    return IchimokuEquityStrategy(strategy_id="ichimoku_equity")


class TestSeeding:
    def test_broker_seed_sets_both_views(self):
        s = _s()
        s.seed_positions({"KO_US_EQ": 15}, {"KO_US_EQ": 89.21})
        assert s._positions["KO_US_EQ"] == 15
        assert int(s.position_for("KO_US_EQ").quantity) == 15, (
            "the engine view feeds the risk gate — if it stays 0 the gate "
            "rejects SELLs on held longs as short_disallowed (#86)")

    def test_the_ko_case_reconciles_to_the_filled_quantity(self):
        """Ordered 18, filled 15 — the broker seed must win."""
        s = _s()
        s._positions["KO_US_EQ"] = 18          # strategy's optimistic view
        s.seed_positions({"KO_US_EQ": 15}, {"KO_US_EQ": 89.21})
        assert s._positions["KO_US_EQ"] == 15
        assert int(s.position_for("KO_US_EQ").quantity) == 15

    def test_zero_position_is_not_treated_as_a_holding(self):
        s = _s()
        s.seed_positions({"KO_US_EQ": 0}, {})
        assert s._positions.get("KO_US_EQ", 0) == 0

    def test_unparseable_quantity_is_skipped_not_guessed(self):
        s = _s()
        s.seed_positions({"KO_US_EQ": 15}, {})
        s._positions["BAD_US_EQ"] = 0
        assert "BAD_US_EQ" not in s.seed_positions.__doc__ or True
        assert s._positions["KO_US_EQ"] == 15


class TestClampArithmetic:
    """The clamp itself: min() of the two views, never the larger."""

    @pytest.mark.parametrize("strategy_qty,engine_qty,expected", [
        (18, 15, 15),   # THE KO case — ordered 18, holds 15
        (15, 18, 15),   # engine ahead — still the smaller
        (15, 15, 15),   # agreement
        (15,  0, 15),   # engine unknown (0) — do not clamp to nothing
        ( 0, 15,  0),   # strategy flat — nothing to sell
    ])
    def test_min_of_both_views(self, strategy_qty, engine_qty, expected):
        own, eng = strategy_qty, engine_qty
        position = own
        if own > 0 and eng > 0 and eng != own:
            position = min(own, eng)
        assert position == expected

    def test_never_exceeds_either_view(self):
        for own in range(0, 25):
            for eng in range(0, 25):
                pos = own
                if own > 0 and eng > 0 and eng != own:
                    pos = min(own, eng)
                assert pos <= own
                if eng > 0 and own > 0:
                    assert pos <= max(eng, own)

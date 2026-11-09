"""Which short puts to close — on real money, so every rule is pinned.

Built 1 Nov 2026 against the owner's LIVE book. REWRITTEN 8 Nov 2026 after he
rejected the central rule by name:

    "see thing like CLOSE PLTR 165P 01-15 68d -$938 costs 2.3x what was
     collected (7.14 -> 16.52) - past the 2x line.  is not a real evaluation"

He was right. The old BREACH rule fired on the OPTION'S MARK, which measures
what exiting costs today, not whether the trade is going badly. A short put
loses money in exactly one way: assignment at a basis above the market. So
the rules now ask that, and these tests pin the real positions that exposed it.

TWO OF THE OLD TESTS ASSERTED THE BUG, which is worth recording. The worst was
`test_twice_the_credit_is_CLOSE`: strike 100, spot 130, mark 2x the credit. A
put 30% out of the money cannot be worth twice its credit — the fixture was an
impossible position, and BREACH fired on it and was graded correct. A rule
whose test case cannot exist in a market is a rule measuring nothing.
"""
import pytest

from tradepro_strategies.quant_engine.options.economics import cash_secured_put
from tradepro_strategies.quant_engine.options.manage import (
    ManageRules,
    assess,
)


# ── the owner's objection, pinned ─────────────────────────────────────────
def test_PLTR_165P_is_NOT_a_close_the_basis_holds():
    """The position he named. Strike 165, credit 7.14, so the effective basis
    is 157.86 — and the stock was 160.38. Assignment hands him PLTR BELOW the
    market. The old rule said CLOSE because the option was down 2.3x."""
    v = assess(strike=165, spot=160.38, credit=7.13952, mark=16.558, dte=68)
    assert v.action == "WATCH", "assignment below market is not a reason to close"
    assert v.effective_basis == pytest.approx(157.86, abs=0.01)
    assert v.basis_cushion_pct > 0
    assert "157.86" in v.why and "cushion" in v.why


def test_the_six_live_winners_stop_reading_as_CLOSE():
    """Six live positions sat ABOVE their effective basis while the old rule
    flagged two thirds of the book. A board that cries wolf on two thirds of a
    book is a board the owner stops opening."""
    live = [
        dict(strike=165, spot=160.38, credit=7.13952, mark=16.558, dte=68),
        dict(strike=450, spot=449.04, credit=14.099236, mark=48.036, dte=68),
        dict(strike=270, spot=275.88, credit=15.191854, mark=31.294, dte=103),
        dict(strike=280, spot=275.88, credit=17.289311, mark=32.319, dte=68),
        dict(strike=330, spot=346.45, credit=11.594208, mark=21.138, dte=68),
        dict(strike=80, spot=80.91, credit=6.209539, mark=10.477, dte=103),
    ]
    for c in live:
        v = assess(**c)
        assert v.action != "CLOSE", f"{c['strike']}P closed on a favourable basis"
        assert v.basis_cushion_pct > 0


# ── the rule that replaced it ─────────────────────────────────────────────
def test_DELL_420P_is_a_CLOSE_on_basis():
    """The position the owner picked out by eye as the only risky one, and the
    arithmetic agrees: basis 388.06 against a 328.53 market is 15.3% of
    overpay, and it is the largest hold-to-expiry loss in the book."""
    v = assess(strike=420, spot=328.53, credit=31.943169, mark=112.318, dte=103)
    assert v.action == "CLOSE" and v.rule == "basis"
    assert "15.3%" in v.why and "388.06" in v.why


def test_the_basis_line_is_configurable():
    tight = assess(strike=420, spot=328.53, credit=31.943169, mark=112.318,
                   dte=103, rules=ManageRules(max_basis_shortfall_pct=20.0))
    assert tight.action != "CLOSE", "15.3% is inside a 20% line"


def test_a_mark_far_above_the_credit_is_NOT_itself_a_close():
    """GOOGL 315P: 4.2x the credit, which the old rule called BREACH. The
    basis is 305.96 against 279.84 — 8.5% of overpay, inside the line. The
    multiple is not a verdict."""
    v = assess(strike=315, spot=279.84, credit=9.04, mark=37.52, dte=75)
    assert v.action == "WATCH"
    assert v.close_pnl < 0 < v.forfeit


# ── one definition of the basis ───────────────────────────────────────────
def test_the_basis_is_the_breakeven_from_economics_not_a_second_formula():
    """DUPLICATE DEFINITIONS ARE THIS DESK'S #1 BUG SHAPE. The effective basis
    is the CSP breakeven and must come from the one function that defines it,
    so a change there cannot leave this module disagreeing."""
    for strike, credit in ((165, 7.13952), (420, 31.943169), (70, 5.53952)):
        v = assess(strike=strike, spot=strike * 0.9, credit=credit,
                   mark=credit * 2, dte=60)
        assert v.effective_basis == cash_secured_put(
            strike=strike, credit_per_share=credit).breakeven


# ── close-now versus hold-to-expiry ───────────────────────────────────────
def test_the_verdict_carries_what_closing_actually_costs():
    """A real position: spot 449.04 against a 450 strike, so of a 48.04 mark
    only 0.96 is intrinsic. Exiting books a loss while letting it expire at the
    SAME price PAYS. The owner's damage-control plan was to exit this one, and
    nothing on the screen said what the decay cost."""
    v = assess(strike=450, spot=449.04, credit=14.099236, mark=48.036, dte=68)
    assert v.close_pnl == pytest.approx(-3393.7, abs=1.0)
    assert v.hold_pnl == pytest.approx(1313.9, abs=1.0)
    assert v.forfeit == pytest.approx(4707.6, abs=1.0)


def test_hold_pnl_uses_intrinsic_only_never_the_mark():
    # Deep out of the money: at expiry the put is worth nothing, so holding
    # banks the whole credit however the mark happens to be quoted today.
    v = assess(strike=100, spot=140, credit=4.0, mark=3.5, dte=60)
    assert v.hold_pnl == pytest.approx(400.0)
    assert v.close_pnl == pytest.approx(50.0)


def test_a_close_verdict_states_the_decay_it_gives_up():
    v = assess(strike=420, spot=328.53, credit=31.943169, mark=112.318, dte=103)
    assert "forfeits" in v.why and "time value" in v.why


# ── assignment: a deadline, and whether it is good news ───────────────────
def test_in_the_money_and_near_expiry_with_a_BAD_basis_is_CLOSE():
    """MRVL Nov20'26 230P: 14.6% in the money, 19 days, basis 224.96 against
    196.40. Time has stopped being a defence and the shares arrive dear."""
    v = assess(strike=230, spot=196.40, credit=5.04, mark=36.15, dte=19)
    assert v.action == "CLOSE" and v.rule == "assignment"
    assert "19d" in v.why and "14.6%" in v.why


def test_in_the_money_and_near_expiry_with_a_GOOD_basis_is_WATCH():
    """The case the old code could not express: assignment IS coming, and it
    is profitable. On a book whose thesis is "I'll take the shares", that is
    the outcome being paid for, not an emergency."""
    # mark 7.00 against an 8.00 credit is only 12.5% captured, so TARGET
    # stays out of the way. (First draft of this test used 4.00 and hit the
    # 50% target rule instead — the fixture was testing a different rule than
    # its own name claimed.)
    v = assess(strike=100, spot=97, credit=8.0, mark=7.0, dte=10)
    assert v.action == "WATCH" and v.rule == "assignment"
    assert "BELOW the market" in v.why and "Profitable if taken" in v.why


def test_target_outranks_assignment():
    # 50% captured is free money and nothing is in tension; it is reported
    # first precisely because there is nothing to weigh.
    v = assess(strike=100, spot=99, credit=8.0, mark=4.0, dte=10)
    assert v.rule == "target"


# ── taking profit ─────────────────────────────────────────────────────────
def test_half_the_credit_captured_is_CLOSE():
    v = assess(strike=100, spot=120, credit=4.00, mark=2.00, dte=60)
    assert v.action == "CLOSE" and v.rule == "target"
    assert "50%" in v.why


def test_just_under_the_target_is_not_closed():
    v = assess(strike=100, spot=120, credit=4.00, mark=2.10, dte=60)
    assert v.action != "CLOSE"


def test_the_live_winners_are_NOT_at_target():
    skhy = assess(strike=160, spot=162.09, credit=26.28, mark=23.41, dte=75)
    vlo = assess(strike=330, spot=384.57, credit=11.59, mark=9.55, dte=75)
    assert skhy.action == "HOLD" and vlo.action == "HOLD"


# ── not assessable is not fine ────────────────────────────────────────────
@pytest.mark.parametrize("kw", [
    {"strike": None}, {"spot": None}, {"credit": None}, {"mark": None},
])
def test_a_missing_input_is_UNKNOWN_never_HOLD(kw):
    """'We could not assess this' and 'this is fine' are different
    statements. A book where unreadable rows look settled is how a position
    gets forgotten."""
    base = dict(strike=100, spot=120, credit=4.0, mark=3.0, dte=40)
    v = assess(**{**base, **kw})
    assert v.action == "UNKNOWN"
    assert v.urgency == 1, "unknown must not sort to the bottom like a HOLD"
    assert v.effective_basis is None, "no inputs means no arithmetic, not zeros"


def test_a_comfortable_position_is_HOLD():
    v = assess(strike=100, spot=140, credit=4.0, mark=3.5, dte=60)
    assert v.action == "HOLD" and v.urgency == 0


# ── every verdict shows its working ───────────────────────────────────────
def test_every_verdict_states_a_number_and_its_arithmetic():
    cases = [
        dict(strike=230, spot=196.40, credit=5.04, mark=36.15, dte=19),
        dict(strike=100, spot=120, credit=4.00, mark=2.00, dte=60),
        dict(strike=420, spot=328.53, credit=31.943169, mark=112.318, dte=103),
        dict(strike=315, spot=279.84, credit=9.04, mark=37.52, dte=75),
        dict(strike=100, spot=140, credit=4.0, mark=3.5, dte=60),
        dict(strike=165, spot=160.38, credit=7.13952, mark=16.558, dte=68),
    ]
    for c in cases:
        v = assess(**c)
        assert any(ch.isdigit() for ch in v.why), f"{v.action} gave no number"
        assert v.effective_basis is not None and v.hold_pnl is not None


def test_urgency_sorts_act_above_watch_above_hold():
    act = assess(strike=420, spot=328.53, credit=31.943169, mark=112.318, dte=103)
    watch = assess(strike=100, spot=98, credit=5.00, mark=8.00, dte=75)
    hold = assess(strike=100, spot=140, credit=4.0, mark=3.5, dte=60)
    assert act.urgency > watch.urgency > hold.urgency

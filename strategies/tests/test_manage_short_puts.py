"""Which short puts to close — on real money, so every rule is pinned.

Built 1 Nov 2026 against the owner's LIVE book: 22 short puts at -$20,233
inside an account with £36,911 of excess liquidity against a £168,967
maintenance requirement. "i need indications on puts i shd be closing".
"""
import pytest

from tradepro_strategies.quant_engine.options.manage import (
    ManageRules,
    assess,
)


# ── the rule that matters most on this book ───────────────────────────────
def test_in_the_money_and_near_expiry_is_CLOSE():
    """MRVL Nov20'26 230P, the real position: spot 196.40, 19 days left.

    14.6% in the money and would put $23,000 of stock into an account with
    £36,911 of excess liquidity. Time has stopped being a defence.
    """
    v = assess(strike=230, spot=196.40, credit=5.04, mark=36.15, dte=19)
    assert v.action == "CLOSE" and v.rule == "assignment"
    assert "19d" in v.why and "14.6%" in v.why


def test_in_the_money_with_TIME_and_a_modest_loss_is_WATCH():
    """Shallow in the money, 75 days to recover, loss still under the 2x line.

    THIS TEST WAS WRONG FIRST TIME and the code was right. I reached for
    GOOGL Jan15'27 315P as the example without checking the multiple: it
    costs 4.2x what was collected (9.04 -> 37.52), so BREACH fires, correctly.
    A real WATCH is a position that has gone against us without the thesis
    having broken.
    """
    v = assess(strike=100, spot=98, credit=5.00, mark=8.00, dte=75)
    assert v.action == "WATCH"
    assert "2.0%" in v.why and "75d" in v.why


def test_the_live_GOOGL_315P_is_a_BREACH_not_a_watch():
    """The real position, read honestly: 11.2% in the money, 75 days left,
    and costing 4.2x the credit. Time left does not undo a thesis that has
    moved this far — the rule fires and should."""
    v = assess(strike=315, spot=279.84, credit=9.04, mark=37.52, dte=75)
    assert v.action == "CLOSE" and v.rule == "breach"
    assert "4.2x" in v.why


def test_assignment_outranks_the_loss_rule():
    # A position can satisfy several rules at once. The one with a DEADLINE
    # is the one that should be reported, because it is the one that forces a
    # decision soonest.
    v = assess(strike=230, spot=196.40, credit=5.04, mark=36.15, dte=19)
    assert v.rule == "assignment", "a deadline outranks a drawdown"


# ── taking profit ─────────────────────────────────────────────────────────
def test_half_the_credit_captured_is_CLOSE():
    v = assess(strike=100, spot=120, credit=4.00, mark=2.00, dte=60)
    assert v.action == "CLOSE" and v.rule == "target"
    assert "50%" in v.why


def test_just_under_the_target_is_not_closed():
    v = assess(strike=100, spot=120, credit=4.00, mark=2.10, dte=60)
    assert v.action != "CLOSE"


def test_the_live_winners_are_NOT_at_target():
    # SKHY (+$287) and VLO (+$205) are profitable but nowhere near 50%
    # captured. A board that said "close" on every green position would be
    # noise, and the owner would stop reading it.
    skhy = assess(strike=160, spot=162.09, credit=26.28, mark=23.41, dte=75)
    vlo = assess(strike=330, spot=384.57, credit=11.59, mark=9.55, dte=75)
    assert skhy.action == "HOLD" and vlo.action == "HOLD"


# ── the loss rule ─────────────────────────────────────────────────────────
def test_twice_the_credit_is_CLOSE():
    v = assess(strike=100, spot=130, credit=5.00, mark=10.00, dte=90)
    assert v.action == "CLOSE" and v.rule == "breach"
    assert "2.0x" in v.why


def test_the_multiple_is_configurable():
    r = ManageRules(loss_multiple_of_credit=3.0)
    v = assess(strike=100, spot=130, credit=5.00, mark=10.00, dte=90, rules=r)
    assert v.action != "CLOSE"


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


def test_a_comfortable_position_is_HOLD():
    v = assess(strike=100, spot=140, credit=4.0, mark=3.5, dte=60)
    assert v.action == "HOLD" and v.urgency == 0


# ── every verdict shows its working ───────────────────────────────────────
def test_every_verdict_states_a_number():
    cases = [
        dict(strike=230, spot=196.40, credit=5.04, mark=36.15, dte=19),
        dict(strike=100, spot=120, credit=4.00, mark=2.00, dte=60),
        dict(strike=100, spot=130, credit=5.00, mark=10.00, dte=90),
        dict(strike=315, spot=279.84, credit=9.04, mark=37.52, dte=75),
        dict(strike=100, spot=140, credit=4.0, mark=3.5, dte=60),
    ]
    for c in cases:
        v = assess(**c)
        assert any(ch.isdigit() for ch in v.why), f"{v.action} gave no number"


def test_urgency_sorts_act_above_watch_above_hold():
    act = assess(strike=230, spot=196.40, credit=5.04, mark=36.15, dte=19)
    watch = assess(strike=100, spot=98, credit=5.00, mark=8.00, dte=75)
    hold = assess(strike=100, spot=140, credit=4.0, mark=3.5, dte=60)
    assert act.urgency > watch.urgency > hold.urgency

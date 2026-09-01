"""The win rate a swing candidate needs just to break even.

Owner, 1 Sep 2026, shown an IWM signal at R:R 0.39: "i am not convinced".

He was right, and the screen gave him no way to see why. R:R is SORTED on and
never FILTERED — the comment beside the sort even calls it "the number that
decides whether a bracket is worth placing", and nothing decides. On a day with
ONE candidate the ranking is a no-op, so whatever turns up becomes "today's
candidate" with nothing having judged it.

IWM: risk 23.25, reward 9.01. It needs 72.1% of these to win. The strategy's own
backtest wins 73.2% over 2,523 trades — a margin of ONE POINT.

Stating the breakeven turns a ratio nobody can price into the single comparison
that settles it. It deliberately does NOT filter: choosing a threshold without a
backtest is the tuning this project has already had to retract.
"""
import pytest


def breakeven(rr: float) -> float:
    return round(100.0 / (1.0 + rr), 1)


def test_the_iwm_case_that_prompted_this():
    # risk 290.57-267.32 = 23.25 ; reward 299.58-290.57 = 9.01
    rr = round(9.01 / 23.25, 2)
    assert rr == 0.39
    assert breakeven(rr) == 71.9  # ~72%, against a 73.2% backtest win rate


@pytest.mark.parametrize("rr,need", [
    (1.0, 50.0),    # symmetric: a coin flip
    (2.0, 33.3),    # 2:1 needs only a third
    (0.5, 66.7),
    (0.39, 71.9),   # today's IWM
    (0.25, 80.0),   # needs 4 in 5 — beyond anything this strategy has shown
])
def test_breakeven_is_the_standard_identity(rr, need):
    assert breakeven(rr) == need


def test_a_worse_ratio_always_demands_a_higher_win_rate():
    xs = [0.25, 0.39, 0.5, 1.0, 2.0]
    needs = [breakeven(x) for x in xs]
    assert needs == sorted(needs, reverse=True)


def test_the_screen_reports_it_and_still_does_not_filter():
    # The value must be SHOWN. It must NOT become a silent gate — that would be
    # a threshold chosen without evidence.
    import inspect
    from tradepro_strategies.cli import swing_candidates as S
    src = inspect.getsource(S)
    assert "breakeven_win_pct" in src
    assert "needs" in src
    for banned in ("if rr < ", "reward_risk < ", "rr <= "):
        assert banned not in src, f"{banned!r} would be an unbacktested filter"

"""The sleeve must be BOUNDED, and must choose well when it is full.

Until 25 Aug 2026 the live Swing strategy had neither property. It had no
concurrency cap at all — measured over 16 years the rule asks for a median of
7 open positions, 28 at the 95th percentile and a peak of 62, which at 5% each
is 310% of capital — and when it did have to choose it chose alphabetically,
because that is the order the bus reaches symbols in.

Measured cost of choosing alphabetically: per-trade mean falls from +1.10%
(take every signal) to +0.52% (cap 8, first-come). More than half the edge.
"""
from __future__ import annotations

from tradepro_strategies.paper.strategies.mean_reversion_swing import (
    MeanReversionSwingStrategy as S,
)


def test_the_sleeve_has_a_cap_at_all():
    """The regression that matters: an unbounded sleeve can commit 310% of
    capital, and nothing in the strategy, the router or the gates document
    ever noticed."""
    assert isinstance(S.MAX_CONCURRENT, int)
    assert 0 < S.MAX_CONCURRENT <= 62      # 62 = measured peak concurrency


def test_the_cap_and_the_position_size_cannot_exceed_capital():
    """12 slots at 5% is 60% committed. A cap that can exceed 100% is not a
    cap — it is leverage nobody asked for."""
    assert S.MAX_CONCURRENT * S.DEFAULT_POSITION_PCT <= 1.0


def test_the_cap_is_wide_enough_to_gather_the_evidence():
    """A PAPER sleeve's job is observations, not compounding.

    Measured: a cap of 12 refuses 33.8% of signals — 95 of the last 275. At
    ~7 signals/week that leaves ~55 completed trades in twelve weeks, short of
    the 70-80 needed to tell a 65% win rate from a coin flip. The window would
    have closed unable to answer its own question. A cap of 30 refuses 5%."""
    assert S.MAX_CONCURRENT >= 25


def test_the_cap_and_the_size_cannot_ask_for_more_than_the_account_has():
    """30 slots at the old 5% would ask for 150% of the account, and the broker
    would reject the surplus — silently turning a position limit into a
    rejection log. Size follows the cap, not the other way round."""
    assert S.MAX_CONCURRENT * S.DEFAULT_POSITION_PCT <= 1.0
    assert S.DEFAULT_POSITION_PCT > 0


def test_ranking_is_by_reward_risk_and_not_by_sigma():
    """Six rules were tested with gates written first. Only reward:risk is
    positive in all four two-split cells; deepest-sigma is NEGATIVE in three
    of them and loses to the alphabetical control at cap 15.

    This test pins the DECISION, not the arithmetic: if someone later 'improves'
    the ranking to sigma because it reads as the more natural expression of a
    mean-reversion rule — which is exactly what I predicted and got wrong —
    this should stop them and send them to RANKING_GATES_V1.md.
    """
    import inspect
    from tradepro_strategies.signals.mean_reversion import reward_risk

    # The strategy must DEFER to the shared key, not carry its own copy. It
    # briefly did carry one, and a ranking that disagreed between the screen
    # and the strategy would fail gate F1 looking like a data problem.
    assert "reward_risk(" in inspect.getsource(S._ranked_today)
    assert "STOP_PCT" in inspect.getsource(reward_risk), \
        "the key must be measured against the fixed stop, not a relative one"

    # And it must actually rank by upside-per-risk: a deeper dip on the same
    # 20-day mean must score higher, and a name whose target is barely above
    # the close must score near zero however dramatic the dip looks.
    shallow = [100.0] * 19 + [98.0]
    deep = [100.0] * 19 + [90.0]
    assert reward_risk(deep, 19) > reward_risk(shallow, 19) > 0


def test_ranking_fails_open_not_closed():
    """A missing ranking must degrade to the old first-come behaviour, never
    to 'stop trading'. The cap already bounds the risk; a ranking failure that
    silently halted the sleeve would be indistinguishable from a quiet day,
    which is the failure mode gate F5 exists to prevent."""
    src = __import__("inspect").getsource(S._ranked_today)
    assert "return None" in src
    entry = __import__("inspect").getsource(S.on_bar)
    assert "rank is not None" in entry, "a None ranking must not block the entry"

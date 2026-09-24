"""A BUY limit IBKR refuses is an entry silently lost.

23 Sep 2026, one cycle, three entries placed nowhere:

    AMP   limit 543.76  market 513.60  +5.9%   REJECTED
    ALL   limit 246.50  market 229.44  +7.4%   REJECTED
    FANG  limit 192.11  market 185.07  +3.8%   REJECTED

"We cannot accept an order at a limit price at or more aggressive than
190.511058. Please submit your order using a limit price that is closer to the
current market price of 185.07." IBKR refuses a BUY limit much more than ~3%
above the market whatever the reason for it.

All three were limit == signalRefPrice * 1.0150 — the anti-chase cap measured
from the signal close, which exists so the strategy can never pay up through
the band that defined the trade (3 Sep: a MARKET order filled SNOW at 367.44 on
a 305.84 reference, 41 points above its own target). The cap is right. But when
the market has FALLEN more than 3% below it, the mean-reversion case is
STRONGER, a limit above the market fills at the market anyway, and the desk was
losing exactly the entries it most wanted.

The rule here LOWERS ONLY. It can never make an entry more aggressive than the
anti-chase cap already allows, and it cannot resurrect the SNOW fill.
"""
import pytest

from tradepro_strategies.paper.strategies import mean_reversion_swing as M


@pytest.fixture(autouse=True)
def _fixed_config(monkeypatch):
    monkeypatch.setattr(M, "_chase_cache", [1.5], raising=False)
    monkeypatch.setattr(M, "_band_cache", [2.0], raising=False)


def _limit_for(signal_close, live):
    """The limit the strategy would send — through the REAL rule.

    reband_limit is the function the entry path calls. Re-implementing the
    arithmetic here instead would be a test that passes while the shipped code
    stays broken, which is the trap this desk keeps falling into.
    """
    cap = round(signal_close * (1 + M._entry_chase_pct() / 100.0), 2)
    limit, _ = M.reband_limit(cap, live, M._entry_band_pct())
    return limit


def test_the_anti_chase_cap_is_unchanged_when_the_market_has_not_moved():
    assert _limit_for(100.0, 100.0) == pytest.approx(101.50)


def test_a_market_that_FELL_gets_a_limit_ibkr_accepts():
    # FANG: signal 189.27 -> cap 192.11, live 185.07. The old code sent 192.11
    # and was refused; 2% above live is 188.77, inside IBKR's ~2.94% band.
    got = _limit_for(189.27, 185.07)
    assert got == pytest.approx(188.77, abs=0.01)
    assert got < 192.11, "must be lower than the refused limit"
    assert got > 185.07, "still above the market, so it fills at the market"


@pytest.mark.parametrize("signal,live,refused", [
    (535.72, 513.60, 543.76),   # AMP  +5.9%
    (242.86, 229.44, 246.50),   # ALL  +7.4%
    (189.27, 185.07, 192.11),   # FANG +3.8%
])
def test_all_three_of_23_sep_become_acceptable(signal, live, refused):
    got = _limit_for(signal, live)
    assert got < refused
    assert (got / live - 1) <= 0.0294, f"{got} is still outside IBKR's band"


def test_it_LOWERS_ONLY_and_never_raises():
    # A market that RALLIED must not lift the limit — that is the 3 Sep SNOW
    # failure, paying up through the band that defined the trade.
    assert _limit_for(100.0, 130.0) == pytest.approx(101.50)
    limit, lowered = M.reband_limit(101.50, 130.0, 2.0)
    assert limit == pytest.approx(101.50) and lowered is False


def test_no_live_price_changes_nothing():
    # A missing quote must not silently alter an entry.
    for absent in (None, 0, 0.0):
        limit, lowered = M.reband_limit(192.11, absent, 2.0)
        assert limit == pytest.approx(192.11) and lowered is False


def test_the_flag_reports_whether_it_acted():
    # The caller logs only when it actually moved the limit; a wrong flag would
    # either hide a change or claim one that never happened.
    assert M.reband_limit(192.11, 185.07, 2.0)[1] is True
    assert M.reband_limit(101.50, 100.0, 2.0)[1] is False


def test_the_band_default_sits_inside_ibkrs_own_threshold():
    # IBKR quoted 190.511058 against a market of 185.07 = 2.94%. A default at
    # or above that would keep being refused.
    assert 0 < M._DEFAULT_BAND_PCT < 2.94

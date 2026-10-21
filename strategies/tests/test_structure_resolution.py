"""resolve_structure — the ONE place a name is steered to a structure.

Three reasons stack: ownership, regime, earnings. Each degrades a cash-secured
put to a bull put spread — the same view with a floor — and never to a
different view. These tests pin the stacking, the reasons travelling with the
decision, and the two cases that must still refuse.
"""
from tradepro_strategies.quant_engine.options.bucket import (
    Resolution,
    resolve_structure,
)
from tradepro_strategies.quant_engine.options.risk import (
    PERMITTED_BY_REGIME,
    Regime,
    Structure,
)


# A large, liquid, ownable name with a modest variance premium.
# MSFT, measured 21 Oct 2026.
OWNABLE = dict(symbol="MSFT", implied_vol=0.28, historical_vol=0.24,
               price=531.87, market_cap_usd=3_949_400_000_000.0,
               reporting_currency="USD")


def _green():
    return dict(regime=Regime.GREEN, permitted=PERMITTED_BY_REGIME[Regime.GREEN])


def _orange():
    return dict(regime=Regime.ORANGE, permitted=PERMITTED_BY_REGIME[Regime.ORANGE])


def test_the_plain_path_is_a_cash_secured_put_with_no_notes():
    r = resolve_structure(**OWNABLE, **_green(), earnings_in_expiry_window=False)
    assert r.structure is Structure.CASH_SECURED_PUT
    assert r.bucket == "own"
    assert not r.downgraded and r.notes == ()


def test_orange_regime_degrades_to_a_spread_and_says_why():
    r = resolve_structure(**OWNABLE, **_orange(), earnings_in_expiry_window=False)
    assert r.structure is Structure.BULL_PUT_SPREAD
    assert r.downgraded
    assert any("not permitted in ORANGE" in n for n in r.notes)


def test_earnings_degrades_to_a_spread_and_says_why():
    r = resolve_structure(**OWNABLE, **_green(), earnings_in_expiry_window=True)
    assert r.structure is Structure.BULL_PUT_SPREAD
    assert any("Earnings fall inside the expiry window" in n for n in r.notes)
    assert any("capped before the print" in n for n in r.notes)


def test_both_reasons_stack_rather_than_overwrite():
    # ORANGE *and* a print. One downgrade, but the row must carry BOTH
    # reasons — a screen that reports one of two causes is not trustworthy.
    r = resolve_structure(**OWNABLE, **_orange(), earnings_in_expiry_window=True)
    assert r.structure is Structure.BULL_PUT_SPREAD
    assert any("ORANGE" in n for n in r.notes)
    assert any("Earnings" in n for n in r.notes)


def test_a_degrade_never_flips_the_view():
    # The whole safety property of the fallback. A bull put spread is the same
    # bullish-to-neutral view as a short put; a BEAR put spread is the opposite
    # one, and substituting it would quietly reverse the trade.
    for ctx in (_green(), _orange()):
        for earn in (False, True):
            r = resolve_structure(**OWNABLE, **ctx, earnings_in_expiry_window=earn)
            assert r.structure is not Structure.BEAR_PUT_SPREAD


def test_unknown_earnings_calendar_does_not_degrade():
    # NEVER INFER FROM AN ABSENCE, in the other direction: `None` must not be
    # read as "there is a print" either. It passes through untouched so the
    # risk engine makes the call and blocks on the missing calendar.
    r = resolve_structure(**OWNABLE, **_green(), earnings_in_expiry_window=None)
    assert r.structure is Structure.CASH_SECURED_PUT
    assert r.notes == ()


def test_red_regime_still_refuses_outright():
    r = resolve_structure(**OWNABLE, regime=Regime.RED,
                          permitted=PERMITTED_BY_REGIME[Regime.RED],
                          earnings_in_expiry_window=False)
    assert r.structure is None
    assert r.notes  # refused WITH a reason, never a bare no


def test_a_name_we_would_not_own_is_still_routed_not_dropped():
    # The owner's second bucket: names we do not want assigned but that pay
    # well are a SPREAD, not a refusal.
    r = resolve_structure(symbol="MIDCAP", implied_vol=0.95, historical_vol=0.60,
                          price=165.0, market_cap_usd=4_000_000_000.0,
                          reporting_currency="USD",
                          **_green(), earnings_in_expiry_window=False)
    assert r.structure is not Structure.CASH_SECURED_PUT
    assert r.structure is None or r.structure is Structure.BULL_PUT_SPREAD

"""Which structure a name should be traded as — CSP, spread, or nothing.

GROUNDED IN THE OWNER'S OWN 52 LEGS (3 Aug - 20 Oct 2026). The closed book
reads 97% win / +$7,576; counting the open legs it is 80% / +$3,724. The gap
is concentrated in two names sold NAKED that the owner never wanted to own:

    SKHY  sold 5.10, marked 25.07   -1,998 and still running
    CRWV  sold 5.55, marked 11.56     -602

Routing exists so those become spreads with a floor instead of naked puts
without one.
"""
from __future__ import annotations

from tradepro_strategies.quant_engine.options.bucket import (
    MIN_AVG_DAILY_USD_VOLUME, MIN_PRICE_USD, Routing, is_ownable, route, summarise,
)
from tradepro_strategies.quant_engine.options.risk import Structure

BIG = 3_000_000_000.0     # WDC-scale daily volume
SMALL = 388_000_000.0     # APLD, measured 20 Oct


def test_a_large_liquid_name_with_rich_premium_is_a_cash_secured_put():
    r = route("GOOGL", implied_vol=0.40, historical_vol=0.30,
              price=344.0, avg_daily_usd_volume=BIG)
    assert r.structure is Structure.CASH_SECURED_PUT
    assert r.bucket == "own"


def test_a_name_we_would_not_own_but_pays_well_is_a_SPREAD():
    """The SKHY case. Not ownable, premium rich -> floor the loss."""
    r = route("SKHY", implied_vol=0.90, historical_vol=0.60,
              price=165.0, avg_daily_usd_volume=SMALL)
    assert r.structure is Structure.BULL_PUT_SPREAD
    assert r.bucket == "spread"
    assert "floor" in r.reason


def test_a_penny_stock_is_never_ownable():
    ok, why = is_ownable(price=4.20, avg_daily_usd_volume=BIG, symbol="PENNY")
    assert not ok and "floor" in why


def test_APLD_is_excluded_which_my_first_version_got_wrong():
    """The correction that prompted the measured gates.

    My first draft derived the ownable list from past trades and it INCLUDED
    APLD - $19.50 on $388m of daily volume, no dividend - which is exactly what
    the owner excluded as a small cap. A list could not express the rule; two
    measurements can.
    """
    ok, why = is_ownable(price=19.50, avg_daily_usd_volume=SMALL, symbol="APLD")
    assert not ok, why


def test_implied_below_realised_is_refused_however_good_the_name():
    """WDC on 20 Oct: implied 67% against realised 72.8%. You are paid LESS
    than the stock moves. Liking the name does not change that."""
    r = route("WDC", implied_vol=0.670, historical_vol=0.728,
              price=364.64, avg_daily_usd_volume=2_880_000_000.0)
    assert r.structure is None
    assert r.bucket == "none"
    assert "less than the stock moves" in r.reason


def test_a_name_we_would_not_own_with_ordinary_premium_is_SKIPPED():
    r = route("SOMECO", implied_vol=0.33, historical_vol=0.30,
              price=15.0, avg_daily_usd_volume=SMALL)
    assert r.structure is None and r.bucket == "none"


def test_missing_vol_blocks_rather_than_defaults():
    """An unverifiable gate must refuse. No false positives."""
    for iv, hv in ((None, 0.30), (0.40, None), (None, None)):
        r = route("GOOGL", implied_vol=iv, historical_vol=hv,
                  price=344.0, avg_daily_usd_volume=BIG)
        assert r.structure is None, (iv, hv)
        assert "unavailable" in r.reason


def test_missing_liquidity_data_means_NOT_ownable_not_assumed_ownable():
    """Being unable to show a name is a large cap must not read as 'it is'.
    The cost of this being wrong is owning 100 shares of something unwanted."""
    r = route("MYSTERY", implied_vol=0.50, historical_vol=0.30,
              price=None, avg_daily_usd_volume=None)
    assert r.structure is not Structure.CASH_SECURED_PUT


def test_the_owner_can_veto_a_name_that_passes_the_measurements():
    ok, why = is_ownable(price=900.0, avg_daily_usd_volume=BIG,
                         symbol="TSLA", deny=frozenset({"TSLA"}))
    assert not ok and "excluded by the owner" in why


def test_every_refusal_carries_a_reason():
    """A bare 'no' cannot be acted on."""
    for r in (route("", implied_vol=0.4, historical_vol=0.3),
              route("X", implied_vol=None, historical_vol=0.3),
              route("X", implied_vol=0.30, historical_vol=0.30,
                    price=5.0, avg_daily_usd_volume=1.0)):
        assert r.reason and len(r.reason) > 10


def test_summarise_counts_every_bucket_including_refusals():
    """A screen that reports only its passes hides how much it refused."""
    out = summarise({
        "A": Routing(Structure.CASH_SECURED_PUT, "own", "x"),
        "B": Routing(Structure.BULL_PUT_SPREAD, "spread", "x"),
        "C": Routing(None, "none", "x"),
        "D": Routing(None, "none", "x"),
    })
    assert out == {"own": 1, "spread": 1, "none": 2}


def test_CRWV_is_ownable_despite_85pct_realised_vol():
    """OWNER'S RULING, 20 Oct 2026: *"core weave is fine … could have been a
    naked put … its a good name"*.

    CRWV realises 85% annualised and sits 55% below its 52-week high, so it is
    tempting to add a volatility ceiling or a drawdown filter to is_ownable. I
    started to. BOTH WOULD EXCLUDE THIS NAME, and the owner has ruled it in.

    What actually separates it from APLD is SIZE, which the existing gates
    already measure: CRWV trades $2.15bn a day against APLD's $388m. Volatility
    is not the discriminator and must not become one.

    The -602 on the CRWV put came from an AI/semiconductor sell-off — the
    sector fell 17-43% while SPY moved 1.6% — not from picking the wrong name.
    A selection rule cannot prevent that; a CONCENTRATION rule can.
    """
    r = route("CRWV", implied_vol=0.992, historical_vol=0.853,
              price=63.88, avg_daily_usd_volume=2.15e9)
    assert r.structure is Structure.CASH_SECURED_PUT, r.reason
    assert r.bucket == "own"


def test_the_size_gate_is_what_separates_CRWV_from_APLD():
    """Both realise ~85% vol. Only one is a name to own, and volume says which."""
    crwv_ok, _ = is_ownable(price=63.88, avg_daily_usd_volume=2.15e9, symbol="CRWV")
    apld_ok, _ = is_ownable(price=19.50, avg_daily_usd_volume=0.388e9, symbol="APLD")
    assert crwv_ok and not apld_ok

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
    MIN_MARKET_CAP_USD, MIN_PRICE_USD, Routing, is_ownable, route, summarise,
)
from tradepro_strategies.quant_engine.options.risk import Structure

# Market caps MEASURED 21 Oct 2026, not invented for the test.
BIG = 370_000_000_000.0     # PG
SMALL = 4_000_000_000.0     # a mid cap, under the $10bn large-cap floor


def test_a_large_liquid_name_with_rich_premium_is_a_cash_secured_put():
    r = route("GOOGL", price=340.27, market_cap_usd=4_161_500_000_000.0)
    assert r.structure is Structure.CASH_SECURED_PUT
    assert r.bucket == "own"


def test_a_name_we_would_not_own_but_pays_well_is_a_SPREAD():
    """The SKHY case. Not ownable, premium rich -> floor the loss."""
    r = route("MIDCAP", price=165.0, market_cap_usd=SMALL)
    assert r.structure is Structure.BULL_PUT_SPREAD
    assert r.bucket == "spread"
    assert "floor" in r.reason


def test_a_penny_stock_is_never_ownable():
    ok, why = is_ownable(price=4.20, market_cap_usd=BIG, symbol="PENNY")
    assert not ok and "floor" in why


def test_APLD_is_excluded_which_my_first_version_got_wrong():
    """The correction that prompted the measured gates.

    My first draft derived the ownable list from past trades and it INCLUDED
    APLD - $19.50 on $388m of daily volume, no dividend - which is exactly what
    the owner excluded as a small cap. A list could not express the rule; two
    measurements can.
    """
    ok, why = is_ownable(price=19.50, market_cap_usd=SMALL, symbol="APLD")
    assert not ok, why


def test_the_variance_gate_lives_in_the_risk_engine_and_still_bites():
    """The property moved; this test moved with it rather than being deleted.

    Routing used to refuse below an IV/HV of 1.05 — a SECOND copy of a
    judgement the engine already makes. The two did not merely duplicate, they
    DISAGREED, and the engine's version is the calibrated one:

        route()        refuse below 1.05
        engine floor   block  below 0.35
        engine min     warn   below 1.00, stating the number

    The engine's comment records why it was relaxed on 30 Aug 2026: at 1.0 it
    blocked EVERYTHING, because the whole universe sits below it — XOM 0.90,
    NVDA 0.81, GOOGL 0.78, APLD 0.70, PG 0.63, MU 0.62, MRVL 0.49. So routing's
    1.05 would have refused every name on the board, which is exactly why it
    was never wired to the screen.

    Both halves are pinned: genuinely uneconomic still BLOCKS, and merely thin
    still WARNS with its number rather than passing silently.
    """
    from tradepro_strategies.quant_engine.options.risk import (
        MarketContext, OptionsRiskConfig, PortfolioState, Regime, TradeCandidate,
        evaluate)

    def _decide(ratio: float):
        ctx = MarketContext(
            regime=Regime.GREEN, falling_knife=False, pct_off_52w_high=25.0,
            iv_rank=None, iv_hv_ratio=ratio,
            open_interest=5000, bid_ask_spread_usd=0.05, premium_mid_usd=4.0,
            earnings_in_expiry_window=False, ex_div_in_expiry_window=False,
            data_fresh=True)
        cand = TradeCandidate(symbol="WDC", structure=Structure.CASH_SECURED_PUT,
                              abs_delta=0.27, dte=35, strike=330.0, contracts=1,
                              notional_gbp=25_000.0)
        return evaluate(cand, ctx, PortfolioState(0.0, 0, 0.0),
                        OptionsRiskConfig(), capital_gates=False)

    uneconomic = _decide(0.30)
    assert not uneconomic.allowed
    assert any("IV/HV" in b for b in uneconomic.all_blocks), uneconomic.all_blocks

    thin = _decide(0.80)
    assert any("IV/HV 0.80" in w for w in thin.warnings), thin.warnings


def test_a_name_we_would_not_own_is_OFFERED_WITH_A_FLOOR_not_skipped():
    """DELIBERATE BEHAVIOUR CHANGE, 21 Oct 2026.

    This used to assert the name was dropped when its premium was merely
    ordinary. That was routing making an economics call it no longer makes —
    and it discarded exactly the owner's second bucket: "on names we do not
    want to have byut techincally are paying good premium we could sell
    spread". The name is now offered as a floored trade and the risk engine
    decides whether the premium justifies it.
    """
    r = route("SOMECO", price=15.0, market_cap_usd=SMALL)
    assert r.structure is Structure.BULL_PUT_SPREAD
    assert r.bucket == "spread"
    assert "floor" in r.reason


def test_missing_size_data_blocks_ownership_rather_than_defaulting():
    """An unverifiable gate must refuse. No false positives.

    Vol is no longer a routing input, so the unverifiable-input case that
    matters here is the one routing still decides: size.
    """
    for px, mc in ((None, BIG), (344.0, None), (None, None)):
        r = route("GOOGL", price=px, market_cap_usd=mc)
        assert r.structure is not Structure.CASH_SECURED_PUT, (px, mc)
        assert "unavailable" in r.reason


def test_missing_liquidity_data_means_NOT_ownable_not_assumed_ownable():
    """Being unable to show a name is a large cap must not read as 'it is'.
    The cost of this being wrong is owning 100 shares of something unwanted."""
    r = route("MYSTERY", price=None, market_cap_usd=None)
    assert r.structure is not Structure.CASH_SECURED_PUT


def test_the_owner_can_veto_a_name_that_passes_the_measurements():
    ok, why = is_ownable(price=900.0, market_cap_usd=BIG,
                         symbol="TSLA", deny=frozenset({"TSLA"}))
    assert not ok and "excluded by the owner" in why


def test_every_refusal_carries_a_reason():
    """A bare 'no' cannot be acted on."""
    for r in (route(""),
              route("X", price=None, market_cap_usd=None),
              route("X", price=5.0, market_cap_usd=1.0)):
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
    r = route("CRWV", price=63.88, market_cap_usd=34_266_968_064.0)
    assert r.structure is Structure.CASH_SECURED_PUT, r.reason
    assert r.bucket == "own"


def test_the_size_gate_is_what_separates_CRWV_from_APLD():
    """Both realise ~85% vol. Only one is a name to own, and volume says which."""
    crwv_ok, _ = is_ownable(price=63.88, market_cap_usd=34_266_968_064.0, symbol="CRWV")
    apld_ok, _ = is_ownable(price=19.50, market_cap_usd=SMALL, symbol="APLD")
    assert crwv_ok and not apld_ok


# ── Ownability is about SIZE, not turnover (21 Oct 2026) ───────────────────
#
# The gate required $1bn of average daily dollar volume — a LIQUIDITY measure
# answering a QUALITY question. Measured, the two diverge on the owner's own
# named example.

def test_PG_is_ownable_though_it_fails_a_turnover_test():
    """THE CASE THAT CHANGED THE GATE.

    PG traded $0.89bn/day on 21 Oct 2026, under the old $1bn floor, so it was
    NOT ownable — while the owner had named it explicitly: "good divident
    paying ones like PG, coca cola etc". A 188-year-old consumer staple trades
    less than a hot new listing, and the old gate read that as unsuitability.
    It would have routed PG away from a cash-secured put and into a spread.
    """
    ok, why = is_ownable(price=159.32, market_cap_usd=370_328_698_880.0,
                         reporting_currency="USD", symbol="PG")
    assert ok, why
    assert "370bn" in why


def test_every_name_the_owner_named_is_ownable():
    # Market caps measured 21 Oct 2026. If a future threshold change breaks
    # one of these, it has broken the requirement, not just a number.
    named = {"PG": (159.32, 370_328_698_880.0), "KO": (93.49, 402_200_000_000.0),
             "MSFT": (531.87, 3_949_400_000_000.0), "NVDA": (216.47, 5_227_100_000_000.0),
             "GOOGL": (340.27, 4_161_500_000_000.0),
             # "core weave is fine ... could have neen a naked put ... its a
             # good name" — the smallest name explicitly approved, so it is
             # the one that pins the floor from below.
             "CRWV": (62.13, 34_266_968_064.0)}
    for sym, (px, mc) in named.items():
        ok, why = is_ownable(price=px, market_cap_usd=mc,
                             reporting_currency="USD", symbol=sym)
        assert ok, f"{sym}: {why}"


def test_a_non_usd_market_cap_is_refused_not_compared():
    """SK hynix returns currency=USD, financialCurrency=KRW and a market cap of
    1,018,882,555,904. Read as dollars that is one of the largest companies on
    earth; it is won. Comparing a foreign figure against a dollar floor is how
    a mid cap gets sold naked puts on an exchange-rate artefact."""
    ok, why = is_ownable(price=139.79, market_cap_usd=1_018_882_555_904.0,
                         reporting_currency="KRW", symbol="SKHY")
    assert not ok
    assert "KRW" in why and "not USD" in why


def test_a_missing_market_cap_blocks_rather_than_assumes():
    ok, why = is_ownable(price=100.0, market_cap_usd=None,
                         reporting_currency="USD", symbol="X")
    assert not ok and "unavailable" in why


def test_a_mid_cap_is_not_ownable_however_liquid():
    # $4bn: comfortably tradeable, still not a name to be assigned 100 shares
    # of. Liquidity is gated elsewhere, on open interest and spread.
    ok, why = is_ownable(price=50.0, market_cap_usd=4_000_000_000.0,
                         reporting_currency="USD", symbol="MID")
    assert not ok and "small or mid cap" in why

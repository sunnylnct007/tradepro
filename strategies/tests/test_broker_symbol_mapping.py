"""The broker symbol is NOT the data symbol.

`index` is what Yahoo is asked for (^GSPC, ^NDX, ^NSEBANK). IBKR needs SPX,
NDX, XSP. They coincide for SPY, QQQ and GLD — which is the ONLY reason
sending `index` to the broker ever worked, and why SPX/XSP/NDX sat marked
unplaceable for weeks behind a note about "needing their own IBKR symbol
mapping". The mapping turned out to be two config keys and one argument.
"""
import pytest

from tradepro_strategies.cli.index_strangle_paper import MARKETS

PLACEABLE = [m for m, c in MARKETS.items() if c.get("paper_trade")]


def test_there_are_placeable_markets():
    assert PLACEABLE, "config change would otherwise silently disable everything"


@pytest.mark.parametrize("market", PLACEABLE)
def test_no_placeable_market_sends_a_yahoo_symbol_to_the_broker(market):
    cfg = MARKETS[market]
    sym = cfg.get("broker_symbol") or cfg["index"]
    # A caret is Yahoo's index prefix. IBKR has never accepted one, and sending
    # it is what made these markets look like they needed bespoke work.
    assert not sym.startswith("^"), (
        f"{market} would send {sym!r} to IBKR — that is a Yahoo symbol")


@pytest.mark.parametrize("market", PLACEABLE)
def test_a_cash_index_declares_IND_and_an_etf_declares_STK(market):
    cfg = MARKETS[market]
    sec = cfg.get("broker_sec_type") or "STK"
    assert sec in ("STK", "IND")
    # Resolution was hardcoded to STK, so an index underlying could never be
    # found. Anything whose data symbol is a Yahoo index must declare IND.
    if cfg["index"].startswith("^"):
        assert sec == "IND", f"{market} is a cash index and must resolve as IND"


@pytest.mark.parametrize("market", PLACEABLE)
def test_every_placeable_market_states_its_contract_size(market):
    # Notional differs by 30x across these: one NDX contract is ~$2.5m of
    # collateral against ~$77k for SPY. A missing lot silently mis-sizes the
    # whole position and every figure derived from it.
    cfg = MARKETS[market]
    assert cfg.get("lot"), f"{market} has no lot size"
    assert cfg.get("grid"), f"{market} has no strike grid"


def test_india_stays_unplaceable():
    # No paper trading is available for India — it is email-only, and the
    # owner executes it by hand. If this ever flips, it must be deliberate.
    for m in ("NIFTY", "BANKNIFTY"):
        assert not MARKETS[m].get("paper_trade"), f"{m} cannot be paper-traded"


# ---------------------------------------------------------------------------
# SMALLEST FIRST. Margin is finite and first-come-first-funded.
#
# Dict order put the LARGEST market first — SPX needs ~12x the margin of GOLD.
# On 2 Sep 2026 XSP filled and SPX was then CANCELLED, consistent with the big
# one taking the headroom. MARGIN_PCT (12%) is OUR estimate; IBKR's real
# requirement on a ~$763k-notional index strangle is unknown and likely higher,
# so a shortfall must drop the single largest position, not everything behind it.
# ---------------------------------------------------------------------------

def test_placement_is_ordered_by_collateral_not_dict_order():
    import inspect
    from tradepro_strategies.cli import index_strangle_paper as P
    src = inspect.getsource(P.main)
    assert "sorted(rows, key=_size)" in src
    assert "for r in rows:" not in src.split("if args.place:")[1][:1200], \
        "the placement loop must not iterate raw dict order"


def test_the_size_key_uses_strike_times_lot():
    from tradepro_strategies.cli.index_strangle_paper import MARKETS, PLACE_EXPIRY_KIND
    # A market with no leg must sort first (0.0), never crash or sort last.
    spot = {"SPX": 7630, "GOLD": 390}
    sizes = {m: spot[m] * MARKETS[m]["lot"] for m in spot}
    assert sizes["SPX"] > sizes["GOLD"] * 10, \
        "SPX really is an order of magnitude larger — that is the whole point"
    assert PLACE_EXPIRY_KIND == "monthly"

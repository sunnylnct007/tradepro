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

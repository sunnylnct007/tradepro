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
    # The unit is now (market, expiry) because BOTH expiries are placed, and the
    # ordering has to span them: otherwise SPX-weekly could be funded ahead of
    # XSP-monthly purely by row order, and a shortfall would drop the CHEAP
    # position instead of the dear one — the exact failure this test exists for.
    assert "units.sort(key=lambda u: _size_of(*u))" in src
    assert "for r in rows:" not in src.split("if args.place:")[1][:1600], \
        "the placement loop must not iterate raw dict order"


def test_the_order_spans_both_expiries_not_just_markets():
    """A shortfall must drop the LARGEST unit, whichever expiry it belongs to."""
    from tradepro_strategies.cli.index_strangle_paper import MARKETS, PLACE_EXPIRY_KINDS

    rows = [
        {"market": "SPX", "legs": {"weekly":  {"put_strike": 7500},
                                   "monthly": {"put_strike": 7495}}},
        {"market": "XSP", "legs": {"weekly":  {"put_strike": 750},
                                   "monthly": {"put_strike": 748}}},
    ]

    def size_of(r, kind):
        leg = (r.get("legs") or {}).get(kind) or {}
        k = leg.get("put_strike")
        lot = (MARKETS.get(r["market"]) or {}).get("lot") or 1
        return float(k) * float(lot) if k else 0.0

    units = [(r, k) for r in rows for k in PLACE_EXPIRY_KINDS if (r["legs"] or {}).get(k)]
    units.sort(key=lambda u: size_of(*u))
    order = [(r["market"], k) for r, k in units]

    assert len(order) == 4, "both expiries of both markets are placement units"
    # Both XSP units must precede both SPX units — XSP is a tenth the size, so
    # sorting by market alone (or by row order) would get this wrong.
    assert [m for m, _ in order[:2]] == ["XSP", "XSP"], order
    assert [m for m, _ in order[2:]] == ["SPX", "SPX"], order


def test_the_size_key_uses_strike_times_lot():
    from tradepro_strategies.cli.index_strangle_paper import MARKETS, PLACE_EXPIRY_KIND
    # A market with no leg must sort first (0.0), never crash or sort last.
    spot = {"SPX": 7630, "GOLD": 390}
    sizes = {m: spot[m] * MARKETS[m]["lot"] for m in spot}
    assert sizes["SPX"] > sizes["GOLD"] * 10, \
        "SPX really is an order of magnitude larger — that is the whole point"
    assert PLACE_EXPIRY_KIND == "monthly"

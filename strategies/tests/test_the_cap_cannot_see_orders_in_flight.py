"""A concurrency cap must count orders it has already approved.

24 Sep 2026. With 13 positions held against --max-open-positions 15, FOUR
entries were approved in a single cycle:

    18:31:17  AMP   BUY 15  LMT 492.81  -> FILLED 483.24
    18:31:18  ABNB  BUY 49  LMT 151.82  -> FILLED 150.29
    18:31:20  ALL   BUY 32  LMT 229.04  -> FILLED 227.73
    18:31:22  PAYX  BUY 73  LMT 104.72  -> FILLED 102.67

Each projected 14 open positions, because positions only move on FILL and none
of the earlier three had filled when the next was checked. All four filled and
the sleeve ended the day holding 17 against a limit of 15.

A cap that can only see settled positions is not a concurrency limit — it is a
limit on how many positions you held when the cycle started. The strategy
already tracked in-flight symbols to avoid stacking two orders on one name;
that same fact now constrains the total.
"""
import pytest

from tradepro_strategies.paper.risk import (
    RiskContext, RiskLimits, _projected_open_count, check_order)
from tradepro_strategies.paper.strategy import (
    Order, OrderSide, OrderType, Position)


def _order(sym):
    return Order(strategy_id="s", symbol=sym, side=OrderSide.BUY, quantity=10,
                 type=OrderType.LIMIT, limit_price=1.0, tag="t")


def _held(n):
    return {f"H{i}": Position(strategy_id="s", symbol=f"H{i}", quantity=10)
            for i in range(n)}


def test_an_in_flight_order_occupies_a_slot():
    held = _held(13)
    # AMP already approved and unfilled; ABNB is the one being checked.
    assert _projected_open_count(held, _order("ABNB"), 10) == 14
    assert _projected_open_count(held, _order("ABNB"), 10,
                                 frozenset({"AMP"})) == 15


def test_the_fourth_entry_of_that_cycle_is_now_refused():
    # 13 held, AMP+ABNB+ALL already in flight, PAYX arrives. Before the fix all
    # four passed; the cap must now stop at 15.
    limits = RiskLimits(max_open_positions=15)
    ctx = RiskContext(strategy_capital_usd=150_000, mark_price=100.0,
                      current_positions=_held(13),
                      in_flight=frozenset({"AMP", "ABNB", "ALL"}))
    res = check_order(_order("PAYX"), limits, ctx)
    assert res.ok is False
    assert "max_open_positions" in res.code


def test_the_first_two_of_that_cycle_still_pass():
    # The fix must not block the entries there was genuinely room for.
    limits = RiskLimits(max_open_positions=15)
    for flight, sym in ((frozenset(), "AMP"), (frozenset({"AMP"}), "ABNB")):
        ctx = RiskContext(strategy_capital_usd=150_000, mark_price=100.0,
                          current_positions=_held(13), in_flight=flight)
        assert check_order(_order(sym), limits, ctx).ok is True, sym


def test_an_in_flight_order_on_a_name_already_HELD_adds_nothing():
    # Adding to an existing position does not consume a new slot.
    held = _held(13)
    assert _projected_open_count(held, _order("NEW"), 10,
                                 frozenset({"H0", "H1"})) == 14


def test_the_orders_own_symbol_is_never_double_counted():
    # The symbol under check is already accounted for by new_qty_signed.
    held = _held(13)
    assert _projected_open_count(held, _order("AMP"), 10,
                                 frozenset({"AMP"})) == 14


def test_the_default_keeps_every_existing_caller_working():
    # RiskContext is constructed in several places; none of them should have to
    # know about this to stay correct.
    held = _held(5)
    ctx = RiskContext(strategy_capital_usd=1000.0, mark_price=1.0,
                      current_positions=held)
    assert ctx.in_flight == frozenset()
    assert _projected_open_count(held, _order("X"), 10) == 6


def test_the_strategy_exposes_its_in_flight_set():
    # The engine reads this; a strategy that tracks in-flight symbols privately
    # and exposes nothing leaves the gate blind again.
    from tradepro_strategies.paper.strategy import Strategy
    assert hasattr(Strategy, "in_flight_symbols")

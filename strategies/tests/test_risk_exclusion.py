"""Symbol-exclusion risk-gate check: blocks opening/extending a blacklisted
symbol, still allows flattening a held position, and is off by default."""
from __future__ import annotations

from tradepro_strategies.paper.risk import check_order, RiskLimits, RiskContext
from tradepro_strategies.paper.strategy import Order, OrderSide, OrderType, Position

EXCL = RiskLimits(excluded_symbols=frozenset({"TSLA"}))


def _order(symbol: str, side: OrderSide, qty: int) -> Order:
    return Order(strategy_id="s1", symbol=symbol, side=side, quantity=qty,
                 type=OrderType.MARKET, tag="test")


def _ctx(positions=None, mark: float = 100.0) -> RiskContext:
    return RiskContext(strategy_capital_usd=1_000_000.0, mark_price=mark,
                       marks={}, current_positions=positions or {})


def test_excluded_symbol_blocks_new_buy():
    r = check_order(_order("TSLA", OrderSide.BUY, 10), EXCL, _ctx())
    assert not r.ok and r.code == "symbol_excluded"


def test_excluded_symbol_allows_flatten():
    # Hold 10 TSLA; selling 10 to flatten must still be allowed (reduce risk).
    pos = {"TSLA": Position(strategy_id="s1", symbol="TSLA", quantity=10, avg_entry_price=100.0)}
    r = check_order(_order("TSLA", OrderSide.SELL, 10), EXCL, _ctx(pos))
    assert r.ok


def test_non_excluded_symbol_passes():
    r = check_order(_order("AAPL", OrderSide.BUY, 10), EXCL, _ctx())
    assert r.ok


def test_no_exclusions_by_default():
    r = check_order(_order("TSLA", OrderSide.BUY, 10), RiskLimits(), _ctx())
    assert r.ok

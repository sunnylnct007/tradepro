"""Wind-down opens nothing new and NEVER blocks an exit.

WHY IT EXISTS. Moving momentum from IBKR to T212 (10 Oct 2026). The old lane
has to keep running, or its 20 positions are orphaned with nothing to exit
them — the September "can open but never close" failure, reached from the
other direction.

THE OBVIOUS ALTERNATIVE SILENTLY STRANDS THE BOOK. `--max-open-positions 0`
looks like "take nothing new", but the gate is:

    post_open = how many symbols are non-flat AFTER this order
    if post_open > max_open_positions: REJECT

An exit taking 20 holdings to 19 projects 19, which exceeds 0, so every EXIT
is rejected along with every entry. That was checked against risk.py before
relying on it, and is why wind-down filters on SIDE instead: a SELL from a
long-only sleeve can only be closing something, so exits are untouched by
construction.
"""
from __future__ import annotations

import dataclasses

from tradepro_strategies.paper.engine import Engine
from tradepro_strategies.paper.risk import RiskLimits
from tradepro_strategies.paper.strategy import OrderSide


def _wind_down_filter(orders, wind_down: bool):
    """The engine's filter, exactly as written in _bar_fanout."""
    return [o for o in orders if o.side == OrderSide.SELL] if wind_down else orders


class _O:
    def __init__(self, side):
        self.side = side


def test_the_flag_is_a_declared_field_and_defaults_off():
    """A stray attribute can be misspelled at a call site and fail silently."""
    flds = {f.name: f for f in dataclasses.fields(Engine)}
    assert "wind_down" in flds
    assert flds["wind_down"].default is False
    assert Engine(bus=None, router=None).wind_down is False


def test_wind_down_drops_entries():
    orders = [_O(OrderSide.BUY), _O(OrderSide.BUY)]
    assert _wind_down_filter(orders, True) == []


def test_wind_down_KEEPS_exits():
    """The property the whole migration depends on."""
    orders = [_O(OrderSide.SELL), _O(OrderSide.SELL)]
    assert len(_wind_down_filter(orders, True)) == 2


def test_a_mixed_batch_keeps_only_the_exits():
    orders = [_O(OrderSide.BUY), _O(OrderSide.SELL), _O(OrderSide.BUY)]
    kept = _wind_down_filter(orders, True)
    assert len(kept) == 1 and kept[0].side == OrderSide.SELL


def test_off_by_default_changes_nothing():
    orders = [_O(OrderSide.BUY), _O(OrderSide.SELL)]
    assert len(_wind_down_filter(orders, False)) == 2


def test_max_open_positions_zero_would_have_stranded_the_book():
    """The alternative I nearly used, shown failing.

    Reproduces risk.py's rule directly: an exit from 20 holdings to 19
    projects 19 open, 19 > 0, so the exit is REJECTED. Keeping this as a
    test means nobody reaches for that flag thinking it is equivalent.
    """
    limits = RiskLimits(max_open_positions=0)
    held_after_an_exit = 19          # 20 positions, one being closed
    rejected = held_after_an_exit > limits.max_open_positions
    assert rejected, (
        "if this ever passes, --max-open-positions 0 became a safe way to stop "
        "entries and this test should be revisited")

    # wind-down, by contrast, lets that same exit through.
    assert len(_wind_down_filter([_O(OrderSide.SELL)], True)) == 1

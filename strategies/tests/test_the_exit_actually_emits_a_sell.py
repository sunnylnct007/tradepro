"""The exit must actually emit a SELL — driven through on_bar, not re-derived.

WHY THIS EXISTS. On 24 Sep 2026 the swing sleeve had opened four new positions
and its exit path had not run since 22 Sep. Every branch of the broker guard had
fired in production EXCEPT the one that lets an exit through:

    _bpos is None   -> hold-unconfirmed   seen twice on 24 Sep (API timeouts)
    _have <= 0      -> already-flat       not seen
    _have < held    -> resize-to-broker   not seen
    confirmed       -> SELL               NEVER SEEN since the guard was written

And the only test covering the sizing re-implemented the arithmetic:

    sized = 0 if broker_qty <= 0 else int(min(local, broker_qty))
    assert sized == expect_sell_qty

That passes if the strategy's exit path is deleted. "Can open but not close" is
the most dangerous state this desk has been in — 0 of 47 exits reached the
broker through September — and it had no end-to-end test.

So this drives the REAL on_bar and asserts on the Order it returns.
"""
import datetime as dt

import pytest

import tradepro_strategies.paper.strategies.mean_reversion_swing as M
from tradepro_strategies.paper.strategy import Bar


def _series_at_target(n=260, base=100.0):
    """A held position whose last close has reached the 20-day mean.

    A flat series sits exactly AT its own mean, so close >= target fires the
    target branch on the first bar — the ordinary profitable exit.
    """
    return [base] * n


def _drive(closes, held, broker_qty, fill_price, entry_days_ago=3,
           symbol="SNOW"):
    """One bar through the real on_bar, with the broker read stubbed."""
    s = M.MeanReversionSwingStrategy(strategy_id="t")
    s.params = {"capital": 100_000, "position_pct": 0.05}
    dates = [(dt.date(2024, 1, 1) + dt.timedelta(days=i)).isoformat()
             for i in range(len(closes))]

    def _stub_history(sym, bar):
        s._dates = dates
        return closes, None

    s._history = _stub_history
    # Held, and OURS: _fill_price / _entry_bar are what _seed_from_oms would
    # have produced, so the position is not treated as inherited.
    s.seed_positions({symbol: held}, {symbol: fill_price})
    s._fill_price[symbol] = fill_price
    s._entry_bar[symbol] = dates[-1 - entry_days_ago]
    # The broker's answer — the thing the guard refuses to proceed without.
    s._broker_positions = lambda: (None if broker_qty is None
                                   else {symbol: float(broker_qty)})
    bar = Bar(symbol=symbol, timestamp=dt.datetime(2024, 12, 1, tzinfo=dt.UTC),
              open=closes[-1], high=closes[-1], low=closes[-1],
              close=closes[-1], volume=1_000_000, timeframe_seconds=86_400)
    return [o for o in (s.on_bar(bar) or []) if o.side.value == "SELL"]


def test_a_confirmed_position_at_target_DOES_emit_a_sell():
    # The branch that had never run in production.
    sells = _drive(_series_at_target(), held=61, broker_qty=61.0,
                   fill_price=95.0)
    assert len(sells) == 1, "the exit path emitted nothing"
    assert sells[0].quantity == 61


def test_the_sell_is_sized_to_the_BROKER_not_to_local_state():
    # Partly closed elsewhere: local says 61, the broker holds 30. Through the
    # real code, not a re-derivation of min().
    sells = _drive(_series_at_target(), held=61, broker_qty=30.0,
                   fill_price=95.0)
    assert len(sells) == 1
    assert sells[0].quantity == 30


def test_a_position_the_broker_no_longer_holds_emits_NOTHING():
    # Selling here would open a short — the 22 Sep LRCX incident.
    assert _drive(_series_at_target(), held=61, broker_qty=0.0,
                  fill_price=95.0) == []


def test_a_short_at_the_broker_emits_NOTHING():
    assert _drive(_series_at_target(), held=61, broker_qty=-671.0,
                  fill_price=95.0) == []


def test_an_UNREADABLE_broker_emits_nothing_rather_than_guessing():
    # A late exit costs one bar; a wrong one is unbounded. Seen live twice on
    # 24 Sep when the positions endpoint timed out.
    assert _drive(_series_at_target(), held=61, broker_qty=None,
                  fill_price=95.0) == []


def test_a_stop_also_reaches_the_broker():
    # The other exit reason. Fill at 200 against a flat 100 series is far below
    # the -8% stop, so the stop branch fires rather than the target.
    sells = _drive(_series_at_target(), held=61, broker_qty=61.0,
                   fill_price=200.0)
    assert len(sells) == 1 and sells[0].quantity == 61


def test_an_INHERITED_position_is_still_left_alone():
    # Same series and a confirmed broker position, but no fill of ours: this is
    # DIS/ABBV/COP, and it must not be sold however the signal reads.
    s = M.MeanReversionSwingStrategy(strategy_id="t")
    s.params = {"capital": 100_000, "position_pct": 0.05}
    closes = _series_at_target()
    dates = [(dt.date(2024, 1, 1) + dt.timedelta(days=i)).isoformat()
             for i in range(len(closes))]
    s._history = lambda sym, bar: (setattr(s, "_dates", dates) or (closes, None))
    s.seed_positions({"DIS": 14}, {"DIS": 95.0})
    s._broker_positions = lambda: {"DIS": 14.0}
    bar = Bar(symbol="DIS", timestamp=dt.datetime(2024, 12, 1, tzinfo=dt.UTC),
              open=closes[-1], high=closes[-1], low=closes[-1],
              close=closes[-1], volume=1_000_000, timeframe_seconds=86_400)
    assert [o for o in (s.on_bar(bar) or []) if o.side.value == "SELL"] == []

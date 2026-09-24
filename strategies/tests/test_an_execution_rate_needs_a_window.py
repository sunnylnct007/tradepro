"""An execution rate computed over all history can never go green again.

24 Sep 2026. `check_execution` read /api/oms/orders?limit=500 with no time
bound and divided over the lot, reporting "190 orders · 76 reached the broker"
— 40% — and WARNing. Split by day, the same data reads:

    19 Sep   34 orders    0 reached ( 0%)     <- the outage, since fixed
    21 Sep   45 orders   35 reached (78%)
    22 Sep   48 orders   26 reached (54%)
    23 Sep    6 orders    3 reached (50%)

The 40% was mostly a fault that had ALREADY been fixed, averaged in forever. A
warning that cannot clear however well the desk behaves is one a reader learns
to skip — the cry-wolf failure this file exists to avoid.

Two further misreadings in the same check:

  * `ichimoku_equity_ibkr` WARNed at 19% off orders whose most recent was
    20 Aug, five weeks dead. Not failing — not trading.
  * `brokerOrderId is null` was treated as "dropped before the broker saw it",
    but an order the BROKER rejected also has no id. On 22 Sep twenty-two
    orders carried IBKR's own words ("Your account has a minimum of 15 orders
    working..."). Those reached the broker and were turned away. Counting them
    as dropped pointed the reader at the router, which was not the fault.
"""
import datetime as dt

import pytest

from tradepro_strategies.cli import desk_check as D


def _order(days_ago, **kw):
    base = {"strategyId": "mean_reversion_swing_ibkr", "side": "BUY",
            "state": "FILLED", "brokerOrderId": "ib-1",
            "createdAtUtc": (dt.datetime.now(dt.UTC)
                             - dt.timedelta(days=days_ago)).isoformat()}
    base.update(kw)
    return base


@pytest.fixture
def oms(monkeypatch):
    def _install(rows):
        monkeypatch.setattr(D, "_get", lambda *a, **k: {"orders": rows})
    return _install


def _lane(checks, sid):
    return next(c for c in checks if c.lane.endswith(sid))


def test_a_fault_outside_the_window_no_longer_counts(oms):
    # The 19 Sep outage: 34 orders, none reached. Older than the window, so it
    # must not drag a currently-healthy lane down.
    old = [_order(30, brokerOrderId=None, state="REJECTED") for _ in range(34)]
    now = [_order(1) for _ in range(10)]
    oms(old + now)
    c = _lane(D.check_execution("http://x", None), "mean_reversion_swing_ibkr")
    assert c.status == D.OK, c.detail
    assert "10 orders" in c.detail, c.detail
    assert "44" not in c.detail, "the out-of-window orders are still being counted"


def test_the_window_is_named_in_the_detail(oms):
    # A rate with no stated window is the defect itself; the reader must be able
    # to see what period it covers.
    oms([_order(1) for _ in range(5)])
    c = _lane(D.check_execution("http://x", None), "mean_reversion_swing_ibkr")
    assert f"last {D.EXEC_WINDOW_DAYS}d" in c.detail, c.detail


def test_a_DORMANT_strategy_is_not_a_failing_one(oms):
    # ichimoku_equity_ibkr: last order 35 days ago, 19% reach rate. Reporting
    # that as an execution failure is a false alarm about a strategy that has
    # nothing to execute.
    oms([_order(35, strategyId="ichimoku_equity_ibkr",
                brokerOrderId=None, state="REJECTED") for _ in range(47)])
    c = _lane(D.check_execution("http://x", None), "ichimoku_equity_ibkr")
    assert c.status == D.OK
    assert "DORMANT" in c.detail and "35 days ago" in c.detail, c.detail


def test_a_dormant_lane_does_not_claim_recent_health_either(oms):
    # The inverse false reading: a lane dead for a month showed a healthy
    # "69 orders · 68 reached" from old data, which reads as current.
    oms([_order(31, strategyId="ichimoku_fx_mr") for _ in range(69)])
    c = _lane(D.check_execution("http://x", None), "ichimoku_fx_mr")
    assert "no orders in the last" in c.detail
    assert "68 reached" not in c.detail


def test_a_BROKER_REFUSAL_counts_as_reaching_the_broker(oms):
    # IBKR's own words on 22 Sep. The order got there and was turned away.
    ibkr = ('"SELL 3 ASML NASDAQ.NMS"\nYour account has a minimum of 15 orders '
            'working on either the buy or sell side for this particular '
            'contract. An additional order on the same side for this contract '
            'will not be allowed until an existing order has been cancelled.')
    oms([_order(1, side="SELL", state="REJECTED", brokerOrderId=None,
                cancelledReason=ibkr) for _ in range(10)]
        + [_order(1, side="SELL", state="FILLED") for _ in range(10)])
    c = _lane(D.check_execution("http://x", None), "mean_reversion_swing_ibkr")
    assert "20 reached the broker" in c.detail, c.detail
    assert "10 of them REFUSED by it" in c.detail, c.detail


def test_OUR_OWN_failure_does_NOT_count_as_reaching_the_broker(oms):
    # A JSON error body and a short internal code are our side, not the
    # broker's. These are the ones that mean the order never left.
    for reason in ('{"error":"Bad Request: no bridge","statusCode":400}',
                   "expired_no_broker_ack", "superseded by newer order", ""):
        oms([_order(1, side="SELL", state="REJECTED", brokerOrderId=None,
                    cancelledReason=reason) for _ in range(10)])
        c = _lane(D.check_execution("http://x", None), "mean_reversion_swing_ibkr")
        assert "0 reached the broker" in c.detail, f"{reason!r} -> {c.detail}"
        assert c.status != D.OK, f"{reason!r} should not read healthy"

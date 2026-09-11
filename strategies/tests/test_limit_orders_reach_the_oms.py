"""A LIMIT order must reach the OMS, not die in the router.

THE FAILURE THIS LOCKS OUT, in full, because it cost two days of forward test:

The IBKR confirmed-order path reuses T212OrderRouter as its OMS transport
(broker_label_override="IBKR_PAPER"); the .NET side then places the real
broker order and has carried OrderType + LimitPrice through since the 3 Sep
SNOW fix. But the router opened `_handle_approval` by rejecting anything that
was not MARKET — a rule that belongs to T212's own HTTP API and nothing else.

Swing is the one lane that sends limits (its entry-chase cap, itself the fix
for SNOW filling 367.44 on a 305.84 signal). So from 9 Sep 14:39 every swing
entry was ranked, built, and dropped before the OMS ever saw it: 208 orders
across SHOP, DASH, BLK, SBUX, ARES and SWK. Nothing looked wrong — the same
runs kept mirroring account positions and recording existing executions as
ledger fills, so the book stayed healthy while the strategy bought nothing.

Two properties are asserted, because fixing either one alone is not a fix:
  1. a LIMIT order on the OMS path is PUSHED, never dropped; and
  2. the pushed intent still carries LMT + the price — a limit that arrives
     downgraded to a market order is the SNOW bug wearing a different hat.
"""
from __future__ import annotations

import asyncio

import pytest

from tradepro_strategies.paper.brokers.t212 import T212OrderRouter
from tradepro_strategies.paper.strategy import Order, OrderSide, OrderType


def _limit_order() -> Order:
    """Shaped like the real thing: BLK as the swing lane actually built it on
    11 Sep — cap 1.5% over the 1095.83 signal close, with the risk and
    provenance fields the strategy always attaches."""
    return Order(
        strategy_id="mean_reversion_swing_ibkr",
        symbol="BLK",
        side=OrderSide.BUY,
        quantity=4,
        type=OrderType.LIMIT,
        tag="swing entry 2.25sigma ref=1095.8300 tgt=1150.63 stop=1008.16",
        limit_price=1112.27,
        risk_target_price=1150.63,
        risk_stop_price=1008.16,
        signal_ref_price=1095.83,
        signal_bar="2026-09-10",
    )


class _Approval:
    """Minimal stand-in for OrderApproved — the router reads .order."""

    def __init__(self, order: Order) -> None:
        self.order = order
        self.approved = True
        self.reason = ""


@pytest.mark.parametrize("placement_mode", ["auto", "manual"])
def test_limit_order_on_the_oms_path_is_pushed_not_dropped(monkeypatch, placement_mode):
    router = T212OrderRouter(
        mode="demo",
        allow_real_orders=False,
        placement_mode=placement_mode,
        broker_label_override="IBKR_PAPER",
    )
    pushed: list[Order] = []

    async def _capture(order, approval):          # noqa: ANN001 — test double
        pushed.append(order)

    monkeypatch.setattr(router, "_push_pending", _capture)
    asyncio.run(router._handle_approval(_Approval(_limit_order()), asyncio.Queue()))

    assert pushed, (
        f"a LIMIT order was DROPPED on the {placement_mode} OMS path — this is "
        "the 9 Sep regression: the strategy proposes, the router discards, and "
        "the account looks healthy because reconciliation still runs"
    )
    assert pushed[0].type is OrderType.LIMIT
    assert pushed[0].limit_price == pytest.approx(1112.27)


def test_the_pushed_intent_still_says_LMT_with_a_price(monkeypatch):
    """A limit that arrives as a market order is the SNOW bug, not a fix.

    The intent is built inline in _push_pending, so capture what is actually
    POSTed to /api/oms/orders — the wire is the contract, not an internal.
    """
    import httpx

    router = T212OrderRouter(
        mode="demo",
        allow_real_orders=False,
        placement_mode="auto",
        broker_label_override="IBKR_PAPER",
    )
    sent: list[dict] = []

    class _Resp:
        status_code = 200
        text = "{}"

        def json(self) -> dict:
            return {"id": "test-oms-id", "state": "Submitted"}

    class _Client:
        def __init__(self, *a, **k) -> None:
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a) -> bool:
            return False

        async def post(self, url, json=None, **kw):   # noqa: A002 — httpx kwarg
            sent.append(json or {})
            return _Resp()

        async def get(self, *a, **k):
            return _Resp()

    monkeypatch.setattr(httpx, "AsyncClient", _Client)
    asyncio.run(router._handle_approval(_Approval(_limit_order()), asyncio.Queue()))

    assert sent, "nothing was POSTed to the OMS for a LIMIT order"
    intent = sent[0]
    assert intent.get("OrderType") == "LMT", (
        "the OMS intent downgraded a LIMIT to a market order; IBKRClient "
        f"refuses a LMT with no price precisely so this cannot reach a broker "
        f"(got {intent.get('OrderType')!r})"
    )
    assert intent.get("LimitPrice") == pytest.approx(1112.27)
    assert intent.get("Broker") == "IBKR_PAPER"


def test_t212s_own_api_still_refuses_a_limit(monkeypatch):
    """The restriction is real — it just belongs to the DIRECT path only."""
    router = T212OrderRouter(
        mode="demo", allow_real_orders=True, placement_mode="direct",
    )
    router.api_key = "test-key"
    placed: list[Order] = []

    async def _place(order):                      # noqa: ANN001 — test double
        placed.append(order)
        return {"id": "should-not-happen"}

    monkeypatch.setattr(router, "_place_order", _place)
    monkeypatch.setattr(router, "_live_orders_enabled", lambda: True)
    asyncio.run(router._handle_approval(_Approval(_limit_order()), asyncio.Queue()))

    assert not placed, "T212's own API takes MARKET only; a LIMIT must not be sent to it"

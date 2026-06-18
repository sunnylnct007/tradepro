"""Phase-2 order routing through the central IBKR gateway: the file handoff
(inbox/outbox), dedup, and idempotency — all without a real IBKR connection.

The gateway is the ONE connection; desks submit intents and read fills, so the
desks never open their own (contending) connection. These tests pin the
contract of that handoff with fake `ib` / `ib_insync` objects."""
import asyncio
import json

from tradepro_strategies import ibkr_gateway as gw


# ── fakes ────────────────────────────────────────────────────────────────────
class _FakeOrder:
    def __init__(self, action, qty):
        self.action = action
        self.totalQuantity = qty
        self.orderId = 9001
        self.account = ""
        self.tif = "DAY"


class _FakeExec:
    def __init__(self, shares, price):
        self.shares = shares
        self.price = price


class _FakeFill:
    def __init__(self, shares, price):
        self.execution = _FakeExec(shares, price)


class _FakeStatus:
    def __init__(self, status):
        self.status = status


class _FakeTrade:
    def __init__(self, order, fills, status):
        self.order = order
        self.fills = fills
        self.orderStatus = _FakeStatus(status)
    def isDone(self):
        return True


class _FakeContract:
    def __init__(self, secType):
        self.secType = secType


class _FakeIbInsync:
    """Minimal ib_insync stand-in: Forex/Stock carry a secType; MarketOrder is
    the order object."""
    @staticmethod
    def Forex(pair):
        return _FakeContract("CASH")
    @staticmethod
    def Stock(sym, exch, ccy):
        return _FakeContract("STK")
    @staticmethod
    def MarketOrder(action, qty):
        return _FakeOrder(action, qty)


class _FakeIb:
    """Records placeOrder calls and returns an immediately-filled trade."""
    def __init__(self):
        self.placed = []
    def placeOrder(self, contract, order):
        self.placed.append((contract, order))
        return _FakeTrade(order, [_FakeFill(order.totalQuantity, 184.5)], "Filled")


def _tmp_dirs(tmp_path, monkeypatch):
    monkeypatch.setattr(gw, "ORDER_INBOX", tmp_path / "inbox")
    monkeypatch.setattr(gw, "ORDER_OUTBOX", tmp_path / "outbox")


def test_submit_then_read_roundtrip(tmp_path, monkeypatch):
    _tmp_dirs(tmp_path, monkeypatch)
    assert gw.read_order_result("abc") is None
    gw.submit_order_intent({"intent_id": "abc", "symbol": "EURUSD", "action": "BUY", "quantity": 1})
    # nothing in the outbox until the gateway places it
    assert gw.read_order_result("abc") is None
    assert (tmp_path / "inbox" / "abc.json").exists()


def test_drain_places_and_writes_fill(tmp_path, monkeypatch):
    _tmp_dirs(tmp_path, monkeypatch)
    gw.submit_order_intent({"intent_id": "i1", "symbol": "EURJPY", "action": "SELL",
                            "quantity": 81, "account": "DUP656969"})
    ib, ibis = _FakeIb(), _FakeIbInsync()
    placed = asyncio.run(gw.drain_order_inbox(ib, ibis, "DUP656969", set()))
    assert placed == 1
    assert len(ib.placed) == 1                      # the gateway placed it
    res = gw.read_order_result("i1")
    assert res["status"] == "filled"
    assert res["fill_qty"] == 81 and res["fill_price"] == 184.5
    assert not (tmp_path / "inbox" / "i1.json").exists()  # inbox consumed


def test_dedup_skips_existing_open_key(tmp_path, monkeypatch):
    _tmp_dirs(tmp_path, monkeypatch)
    gw.submit_order_intent({"intent_id": "i2", "symbol": "AAPL", "action": "BUY", "quantity": 1})
    ib, ibis = _FakeIb(), _FakeIbInsync()
    asyncio.run(gw.drain_order_inbox(ib, ibis, "", {("AAPL", "BUY")}))
    assert ib.placed == []                          # NOT placed — duplicate
    assert gw.read_order_result("i2")["status"] == "skipped_duplicate"


def test_idempotent_does_not_replace_when_result_exists(tmp_path, monkeypatch):
    _tmp_dirs(tmp_path, monkeypatch)
    # pre-seed an outbox result, then submit the same id again
    (tmp_path / "outbox").mkdir(parents=True)
    (tmp_path / "outbox" / "i3.json").write_text(json.dumps({"intent_id": "i3", "status": "filled"}))
    gw.submit_order_intent({"intent_id": "i3", "symbol": "MSFT", "action": "BUY", "quantity": 1})
    ib, ibis = _FakeIb(), _FakeIbInsync()
    asyncio.run(gw.drain_order_inbox(ib, ibis, "", set()))
    assert ib.placed == []                          # already done — not re-placed
    assert not (tmp_path / "inbox" / "i3.json").exists()

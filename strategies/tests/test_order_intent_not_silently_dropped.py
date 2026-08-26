"""An order that was never placed must not be reported as placed.

THE DEFECT, 2026-08-26. The desk submits an order by writing an intent file to
`~/.tradepro/cache/ibkr-orders/inbox` and polling the outbox ~12s for the
gateway's outcome. When no outcome appeared it logged, at INFO:

    "no result yet — gateway will place; fill reconciles from the book"

and returned. That sentence encodes an assumption — that a gateway exists and
will get to it — which nothing verified. The assumption had been false since
2026-07-06 (nothing listening on port 7500; the daemon sat in a reconnect loop
logging 50,331 refusals) and became permanently false on 2026-08-26 when the
daemon was retired in favour of the Web API.

Why it mattered more than "one wrong log line": the Swing forward test runs on
DUP656969, and `_live_orders_enabled` returns True unconditionally for any DU
paper account. So the test was ARMED. Its first entry signal would have written
an intent into a dead inbox, logged the reassuring line, and moved on — and a
12-week forward test whose entire purpose is measuring real fills would have
recorded zero, discovered at week 12.

The signal that separates the two cases is exact rather than heuristic:
`_drain_orders` unlinks an intent the instant it claims one, on every path
including rejection. So a file still present after the poll was claimed by
nobody.
"""
from __future__ import annotations

import pytest

from tradepro_strategies import ibkr_gateway as gw


@pytest.fixture()
def order_dir(tmp_path, monkeypatch):
    inbox, outbox = tmp_path / "inbox", tmp_path / "outbox"
    inbox.mkdir()
    outbox.mkdir()
    monkeypatch.setattr(gw, "ORDER_INBOX", inbox)
    monkeypatch.setattr(gw, "ORDER_OUTBOX", outbox)
    return inbox, outbox


def test_an_unclaimed_intent_is_reported_as_undrained(order_dir):
    """THE regression: nothing is running, so the file is still there."""
    gw.submit_order_intent({"intent_id": "abc", "symbol": "SPY"})
    assert gw.intent_undrained("abc") is True


def test_a_claimed_intent_is_not_reported_as_undrained(order_dir):
    """The benign case must stay benign — a gateway picked it up and the
    result is merely still in flight. Flagging this would cry wolf on every
    order that fills a beat after the poll window."""
    gw.submit_order_intent({"intent_id": "abc", "symbol": "SPY"})
    (order_dir[0] / "abc.json").unlink()          # what _drain_orders does
    assert gw.intent_undrained("abc") is False


def test_an_intent_that_was_never_submitted_is_not_undrained(order_dir):
    assert gw.intent_undrained("never-existed") is False


def test_the_two_cases_are_actually_distinguishable(order_dir):
    """The point of the fix. Before it, both of these produced identical
    desk behaviour — no outbox result, one reassuring INFO line."""
    gw.submit_order_intent({"intent_id": "dropped", "symbol": "SPY"})
    gw.submit_order_intent({"intent_id": "inflight", "symbol": "QQQ"})
    (order_dir[0] / "inflight.json").unlink()

    assert gw.read_order_result("dropped") is None
    assert gw.read_order_result("inflight") is None      # indistinguishable here
    assert gw.intent_undrained("dropped") is True        # ...but not here
    assert gw.intent_undrained("inflight") is False


def test_submit_leaves_no_partial_file_for_the_check_to_misread(order_dir):
    """`submit_order_intent` writes temp+rename. A `.tmp` must never be
    mistaken for a live intent, or every order would look dropped."""
    gw.submit_order_intent({"intent_id": "abc", "symbol": "SPY"})
    inbox = order_dir[0]
    assert [p.name for p in inbox.iterdir()] == ["abc.json"]
    assert not list(inbox.glob(".*.tmp"))

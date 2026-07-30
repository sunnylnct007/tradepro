"""record_broker_fills — audit-only fill recording for the ack-less IBKR clone.

Proves it restores fills_count + fills_log (chart markers / "did it execute")
WITHOUT mutating the broker-seeded position or realised P&L (no double-count),
and de-dupes by order_id.
"""
import datetime as d

from tradepro_strategies.paper.ledger import Ledger
from tradepro_strategies.paper.strategy import Fill, OrderSide


def _fill(oid, sym, side, qty, px):
    return Fill(order_id=oid, strategy_id="s", symbol=sym, side=side,
                quantity=qty, fill_price=px,
                fill_time=d.datetime(2026, 7, 30, 14, 0, tzinfo=d.timezone.utc),
                commission=0.0)


def test_records_fills_without_touching_position_or_pnl():
    led = Ledger()
    led.seed_positions("s", {"KO": 18}, {"KO": 88.0})     # broker-truth position
    n = led.record_broker_fills("s", [_fill("exec1", "KO", OrderSide.BUY, 18, 88.0)])
    book = led.books["s"]
    assert n == 1 and book.fills_count == 1 and len(book.fills_log) == 1
    assert book.positions["KO"].quantity == 18            # NOT 36 — no double-count
    assert book.realised_pnl == 0.0                       # realised untouched


def test_dedup_by_order_id():
    led = Ledger()
    f = _fill("exec1", "KO", OrderSide.BUY, 18, 88.0)
    assert led.record_broker_fills("s", [f]) == 1
    assert led.record_broker_fills("s", [f]) == 0         # same exec → not stacked
    assert led.books["s"].fills_count == 1


def test_snapshot_surfaces_recorded_fills():
    led = Ledger()
    led.record_broker_fills("s", [
        _fill("e1", "KO", OrderSide.BUY, 18, 88.0),
        _fill("e2", "KO", OrderSide.SELL, 18, 89.0),
    ])
    snap = led.to_snapshot(include_fills=10)
    st = next(s for s in snap["strategies"] if s["strategy_id"] == "s")
    assert st["fills_count"] == 2 and len(st["recent_fills"]) == 2
    assert {f["side"] for f in st["recent_fills"]} == {"BUY", "SELL"}

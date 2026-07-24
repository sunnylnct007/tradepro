"""Live-mark overlay — the paper snapshot's displayed unrealised P&L must equal
broker truth, not a stale daily close (the ANET +$4.92-vs-real-$13.32 bug).

Guards feedback_no_false_positives: never show a confident P&L we can't stand
behind — a missing cost basis or a missing live mark is flagged, not fabricated.
"""
import logging
from unittest.mock import patch

from tradepro_strategies.cli import paper_session as ps

LOG = logging.getLogger("test")


def _snap(positions, realised=0.0):
    return {"strategies": [{
        "strategy_id": "ichimoku_equity",
        "realised_pnl": realised,
        "unrealised_pnl": 0.0,
        "equity": realised,
        "positions": positions,
    }]}


def test_live_mark_replaces_stale_close():
    # Ledger held a stale daily close (78.246) → would show +$4.92 on a 20-share
    # ANET. Broker's live mark is 77.50 → true since-entry P&L is -$10.00.
    snap = _snap([{"symbol": "ANET", "quantity": 20, "avg_entry_price": 78.00,
                   "last_mark": 78.246, "unrealised_pnl": 4.92}])
    with patch.object(ps, "_fetch_broker_held_marks", return_value={"ANET": 77.50}):
        ps._overlay_live_marks_and_pnl(snap, "t212", LOG)
    pos = snap["strategies"][0]["positions"][0]
    assert pos["last_mark"] == 77.50
    assert pos["since_entry_pnl"] == -10.00
    assert pos["unrealised_pnl"] == -10.00          # display P&L now == broker truth
    assert pos["mark_source"] == "broker_live"
    assert pos["mark_is_stale"] is False
    book = snap["strategies"][0]
    assert book["unrealised_pnl"] == -10.00         # rolled up from corrected pos
    assert book["equity"] == -10.00
    assert book["marks_source"] == "broker_live"


def test_missing_cost_basis_is_unknown_not_zero():
    # avg_entry_price=0 → we have no trustworthy basis. since_entry must be None
    # (unknown), NEVER a fabricated number, and the position is flagged.
    snap = _snap([{"symbol": "X", "quantity": 5, "avg_entry_price": 0.0,
                   "last_mark": 10.0, "unrealised_pnl": 50.0}])
    with patch.object(ps, "_fetch_broker_held_marks", return_value={"X": 11.0}):
        ps._overlay_live_marks_and_pnl(snap, "t212", LOG)
    pos = snap["strategies"][0]["positions"][0]
    assert pos["since_entry_pnl"] is None
    assert pos["mark_is_stale"] is True
    book = snap["strategies"][0]
    assert book["unrealised_pnl_partial"] is True
    assert book["positions_without_cost_basis"] == 1


def test_no_broker_marks_flags_stale_never_fabricates_live():
    # Broker unreachable ({}). We keep the ledger mark but say plainly it is not
    # live — no invented "current" price.
    snap = _snap([{"symbol": "ANET", "quantity": 20, "avg_entry_price": 78.00,
                   "last_mark": 78.246, "unrealised_pnl": 4.92}])
    with patch.object(ps, "_fetch_broker_held_marks", return_value={}):
        ps._overlay_live_marks_and_pnl(snap, "t212", LOG)
    pos = snap["strategies"][0]["positions"][0]
    assert pos["mark_source"] == "ledger_stale"
    assert pos["mark_is_stale"] is True
    assert snap["strategies"][0]["marks_source"] == "ledger_only"


def test_since_entry_equals_current_minus_entry_times_qty():
    # The user's exact ask: LTD P&L = (today price - entry) * qty.
    snap = _snap([{"symbol": "MU", "quantity": 12, "avg_entry_price": 100.00,
                   "last_mark": 95.0, "unrealised_pnl": 0.0}])
    with patch.object(ps, "_fetch_broker_held_marks", return_value={"MU": 108.25}):
        ps._overlay_live_marks_and_pnl(snap, "t212", LOG)
    pos = snap["strategies"][0]["positions"][0]
    assert pos["since_entry_pnl"] == round((108.25 - 100.00) * 12, 2)   # +99.00

"""Signal replay — the owner's question, as a product.

"at least we know the signals that strategy generated. we should be able to
validate if we would have made money or not" (3 Oct 2026).

These tests drive the REAL exit rules (imported signal modules, not stubs)
through replay() with synthetic bars, so what is asserted is the actual
decision path the engine trades — not a re-implementation that can drift.
"""
from __future__ import annotations

import pytest

from tradepro_strategies import signal_replay as R
from tradepro_strategies.signals import mean_reversion as MR
from tradepro_strategies.signals import momentum_pullback as MOM


def _store(**symbols):
    """dict of symbol -> (dates, closes) as a load_bars callable."""
    def load(sym):
        return symbols.get(sym)
    return load


def _dates(n, start=1):
    return [f"2026-09-{d:02d}" for d in range(start, start + n)]


# ── the honesty rules ──────────────────────────────────────────────────────

def test_first_appearance_only():
    """A signal re-published daily is ONE trade, not three."""
    flat = [100.0] * 30
    boards = {
        "2026-09-10": {"signal_bar": "2026-09-10", "symbols": ["AAA"]},
        "2026-09-11": {"signal_bar": "2026-09-11", "symbols": ["AAA"]},
        "2026-09-12": {"signal_bar": "2026-09-12", "symbols": ["AAA"]},
    }
    out = R.replay(boards, _store(AAA=(_dates(30), flat)), MOM)
    assert out["summary"]["signals"] == 1
    assert len(out["closed"]) + len(out["open"]) == 1


def test_unmeasurable_signals_are_named_never_dropped():
    """'82 rows, 0 eligible' taught this: absence must be loud."""
    boards = {"2026-09-10": {"signal_bar": "2026-09-10",
                             "symbols": ["GONE", "AAA"]}}
    flat = [100.0] * 30
    out = R.replay(boards, _store(AAA=(_dates(30), flat)), MOM)
    assert out["summary"]["unmeasured"] == 1
    row = out["unmeasured"][0]
    assert row["symbol"] == "GONE"
    assert "golden store" in row["why"]


def test_a_signal_bar_missing_from_the_store_is_unmeasured_with_the_reason():
    boards = {"2026-09-10": {"signal_bar": "2026-08-01", "symbols": ["AAA"]}}
    out = R.replay(boards, _store(AAA=(_dates(30), [100.0] * 30)), MOM)
    assert out["summary"]["unmeasured"] == 1
    assert "2026-08-01" in out["unmeasured"][0]["why"]


# ── the real exit rules fire correctly ─────────────────────────────────────

def test_momentum_hard_stop_closes_the_trade():
    """Entry 100, close sinks to 91 — below the 8% stop — must close as a loss."""
    closes = [100.0] * 10 + [100.0, 97.0, 91.0] + [91.0] * 5
    dates = _dates(len(closes))
    boards = {"2026-09-11": {"signal_bar": dates[10], "symbols": ["AAA"]}}
    out = R.replay(boards, _store(AAA=(dates, closes)), MOM)
    assert len(out["closed"]) == 1
    t = out["closed"][0]
    assert t["ret_pct"] < -8.0
    assert t["pnl"] < 0
    assert out["summary"]["realised_pnl"] == t["pnl"]


def test_momentum_trail_banks_a_profit():
    """Entry 100, run to 130, give back >8% from the peak — banks a WIN."""
    closes = [100.0] * 10 + [100, 110, 120, 130, 118] + [118.0] * 3
    dates = _dates(len(closes))
    boards = {"2026-09-11": {"signal_bar": dates[10], "symbols": ["AAA"]}}
    out = R.replay(boards, _store(AAA=(dates, closes)), MOM)
    assert len(out["closed"]) == 1
    t = out["closed"][0]
    assert t["pnl"] > 0, f"trail from 130 to 118 should bank a gain, got {t}"
    assert t["ret_pct"] == pytest.approx(18.0, abs=0.1)


def test_swing_target_exit_at_the_mean():
    """A dip that reverts to its 20-day mean exits at 'target'."""
    # 25 settled bars at 100, a dip to 90 (the archived signal), then recovery
    # back above the rolling mean.
    closes = [100.0] * 25 + [90.0, 92.0, 96.0, 100.0, 101.0]
    dates = _dates(len(closes), start=1)
    boards = {"2026-09-26": {"signal_bar": dates[25], "symbols": ["DIP"]}}
    out = R.replay(boards, _store(DIP=(dates, closes)), MR)
    assert len(out["closed"]) == 1
    t = out["closed"][0]
    assert t["reason"] == "target"
    assert t["pnl"] > 0


def test_a_position_with_no_exit_stays_open_and_is_not_blended():
    """evaluated != closed. An open mark must never inflate realised P&L."""
    closes = [100.0] * 10 + [100.0, 101.0, 102.0]
    dates = _dates(len(closes))
    boards = {"2026-09-11": {"signal_bar": dates[10], "symbols": ["AAA"]}}
    out = R.replay(boards, _store(AAA=(dates, closes)), MOM)
    assert out["closed"] == []
    assert len(out["open"]) == 1
    assert out["summary"]["realised_pnl"] == 0
    assert out["summary"]["open_pnl"] == out["open"][0]["pnl"]


def test_summary_counts_reconcile_with_rows():
    closes_stop = [100.0] * 10 + [100.0, 90.0] + [90.0] * 4
    closes_open = [100.0] * 16
    dates = _dates(16)
    boards = {"2026-09-11": {"signal_bar": dates[10],
                             "symbols": ["LOSS", "HOLD", "GONE"]}}
    out = R.replay(boards, _store(LOSS=(dates, closes_stop),
                                  HOLD=(dates, closes_open)), MOM)
    s = out["summary"]
    assert s["signals"] == 3
    assert s["closed"] == len(out["closed"]) == 1
    assert s["open"] == len(out["open"]) == 1
    assert s["unmeasured"] == len(out["unmeasured"]) == 1


def test_unknown_strategy_is_refused_with_the_valid_list():
    with pytest.raises(ValueError, match="swing"):
        R.run("not_a_strategy", "http://api")

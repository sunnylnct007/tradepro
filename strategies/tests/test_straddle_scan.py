"""Straddle scanner pure core (SPEC Part B §2.3-2.4). Observational only —
these tests pin the metric arithmetic and the can't-verify-blocks rule."""
from __future__ import annotations

import datetime as dt

from tradepro_strategies.cli.straddle_scan import realized_print_moves, straddle_gates


def _dates(n, start="2024-01-02"):
    d0 = dt.date.fromisoformat(start)
    out, d = [], d0
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d)
        d += dt.timedelta(days=1)
    return out


def test_realized_move_is_close_to_close_around_the_print():
    dates = _dates(10)
    closes = [100, 100, 100, 100, 90, 100, 100, 100, 100, 100]
    # print on dates[3]: |close(T+1)/close(T-1)-1| = |90/100 - 1| = 10%
    import pytest
    moves = realized_print_moves(dates, closes, [dates[3]])
    assert moves == [pytest.approx(10.0)]


def test_print_on_non_session_uses_next_bar():
    dates = _dates(10)
    closes = [100] * 4 + [80] + [80] * 5
    saturday = dates[3] + dt.timedelta(days=(5 - dates[3].weekday()) % 7 or 7)
    moves = realized_print_moves(dates, closes, [saturday])
    assert len(moves) == 1


def test_prints_without_surrounding_bars_are_skipped():
    dates = _dates(5)
    closes = [100] * 5
    assert realized_print_moves(dates, closes, [dates[0], dates[-1]]) == []


def test_gates_candidate_requires_every_gate_decidable_and_passing():
    ok = dict(edge_ratio=1.3, n_prints=9, iv_pctile=30.0, iv_hv=0.9,
              worst_leg_spread_pct=5.0, min_leg_oi=800, cost_pct_of_nav=1.0)
    assert straddle_gates(**ok)["candidate"] is True
    # unknown IV percentile (store immature) BLOCKS candidacy — can't-verify ≠ pass
    assert straddle_gates(**{**ok, "iv_pctile": None})["candidate"] is False
    # edge below the 1.15 margin blocks
    assert straddle_gates(**{**ok, "edge_ratio": 1.05})["candidate"] is False
    # thin sample blocks
    assert straddle_gates(**{**ok, "n_prints": 6})["candidate"] is False
    # wheel-inverse check: HIGH iv percentile blocks (buy vol cheap)
    assert straddle_gates(**{**ok, "iv_pctile": 80.0})["candidate"] is False

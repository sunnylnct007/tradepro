"""Intraday setups board: facts about the session, never a signal; corrupt
opens named not scored; the module cannot place anything."""
from __future__ import annotations

import ast
import pathlib
from datetime import date

import numpy as np
import pandas as pd

from tradepro_strategies import intraday_setups as I


def _session(d: str, opens, closes, vols, highs=None, lows=None):
    idx = pd.date_range(f"{d} 09:30", periods=len(closes), freq="5min", tz="America/New_York")
    highs = highs or [max(o, c) for o, c in zip(opens, closes)]
    lows = lows or [min(o, c) for o, c in zip(opens, closes)]
    return date.fromisoformat(d), pd.DataFrame({"open": opens, "high": highs, "low": lows, "close": closes,
                                                 "volume": vols}, index=idx)


def _quiet(d, px=100.0, vol=1000):
    n = 8
    return _session(d, [px] * n, [px] * n, [vol] * n)


def test_a_stock_in_play_that_breaks_its_candle_is_reported_with_the_bar():
    ss = [_quiet(f"2026-10-{k:02d}") for k in range(1, 7)]
    # today: first candle 100 -> 101 on 3x volume (up), then a close above its high at 09:40
    ss.append(_session("2026-10-07", [100, 101, 101.5, 102], [101, 100.8, 102, 102.5], [3000, 1000, 1000, 1000]))
    qqq = _session("2026-10-07", [500] * 4, [500] * 4, [1] * 4)[1]
    r = I.setup_row("X", ss, qqq)
    assert r["in_play"] is True and r["opening_volume_x"] == 3.0
    assert r["rule7"]["side"] == "long" and r["rule7"]["level"] == 101 and r["rule7"]["triggered_at"] == "09:40"
    assert r["rule2"]["eligible"] is False and r["gap_pct"] == 0.0
    assert r["levels"]["prior_close"] == 100.0 and r["rs_vs_qqq_pct"] == 2.5


def test_a_split_sized_open_is_named_not_scored():
    ss = [_quiet(f"2026-10-{k:02d}", px=750.0) for k in range(1, 7)]
    ss.append(_session("2026-10-07", [80, 81], [81, 82], [50000, 1000]))
    r = I.setup_row("SNDK", ss, None)
    assert r["not_scored"].startswith("-89%") and r["in_play"] is None
    assert r["rule7"]["eligible"] is False and r["rule2"]["eligible"] is False


def test_fewer_than_two_sessions_gives_no_row():
    assert I.setup_row("X", [_quiet("2026-10-01")], None) is None


def test_the_module_has_no_broker_import():
    tree = ast.parse(pathlib.Path(I.__file__).read_text())
    mods = {a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names} | \
           {n.module or "" for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)}
    assert not any(("paper" in m or "broker" in m or "oms" in m or "requests" in m) for m in mods), mods

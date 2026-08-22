"""Garbage-bar alarm policy (owner, 15 Aug 2026: "too many bar errors ...
not even clear if it's today or yesterday").

A bar dropped from YESTERDAY means the feed is failing NOW — actionable.
A corrupt bar from 2020 that we re-drop on every run forever is noise, and
it was being announced at the same volume and urgency as the actionable one.
Only recent drops alarm; every message states the run date and the bar's age.
"""
from __future__ import annotations

import datetime as dt

import numpy as np
import pandas as pd

import tradepro_strategies.cache as cache


def _run(dates, closes, monkeypatch, tmp_path=None):
    # Isolate the once-per-bar alarm dedupe state from the real machine's —
    # otherwise a test's synthetic "TEST dropped yesterday" entry leaks into
    # (or is suppressed by) ~/.tradepro/state/garbage_bar_alarmed.json.
    import tempfile
    monkeypatch.setenv("TRADEPRO_STATE_DIR",
                       str(tmp_path) if tmp_path is not None else tempfile.mkdtemp())
    calls = []
    monkeypatch.setattr(cache, "_drop_garbage_bars", cache._drop_garbage_bars)
    import tradepro_strategies.run_log as rl
    monkeypatch.setattr(rl, "log_run",
                        lambda *a, **k: calls.append(k.get("error") or (a[3] if len(a) > 3 else "")))
    df = pd.DataFrame({"close": closes}, index=pd.to_datetime(dates))
    out = cache._drop_garbage_bars(df, symbol="TEST", provider="yahoo")
    return out, calls


def test_recent_nan_alarms_and_names_the_day(monkeypatch):
    y = dt.date.today() - dt.timedelta(days=1)
    out, calls = _run(["2026-01-05", "2026-01-06", y.isoformat()],
                      [100.0, 101.0, np.nan], monkeypatch)
    assert len(out) == 2, "the bad bar is still dropped"
    assert calls, "a recent drop must alarm"
    msg = calls[0]
    assert "yesterday" in msg
    assert dt.date.today().isoformat() in msg, "run date must be stated"
    assert "TEST" in msg


def test_historical_corruption_does_not_alarm(monkeypatch):
    # A 6-year-old spike bar between two normal ones — dropped, not alarmed.
    out, calls = _run(["2020-04-19", "2020-04-20", "2020-04-21"],
                      [100.0, 0.01, 100.0], monkeypatch)
    assert len(out) == 2, "historical garbage is still dropped"
    assert calls == [], "historical re-drops must not raise a run_log warn"


def test_mixed_alarms_once_and_counts_the_rest(monkeypatch):
    y = dt.date.today() - dt.timedelta(days=1)
    out, calls = _run(["2020-04-19", "2020-04-20", "2020-04-21", y.isoformat()],
                      [100.0, 0.01, 100.0, np.nan], monkeypatch)
    assert len(calls) == 1, "one alarm per symbol, not one per bad bar"
    assert "historical" in calls[0], "the suppressed historical count is still disclosed"

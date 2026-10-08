"""The digest must notice a lane that stopped ticking.

THE FAILURE IT EXISTS FOR, 7-8 Oct 2026. Both trading lanes sat inside a
single run for ~29 hours, then stalled again for 17. Zero orders were placed
across a full trading day while both boards ranked candidates. Every existing
signal said healthy:

    launchctl   exit=0
    the job     loaded
    desk-check  passed

A lane that is LOADED BUT NOT TICKING was invisible to everything we checked.
This is the check that sees it, so it gets tests that fire it rather than a
comment promising it works.

The timezone case is here deliberately: the first version compared a UTC
`now` against BST log lines and produced an age of MINUS 58 minutes, which
would have made the detector permanently silent. A monitor on this desk has
made that exact mistake before.
"""
from __future__ import annotations

import datetime as _dt

import pytest

from tradepro_strategies.cli import daily_digest as D


@pytest.fixture
def lane_log(tmp_path, monkeypatch):
    """Point the digest at a single synthetic lane log we control."""
    p = tmp_path / "lane.log"

    def _write(*seed_times: str, noise: int = 3):
        lines = []
        for t in seed_times:
            lines += ["some unrelated chatter"] * noise
            lines.append(f"{t},123 tradepro.cli INFO position seed: "
                         f"via IBKR WEB API — 35 position(s): AAPLx1")
        p.write_text("\n".join(lines) + "\n")
        monkeypatch.setattr(D, "LANES", {"swing": str(p)})
        return p

    return _write


def _line(now_local):
    out = D.lane_health(now_local)
    assert len(out) == 1
    return out[0]


def test_a_recent_seed_reads_ok(lane_log):
    lane_log("2026-10-08 22:08:14")
    line = _line(_dt.datetime(2026, 10, 8, 22, 11, 0))
    assert " ok " in line
    assert "3 min ago" in line
    assert "STALLED" not in line


def test_a_lane_that_stopped_ticking_is_STALLED(lane_log):
    """The 8 Oct case: last seed hours ago, process still 'loaded'."""
    lane_log("2026-10-08 00:42:45")
    line = _line(_dt.datetime(2026, 10, 8, 17, 57, 0))
    assert "STALLED" in line, (
        "a lane silent for 17 hours read as healthy — this is the exact "
        "failure the check exists to catch")


def test_the_boundary_is_two_missed_ticks_not_one(lane_log):
    """Lanes tick every 15 min; a single slow pass must not cry wolf."""
    lane_log("2026-10-08 12:00:00")
    assert " ok " in _line(_dt.datetime(2026, 10, 8, 12, 30, 0))      # 30m: fine
    assert "STALLED" in _line(_dt.datetime(2026, 10, 8, 12, 50, 0))   # 50m: not


def test_age_is_never_negative_the_timezone_bug(lane_log):
    """A UTC-vs-local mismatch produced '(-58 min ago)' and a silent detector."""
    lane_log("2026-10-08 22:08:14")
    line = _line(_dt.datetime(2026, 10, 8, 22, 11, 0))
    assert "(-" not in line, (
        "negative age: the comparison clock does not match the log's clock, "
        "so the staleness test can never trip")


def test_todays_passes_are_counted(lane_log):
    lane_log("2026-10-08 09:00:00", "2026-10-08 09:20:00", "2026-10-07 23:00:00")
    line = _line(_dt.datetime(2026, 10, 8, 9, 30, 0))
    assert "2 pass(es) today" in line, line


def test_a_missing_log_is_UNKNOWN_not_ok(monkeypatch, tmp_path):
    """Absence is not health. UNKNOWN reads as a problem, per house rule."""
    monkeypatch.setattr(D, "LANES", {"swing": str(tmp_path / "nope.log")})
    assert "UNKNOWN" in _line(_dt.datetime(2026, 10, 8, 12, 0, 0))


def test_a_log_with_no_seed_line_is_UNKNOWN(tmp_path, monkeypatch):
    """A lane that logged plenty but never seeded has not run a pass."""
    p = tmp_path / "lane.log"
    p.write_text("\n".join(["chatter with no seed"] * 50) + "\n")
    monkeypatch.setattr(D, "LANES", {"swing": str(p)})
    assert "UNKNOWN" in _line(_dt.datetime(2026, 10, 8, 12, 0, 0))


def test_subject_announces_a_stall(lane_log, monkeypatch):
    """The subject is the only part read on a phone."""
    lane_log("2026-10-08 00:42:45")
    monkeypatch.setattr(D, "orders_today", lambda *a, **k: ["  none"])
    monkeypatch.setattr(D, "replay_lines", lambda *a, **k: ["  (skipped)"])
    monkeypatch.setattr(D, "book_lines", lambda *a, **k: ["  (skipped)"])
    subject, _ = D.build("http://x", None,
                         _dt.datetime(2026, 10, 8, 17, 57, 0),
                         _dt.datetime(2026, 10, 8, 17, 57, 0))
    assert "LANES STALLED" in subject


def test_subject_is_quiet_when_healthy(lane_log, monkeypatch):
    lane_log("2026-10-08 17:50:00")
    monkeypatch.setattr(D, "orders_today", lambda *a, **k: ["  none"])
    monkeypatch.setattr(D, "replay_lines", lambda *a, **k: ["  (skipped)"])
    monkeypatch.setattr(D, "book_lines", lambda *a, **k: ["  (skipped)"])
    subject, _ = D.build("http://x", None,
                         _dt.datetime(2026, 10, 8, 17, 57, 0),
                         _dt.datetime(2026, 10, 8, 17, 57, 0))
    assert "lanes ok" in subject

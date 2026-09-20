"""A stale close must never become a candidate — measured, 20 Sep 2026.

The day the universe widened 244 → 956, one swing board mixed THREE vintages
as comparable rows: CVS/GM/VZ on Friday's close, eight names on Thursday's,
and PYPL on 31 AUGUST at -2.78σ — all labelled "BUY today", with the footer
saying "none were dropped". The suspect-series guard checks QUALITY; nothing
checked AGE. _pick_signal_index steps over a PARTIAL bar, but when a symbol's
history simply ENDS early, the index lands on the old close and the signal
computes anyway.

A signal on a stale close is not a smaller edge — it is a different, unpriced
trade. Missing-feed rule applies: explicit quarantine, never fail-open.
"""
import datetime as dt
from unittest.mock import patch

import tradepro_strategies.cli.swing_candidates as SC


class _Frame:
    """250 settled sessions ending at `end` — deep dip on the last bar so the
    entry rule WOULD fire if freshness allowed it."""
    def __init__(self, end: dt.date):
        n = 250
        days, d = [], end
        while len(days) < n:
            if d.weekday() < 5:
                days.append(d)
            d -= dt.timedelta(days=1)
        days.reverse()
        self.index = [f"{x} 04:00:00" for x in days]
        closes = [100.0 + 0.01 * i for i in range(n)]
        closes[-1] = 88.0        # far below the 20-day mean, above the 200-SMA
        self._d = {
            "close": closes,
            "high": [c * 1.01 for c in closes],
            "low": [c * 0.97 for c in closes],
            "volume": [1_000_000.0] * n,
        }
    def __getitem__(self, k): return _Col(self._d[k])
    def __contains__(self, k): return k in self._d
    @property
    def columns(self): return list(self._d)


class _Col:
    def __init__(self, v): self._v = v
    def tolist(self): return list(self._v)


def _run(last_bar: dt.date, settled: str):
    with patch.object(SC, "_load", lambda sym: _Frame(last_bar)), \
         patch.object(SC, "_last_completed_session", lambda: settled):
        return SC.scan(["TEST"])


def test_a_bar_from_the_settled_session_may_signal():
    """The gate must pass a settled-session bar through to EVALUATION — the
    row may then land in candidates, near-misses or priced-out on its merits,
    but never in stale_dropped."""
    fri = dt.date(2026, 9, 18)
    rows, _q, near, priced, stale = _run(fri, "2026-09-18")
    assert not stale, "a settled bar must not be called stale"
    evaluated = rows + near + priced
    assert any(r["symbol"] == "TEST" for r in evaluated), (
        "the symbol reached no surface at all — the gate swallowed a fresh bar")
    assert all(r.get("bar", "2026-09-18") == "2026-09-18" for r in evaluated)


def test_thursdays_close_is_refused_on_sunday():
    thu = dt.date(2026, 9, 17)
    rows, _q, _n, _p, stale = _run(thu, "2026-09-18")
    assert rows == [], "a stale close must never emit a candidate"
    assert stale and stale[0]["symbol"] == "TEST"
    assert stale[0]["last_bar"] == "2026-09-17"
    assert "REFUSING" in stale[0]["reason"]


def test_a_three_week_old_series_is_refused_the_same_way():
    """PYPL's actual state: last daily bar 31 Aug, screened at -2.78σ."""
    aug = dt.date(2026, 8, 31)
    rows, _q, _n, _p, stale = _run(aug, "2026-09-18")
    assert rows == [] and stale[0]["last_bar"] == "2026-08-31"


def test_the_artifact_publishes_the_drops_and_the_true_session():
    art = SC.build_artifact(
        [], universe="tradeable", evaluated=10,
        stale_dropped=[{"symbol": "PYPL", "last_bar": "2026-08-31",
                        "settled": "2026-09-18", "reason": "x"}])
    assert art["stale_dropped"][0]["symbol"] == "PYPL"
    assert art["signal_bar"] == SC._last_completed_session(), (
        "the board's session label must be THE session, not whatever bar the "
        "first surviving row happened to sit on")

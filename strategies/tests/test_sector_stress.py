"""sector_stress.py — the one shipped result of SELLOFF_PLAYBOOK_GATES_V1, named on rows."""
from __future__ import annotations

import importlib.util
from datetime import date, timedelta
from pathlib import Path

from tradepro_strategies import events as ev
from tradepro_strategies import sector_stress as ss

TODAY = date(2026, 11, 10)
FRESH_STORE = {"totalRows": 5000, "symbols": 900, "lastUploadUtc": "2099-01-01T00:00:00Z"}


def _closes(n, last_dd):
    """Flat at 100 then a last close `last_dd` below the high."""
    c = [100.0] * (n - 1) + [100.0 * (1 + last_dd)]
    d = [(TODAY - timedelta(days=n - i)).isoformat() for i in range(n)]
    return c, d


def test_drawdown_and_stress_threshold():
    c, d = _closes(60, -0.09)
    s = ss.etf_state("XLK", c, d)
    assert s["dd_pct"] == -9.0 and s["stressed"] is True
    c, d = _closes(60, -0.07)
    assert ss.etf_state("XLK", c, d)["stressed"] is False
    assert ss.drawdown_from_high([100.0]) is None


def test_build_names_missing_etfs_instead_of_dropping_them():
    def load(sym):
        if sym == "XLRE":
            return None
        return _closes(60, -0.10 if sym == "XLK" else 0.0)
    art = ss.build(load=load, today=TODAY)
    assert art["stressed"] == ["XLK"]
    assert art["missing_etfs"] == ["XLRE"]
    assert art["rule"]["stress_dd_pct"] == -8.0
    assert "NVDA" in art["sector_of"] or len(art["sector_of"]) > 400


def _art(stressed_etfs, *, as_of=TODAY, sector_of=None):
    etfs = {e: {"symbol": e, "dd_pct": -9.4 if e in stressed_etfs else -1.0,
                "stressed": e in stressed_etfs, "bars_as_of": as_of.isoformat()}
            for e in list(ss.SECTOR_ETF.values()) + list(ss.MARKET_ETFS)}
    return {"as_of_date": as_of.isoformat(), "etfs": etfs, "stressed": sorted(stressed_etfs),
            "sector_of": sector_of or {"NVDA": "XLK", "XOM": "XLE"}}


def test_stress_events_name_the_sector_and_the_market_with_the_s2_number():
    evs, gap = ss.stress_events_for("NVDA", _art({"XLK", "QQQ"}), today=TODAY)
    assert gap is None
    assert [e["scope"] for e in evs] == ["XLK", "QQQ"]
    assert "86%" in evs[0]["title"] and "96%" in evs[0]["title"] and "9.4%" in evs[0]["title"]
    evs, _ = ss.stress_events_for("XOM", _art({"XLK"}), today=TODAY)
    assert evs == []   # energy is fine, tech's sell-off is not XOM's


def test_a_stale_or_missing_artifact_is_a_named_gap_not_calm():
    _, gap = ss.stress_events_for("NVDA", None, today=TODAY)
    assert gap and "not published" in gap
    _, gap = ss.stress_events_for("NVDA", _art({"XLK"}, as_of=TODAY - timedelta(days=9)), today=TODAY)
    assert gap


def test_events_between_carries_the_sell_off_as_a_high_event():
    def fetch(path, params):
        if path.startswith("/api/earnings-calendar/"):
            return {"events": [], "store": FRESH_STORE}
        if path == "/api/catalysts/":
            return {"catalysts": [{"symbol": "MARKET", "kind": "fomc", "occurs_on": "2026-12-09",
                                   "title": "FOMC", "source": "market_calendar", "severity": "high"}]}
        if path == f"/api/today-setups/{ss.ARTIFACT_UNIVERSE}/latest":
            return {"artifact": _art({"XLK"})}
        raise AssertionError(path)
    w = ev.events_between("NVDA", TODAY, TODAY + timedelta(days=20), api_base="http://x", fetch=fetch)
    assert w.verified
    assert [e.kind for e in w.events] == ["sector_selloff"]
    assert w.events[0].scope == "XLK" and w.events[0].severity == "high"
    assert "sector sell" in w.names()


def test_events_between_names_the_gap_when_stress_is_unpublished():
    def fetch(path, params):
        if path.startswith("/api/earnings-calendar/"):
            return {"events": [], "store": FRESH_STORE}
        if path == "/api/catalysts/":
            return {"catalysts": [{"symbol": "MARKET", "kind": "fomc", "occurs_on": "2026-12-09",
                                   "title": "FOMC", "source": "market_calendar", "severity": "high"}]}
        return None   # the today-setups read 404s
    w = ev.events_between("NVDA", TODAY, TODAY + timedelta(days=20), api_base="http://x", fetch=fetch)
    assert not w.verified and any("sector stress" in g for g in w.gaps)


def test_the_sector_map_matches_the_frozen_study():
    study = Path(__file__).resolve().parents[1] / "backtests" / "studies" / "selloff_playbook_v1.py"
    spec = importlib.util.spec_from_file_location("selloff_playbook_v1", study)
    mod = importlib.util.module_from_spec(spec)
    src = study.read_text()
    # Import only the constant block: exec the module text up to `random.seed` so
    # the study's main() is never run from a test.
    ns: dict = {}
    head = src.split("random.seed")[0]
    head = "\n".join(l for l in head.splitlines()
                     if not l.startswith("sys.path.insert") and "daily_bars" not in l)
    exec(head, ns)  # noqa: S102 — our own file, constants only
    assert ns["SECTOR_ETF"] == ss.SECTOR_ETF
    assert ns["DROP"] == -ss.STRESS_DD and ns["HIGH_WIN"] == ss.HIGH_WIN
    assert mod is not None

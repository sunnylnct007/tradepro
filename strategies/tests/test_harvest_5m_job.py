"""The 5m harvest as a Lambda job: scope = flagged ∪ candidates ∪ held, a
7-day window, follow-ups that cannot hide each other."""
from __future__ import annotations

import pathlib
import sys
from datetime import date

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import lambda_handler as H  # noqa: E402

from tradepro_strategies.cli import harvest_5m as J  # noqa: E402


def test_registered_with_no_argv_of_its_own():
    assert H.JOBS["harvest_5m"] == ("tradepro_strategies.cli.harvest_5m", [])


def test_argv_is_the_mac_lanes_5m_call_with_a_trailing_window():
    a = J.harvest_argv(["QQQ", "AMD"], today=date(2026, 11, 21))
    assert a[:6] == ["--resolution", "5m", "--asset", "us_etf", "--allow-partial", "--verbose"]
    assert a[a.index("--from") + 1] == "2026-11-14" and a[a.index("--to") + 1] == "2026-11-21"
    assert a[-1] == "QQQ,AMD"


def test_scope_is_the_union_and_refuses_an_empty_universe(monkeypatch):
    import tradepro_strategies.universe as U
    monkeypatch.setattr(U, "harvest_symbols", lambda d=None: ["AAPL", "QQQ"])
    monkeypatch.setattr(J, "candidate_symbols", lambda b, t: {"AMD", "QQQ"})
    assert J.scope("http://x", "t") == ["AAPL", "AMD", "QQQ"]
    monkeypatch.setattr(U, "harvest_symbols", lambda d=None: [])
    try:
        J.scope("http://x", "t")
    except RuntimeError as exc:
        assert "empty scope" in str(exc)
    else:
        raise AssertionError("an empty universe must refuse, not harvest nothing")


def test_a_failing_follow_up_is_named_and_does_not_stop_the_others(monkeypatch):
    calls = []

    def fake_run(name, module, argv):
        calls.append(name)
        return 1 if name == "tradepro-intraday-shadow" else 0
    monkeypatch.setattr(J, "_run_cli", fake_run)
    monkeypatch.setattr(J, "scope", lambda b, t: ["QQQ"])
    import tradepro_strategies.cli.push_to_api as P
    monkeypatch.setattr(P, "load_credentials", lambda: ("http://x/", "t"))
    rc = J.main([])
    assert rc == 1
    assert calls == ["tradepro-bar-cache-harvest", "tradepro-intraday-shadow", "tradepro-intraday-setups"]


def test_movers_is_its_own_job_not_a_follow_up():
    """A slow reader must never share the harvest's time budget (21 Nov 2026:
    5m37s of cold S3 reads after a 4m20s harvest)."""
    assert "tradepro-market-movers" not in [n for n, _, _ in J.FOLLOW_UPS]
    assert H.JOBS["market_movers"] == ("tradepro_strategies.cli.market_movers", ["--push"])

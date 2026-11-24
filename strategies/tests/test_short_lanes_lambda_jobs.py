"""The short Mac lanes as Lambda jobs (22 Nov 2026): health probe, universe
refresh, trade alerts. Owner: "we are paying aws and need to leverage their
infra ... unless we have some llm to run we dont need [the Mac]".

What this pins: each job is registered against a real module with the flags
the Mac lane used; the one that PLACES (paper closes) keeps no memory on the
host it runs on; and the Lambda names itself in the run log so the side-by-side
with the Mac can be read off one table.
"""
from __future__ import annotations

import pytest

import importlib
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import lambda_handler as H  # noqa: E402

SHORT_LANES = {
    "ibkr_health_probe": ("tradepro_strategies.cli.ibkr_health_probe", []),
    "refresh_universes": ("tradepro_strategies.cli.refresh_universes", ["--push"]),
    "trade_alerts": ("tradepro_strategies.cli.trade_alerts", []),
}


def test_the_three_short_lanes_are_registered_with_the_macs_flags():
    for job, (module, argv) in SHORT_LANES.items():
        assert H.JOBS[job] == (module, argv), job


def test_each_points_at_a_real_module_with_a_main():
    for module, _ in SHORT_LANES.values():
        assert callable(getattr(importlib.import_module(module), "main"))


def test_trade_alerts_keeps_its_ledger_in_the_api_not_on_the_host():
    """A file under ~/.tradepro is wiped between Lambda cold starts; a watcher
    that forgets re-alerts and, here, RE-CLOSES. The ledger is a settings-kv
    key; the only home-directory path left is the one-time legacy import."""
    src = (ROOT / "tradepro_strategies" / "cli" / "trade_alerts.py").read_text()
    home_paths = [ln for ln in src.splitlines() if "Path.home()" in ln]
    assert home_paths == [ln for ln in home_paths if "LEGACY_FIRED" in ln], home_paths
    assert "/api/settings-kv/" in src
    assert "resolve_smtp_creds" in src and "CRED_PATH.read_text" not in src


def test_the_lambda_names_itself_in_the_run_log():
    src = (ROOT / "lambda_handler.py").read_text()
    docker = (ROOT / "Dockerfile.lambda").read_text()
    assert re.search(r'TRADEPRO_MACHINE", "lambda:tradepro-jobs"', src)
    assert "TRADEPRO_MACHINE=lambda:tradepro-jobs" in docker


def test_the_image_can_parse_wikipedia_tables():
    """refresh_universes is pandas.read_html; the image installed no HTML
    parser until this lane moved."""
    docker = (ROOT / "Dockerfile.lambda").read_text()
    assert "lxml" in docker and "beautifulsoup4" in docker


def test_refresh_universes_returns_zero_when_it_has_pushed(monkeypatch):
    """First Lambda run pushed everything and was graded FAILED: main() fell
    off the end returning None. The handler reads rc == 0 and nothing else."""
    from tradepro_strategies.cli import refresh_universes as RU
    monkeypatch.setattr(sys, "argv", ["refresh-universes", "--push"])
    monkeypatch.setattr(RU.wu, "fetch_all_universes", lambda **k: {"sp500": ["AAPL"]})
    monkeypatch.setattr(RU, "_build_payload", lambda batch: {"universes": [], "errors": {}})
    monkeypatch.setattr(RU, "_resolve_creds", lambda: ("http://api.test", "tok"))
    monkeypatch.setattr(RU, "_push", lambda payload, base, token: True)
    assert RU.main() == 0


def test_the_rotation_lane_is_retired_on_lambda_and_lives_on_fargate():
    """23 Nov 2026 22:35 UTC: the first Lambda tick timed out at 900 s reading
    ~180 names' history from S3 cold. The job is RETIRED here (a stray rule
    returns cleanly, never double-ticks the book) and runs as the Fargate
    schedule with the EFS mirror."""
    assert "paper_momentum_rotation_ibkr" not in H.JOBS
    assert "rotation-tick" in H.RETIRED_JOBS["paper_momentum_rotation_ibkr"]


def test_the_image_installs_the_ibkr_extra_the_mac_lanes_run_with():
    """First Fargate rotation tick (23 Nov 2026): 'ib_insync genuinely
    unavailable — skip' and the account-state push never happened. The Mac
    runs `uv run --extra ibkr`; the image must carry the same extra."""
    from pathlib import Path
    df = (Path(__file__).resolve().parents[1] / "Dockerfile.lambda").read_text()
    assert "ib_insync" in df


def test_the_live_book_refresh_job_reads_and_pushes_without_mail():
    """24 Nov 2026 07:15: the live card was 8 h old because the lane ran once
    a day at 22:50. The refresh job is the same read, pushed, never mailed."""
    module, argv = H.JOBS["live_risk_refresh"]
    assert module == H.JOBS["live_risk"][0]
    assert "--push" in argv and "--via-api" in argv and "--email" not in argv

"""The short Mac lanes as Lambda jobs (22 Nov 2026): health probe, universe
refresh, trade alerts. Owner: "we are paying aws and need to leverage their
infra ... unless we have some llm to run we dont need [the Mac]".

What this pins: each job is registered against a real module with the flags
the Mac lane used; the one that PLACES (paper closes) keeps no memory on the
host it runs on; and the Lambda names itself in the run log so the side-by-side
with the Mac can be read off one table.
"""
from __future__ import annotations

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

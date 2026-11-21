"""live-risk as a Lambda job (21 Nov 2026): reads the book through the API's
Flex route, never directly; NAV from the environment, never the repo."""
from __future__ import annotations

import os
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import lambda_handler as H  # noqa: E402

from tradepro_strategies.cli import live_risk as LR  # noqa: E402


def test_the_job_is_registered_via_the_api_and_carries_no_account_figure():
    module, argv = H.JOBS["live_risk"]
    assert module == "tradepro_strategies.cli.live_risk"
    assert "--via-api" in argv and "--push" in argv and "--email" in argv
    assert "--nav-usd" not in argv
    assert not any(a.replace(".", "").isdigit() for a in argv)


def test_via_api_never_touches_flex_credentials(monkeypatch):
    """Even with a token in the environment, --via-api must not read it."""
    monkeypatch.setenv("TRADEPRO_IBKR_FLEX_TOKEN", "t")
    monkeypatch.setenv("TRADEPRO_IBKR_FLEX_POSITIONS_QUERY", "q")
    calls = []
    monkeypatch.setattr(LR, "flex_credentials", lambda *a, **k: calls.append(1) or ("t", "q", "environment"))
    monkeypatch.setattr(LR, "_flex_direct", lambda *a, **k: pytest.fail("direct Flex read must not run"))
    monkeypatch.setattr(LR, "load_credentials", lambda: ("http://api.test", "tok"))

    class R:
        status_code = 200

        def json(self):
            return {"enabled": True, "positions": []}
    seen = {}
    def fake_get(url, **k):
        seen["url"] = url
        return R()
    monkeypatch.setattr(LR.requests, "get", fake_get)
    monkeypatch.setattr(sys, "argv", ["live-risk", "--via-api"])
    rc = LR.main()
    assert calls == [] and seen["url"].endswith("/api/integrations/ibkr/flex/positions")
    assert rc == 1   # an empty book is refused, as before


def test_nav_comes_from_the_environment_when_not_passed(monkeypatch):
    monkeypatch.setenv("TRADEPRO_LIVE_NAV_USD", "123456")
    assert LR._env_float("TRADEPRO_LIVE_NAV_USD") == 123456.0
    monkeypatch.setenv("TRADEPRO_LIVE_NAV_USD", "not a number")
    assert LR._env_float("TRADEPRO_LIVE_NAV_USD") is None
    monkeypatch.delenv("TRADEPRO_LIVE_NAV_USD")
    assert LR._env_float("TRADEPRO_LIVE_NAV_USD") is None

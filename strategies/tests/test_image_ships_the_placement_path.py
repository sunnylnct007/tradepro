"""The jobs image must carry every package the paper session imports.

25 Nov 2026. AMD closed below the rotation sleeve's trailing stop on 24 Nov.
Every Fargate tick of the 25th decided "exit-trailing-stop" for it, the router
printed "IBKR_PAPER AUTO-OMS · SELL AMD qty=7", and no order ever reached the
OMS: `_push_pending` imports httpx, Dockerfile.lambda installed a hand-picked
list of eleven packages without it, and the ImportError was a WARNING followed
by `return`. The decision log, the board and the account card were all green.

Three rules, each pinned here:
  1. the image installs pyproject's dependency list — one list, not two;
  2. every third-party import on the paper path is DECLARED in that list;
  3. a router that cannot send an order refuses to start, and the success line
     prints only when the push happened.
"""
from __future__ import annotations

import builtins
import logging
import re
import sys
import tomllib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
PKG = ROOT / "tradepro_strategies"

# import name -> distribution name where they differ
_DIST = {
    "dateutil": "python-dateutil",
    "bs4": "beautifulsoup4",
    "yaml": "pyyaml",
    "fitz": "pymupdf",
    "exchange_calendars": "exchange-calendars",
    "firebase_admin": "firebase-admin",
    "multipart": "python-multipart",
    "botocore": "boto3",  # ships with boto3
}
# Imported only under their own feature flags, never on the paper path.
_NOT_ON_THE_PAPER_PATH = {"ib_insync"}  # the ibkr extra — checked separately


def _declared() -> set[str]:
    proj = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]
    specs = proj["dependencies"] + proj["optional-dependencies"]["ibkr"]
    return {re.split(r"[\[<>=!~ ]", s, 1)[0].lower() for s in specs}


def _third_party_imports(files) -> dict[str, set[str]]:
    """Top-level names of every import statement, read from the AST (a regex
    over the text also matched prose such as "import the whole tree")."""
    import ast
    stdlib = set(sys.stdlib_module_names)
    out: dict[str, set[str]] = {}
    for f in files:
        tree = ast.parse(f.read_text(errors="ignore"))
        for node in ast.walk(tree):
            names = []
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                names = [node.module]
            for n in names:
                top = n.split(".")[0]
                if top in stdlib or top in ("tradepro_strategies", "__future__"):
                    continue
                out.setdefault(top, set()).add(str(f.relative_to(ROOT)))
    return out


def test_the_image_installs_pyprojects_list_not_a_hand_list():
    docker = (ROOT / "Dockerfile.lambda").read_text()
    install = docker[docker.index("RUN python -m pip install"):docker.index("COPY tradepro_strategies")]
    assert "tomllib" in install and "pyproject.toml" in install
    assert "['dependencies']" in install and "['ibkr']" in install
    # No package is pinned by hand in the install step any more.
    assert not re.search(r'"[a-z_0-9-]+>=[\d.]+"', install), install


def test_every_third_party_import_on_the_paper_path_is_declared():
    files = list((PKG / "paper").rglob("*.py")) + list((PKG / "bar_cache").rglob("*.py")) + [
        PKG / "cli" / "paper_session.py", PKG / "secrets.py", PKG / "run_log.py",
        PKG / "cli" / "push_to_api.py", PKG / "momentum_rotation.py",
    ]
    files = [f for f in files if f.exists()]
    declared = _declared()
    missing = {}
    for name, where in _third_party_imports(files).items():
        dist = _DIST.get(name, name).lower()
        if dist not in declared and name not in _NOT_ON_THE_PAPER_PATH:
            missing[name] = sorted(where)[:3]
    assert not missing, f"imported on the paper path but not declared in pyproject: {missing}"


def test_httpx_is_declared_because_the_order_push_imports_it():
    assert "httpx" in _declared()
    t212 = (PKG / "paper" / "brokers" / "t212.py").read_text()
    assert "import httpx" in t212


def _no_httpx(monkeypatch):
    real = builtins.__import__

    def fake(name, *a, **k):
        if name == "httpx":
            raise ImportError("No module named 'httpx'")
        return real(name, *a, **k)

    monkeypatch.setattr(builtins, "__import__", fake)
    monkeypatch.delitem(sys.modules, "httpx", raising=False)


@pytest.mark.parametrize("mode", ["auto", "manual"])
def test_the_oms_router_refuses_to_start_without_httpx(monkeypatch, mode):
    from tradepro_strategies.paper.brokers import t212 as T
    monkeypatch.setattr(T, "get_secret", lambda *_a, **_k: None)
    _no_httpx(monkeypatch)
    with pytest.raises(RuntimeError, match="httpx"):
        T.T212OrderRouter(mode="demo", allow_real_orders=False, placement_mode=mode,
                          broker_label_override="IBKR_PAPER")


def test_the_router_starts_when_httpx_is_present(monkeypatch):
    from tradepro_strategies.paper.brokers import t212 as T
    monkeypatch.setattr(T, "get_secret", lambda *_a, **_k: None)
    T.T212OrderRouter(mode="demo", allow_real_orders=False, placement_mode="auto",
                      broker_label_override="IBKR_PAPER")


def test_auto_oms_is_logged_only_when_the_push_happened(monkeypatch, caplog):
    """The success line printed on every path; a dropped order read as placed."""
    import asyncio
    from tradepro_strategies.paper.brokers import t212 as T
    from tradepro_strategies.paper.messages import OrderApproved
    from tradepro_strategies.paper.strategy import Order, OrderSide, OrderType

    monkeypatch.setattr(T, "get_secret", lambda *_a, **_k: None)
    r = T.T212OrderRouter(mode="demo", allow_real_orders=False, placement_mode="auto",
                          broker_label_override="IBKR_PAPER")
    order = Order(strategy_id="momentum_rotation_ibkr", symbol="AMD", side=OrderSide.SELL,
                  quantity=7, type=OrderType.MARKET, tag="swing exit trailing-stop held=7")
    approval = OrderApproved(order=order, bar_at_approval=None, approved_at=None)

    async def not_sent(*_a, **_k):
        return False

    async def sent(*_a, **_k):
        return True

    with caplog.at_level(logging.INFO):
        monkeypatch.setattr(r, "_push_pending", not_sent)
        asyncio.run(r._handle_approval(approval, asyncio.Queue()))
        assert "AUTO-OMS" not in caplog.text
        assert "ORDER NOT SENT" in caplog.text and "SELL AMD" in caplog.text
        caplog.clear()
        monkeypatch.setattr(r, "_push_pending", sent)
        asyncio.run(r._handle_approval(approval, asyncio.Queue()))
        assert "IBKR_PAPER AUTO-OMS" in caplog.text and "SELL AMD" in caplog.text


def test_push_pending_returns_false_without_httpx(monkeypatch, caplog):
    import asyncio
    from tradepro_strategies.paper.brokers import t212 as T
    from tradepro_strategies.paper.strategy import Order, OrderSide, OrderType

    monkeypatch.setattr(T, "get_secret", lambda *_a, **_k: None)
    r = T.T212OrderRouter(mode="demo", allow_real_orders=False, placement_mode="auto",
                          broker_label_override="IBKR_PAPER")
    order = Order(strategy_id="s", symbol="AMD", side=OrderSide.SELL, quantity=7,
                  type=OrderType.MARKET, tag="t")
    _no_httpx(monkeypatch)
    with caplog.at_level(logging.ERROR):
        assert asyncio.run(r._push_pending(order, None)) is False
    assert "NOT sent" in caplog.text

"""24 Nov 2026 review: get_research_studies returned "studies.json not found"
in production — the file lived under frontend/src/data, which neither image
copies. ONE copy now lives inside the package; the frontend imports it from
there. No second copy may come back."""
from __future__ import annotations

import json
from pathlib import Path

PKG = Path(__file__).resolve().parents[1] / "tradepro_strategies"
REPO = PKG.parents[1]


def test_the_register_is_inside_the_package_and_parses():
    f = PKG / "data" / "studies.json"
    assert f.exists()
    data = json.loads(f.read_text())
    assert (data if isinstance(data, list) else data.get("studies")), "register is empty"


def test_there_is_no_second_copy_under_the_frontend():
    assert not (REPO / "frontend" / "src" / "data" / "studies.json").exists()
    src = (REPO / "frontend" / "src" / "components" / "desk" / "ResearchView.tsx").read_text()
    assert "strategies/tradepro_strategies/data/studies.json" in src


def test_the_mcp_tool_finds_it_from_the_package(monkeypatch):
    from tradepro_strategies.mcp import tools as T
    r = T.get_research_studies()
    assert r.get("count", 0) > 0 and not r.get("error")

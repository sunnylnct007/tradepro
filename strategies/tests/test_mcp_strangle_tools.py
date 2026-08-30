"""The strangle suite must be reachable over MCP, and reachable ONCE.

Owner, 30 Aug 2026: "all functionality we need to expose from tradepro as mcp
so we can improve them if needed". A strategy that can only be inspected by
running a CLI on one laptop cannot be reviewed or improved from a chat.
"""
from __future__ import annotations

import os

from tradepro_strategies.mcp import tools as t

SERVER = os.path.join(os.path.dirname(t.__file__), "server.py")
EXPECTED = [
    "get_index_strangle_candidates",
    "get_index_strangle_markets",
    "get_index_strangle_evidence",
    "get_index_strangle_threshold_rule",
    "get_index_strangle_alerts",
    "run_index_strangle_sim",
]


def test_no_tool_is_registered_twice():
    """FastMCP lets the LAST registration of a name win, silently. Two tools
    were registered twice (list_watchlists, get_watchlist) — the first of each
    was dead code that still looked live, which is precisely how someone edits
    the copy that does nothing. This is the repo's dominant bug shape sitting
    inside the MCP server, so it gets a permanent guard."""
    import re
    src = open(SERVER).read()
    names = re.findall(r"@mcp\.tool\(\)\s*\n\s*@instrumented\([^)]*\)\s*\n\s*def (\w+)",
                       src)
    dupes = {n for n in names if names.count(n) > 1}
    assert not dupes, f"registered more than once: {sorted(dupes)}"
    assert len(names) > 80, f"only found {len(names)} tools — regex drifted"


def test_strangle_tools_exist_in_both_layers():
    """A tool registered in server.py but missing from tools.py fails at call
    time, inside a chat, with a traceback the caller cannot act on."""
    src = open(SERVER).read()
    for name in EXPECTED:
        assert hasattr(t, name), f"tools.py missing {name}"
        assert f'def {name}(' in src, f"server.py does not register {name}"


def test_offline_strangle_tools_answer_without_network():
    """These three read the committed evidence and config only. If any starts
    reaching for the network it becomes unusable from a chat that is waiting."""
    for name in ("get_index_strangle_markets", "get_index_strangle_evidence",
                 "get_index_strangle_threshold_rule"):
        out = getattr(t, name)()
        assert out.get("ok") is True, (name, out.get("error"))


def test_market_config_and_evidence_agree_over_mcp():
    """The gate an MCP caller is shown must be the gate the screen uses."""
    cfg = {m["market"]: m["vol_gate"]
           for m in t.get_index_strangle_markets()["markets"]}
    rule = t.get_index_strangle_threshold_rule()["markets"]
    for m, r in rule.items():
        assert r["configured"] == cfg[m], m
        assert r["agrees"] is True, f"{m}: gate {cfg[m]} but rule says {r['chosen_by_rule']}"


def test_unknown_market_is_refused_not_guessed():
    for bad in ("FTSE", "RUSSELL", "nonsense"):
        assert t.get_index_strangle_candidates(bad)["ok"] is False
        assert t.run_index_strangle_sim(bad)["ok"] is False


def test_sim_runs_are_bounded():
    """An unbounded paths argument from a chat prompt would hang the server."""
    assert t.run_index_strangle_sim("NIFTY", paths=10**7)["ok"] is False
    assert t.run_index_strangle_sim("NIFTY", trades=10**6)["ok"] is False

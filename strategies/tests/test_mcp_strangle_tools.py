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


def test_the_live_pnl_tool_is_registered_and_explains_its_nulls():
    """An agent must be able to ask "how are we doing" and get the WHOLE number.

    Owner, 8 Sep 2026: "we shd be able to see live pnl at any point of time."
    The desk is reviewed through an agent, and an agent cannot read a screen —
    a figure that exists only in the UI does not exist for the review.
    """
    from tradepro_strategies.mcp import tools

    assert hasattr(tools, "get_strangle_live_pnl")
    doc = tools.get_strangle_live_pnl.__doc__ or ""
    # The dangerous misreading is null-as-flat. It must be stated where it is
    # read, not only in the endpoint that produces it.
    assert "null" in doc.lower()
    assert "warnings" in doc.lower()


def test_the_live_pnl_tool_calls_the_pnl_endpoint_and_keeps_the_warnings():
    import types
    from tradepro_strategies.mcp import tools

    seen = {}

    def fake_get(path, params=None):
        seen["path"], seen["params"] = path, params
        return {"asOfUtc": "2026-09-08T16:00:00Z", "broker": "IBKR_PAPER",
                "realised": {"total": 188.73}, "open": {"unrealised": -48.35},
                "total": 140.38,
                "warnings": ["a session here has BOTH a realised result and an open position"]}

    orig = tools._get
    tools._get = fake_get
    try:
        out = tools.get_strangle_live_pnl(days=1)
    finally:
        tools._get = orig

    assert seen["path"] == "/api/strangle-decisions/pnl"
    assert out["total"] == 140.38
    # A warning dropped in transit is a warning that does not exist. The blend
    # caveat is the whole reason today's number needs reading twice.
    assert out["warnings"] and "open position" in out["warnings"][0]

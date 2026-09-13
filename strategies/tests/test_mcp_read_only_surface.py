"""The remote MCP endpoint must not be able to trade.

`tradepro-mcp-http` serves build_server(read_only=True) to any agent
that can reach the public URL. These tests pin the boundary: the
money-touching tools are gone, and the guard that removes them fails
loudly rather than fails open.
"""
from __future__ import annotations

import pytest

from tradepro_strategies.mcp.server import (
    MUTATING_TOOLS,
    _READ_ONLY_TRACE_TOOLS,
    _apply_read_only,
    build_server,
)


def _names(server) -> set[str]:
    return {tool.name for tool in server._tool_manager.list_tools()}


# The five that move a position or release an order. Spelled out
# separately from MUTATING_TOOLS so that emptying that set by accident
# still fails the suite.
MONEY_TOOLS = {
    "approve_paper_order",
    "reject_paper_order",
    "set_paper_placement_mode",
    "close_option_leg",
    "flatten_short_options",
}


def test_money_tools_absent_from_read_only_surface():
    exposed = _names(build_server(read_only=True))
    assert not (MONEY_TOOLS & exposed), (
        f"remote surface can trade: {sorted(MONEY_TOOLS & exposed)}"
    )


def test_read_only_removes_exactly_the_denylist():
    full = _names(build_server())
    ro = _names(build_server(read_only=True))
    assert full - ro == set(MUTATING_TOOLS)


def test_stdio_surface_keeps_everything():
    """The Mac keeps the full surface — read_only must be opt-in."""
    full = _names(build_server())
    assert MUTATING_TOOLS <= full


def test_read_only_surface_is_not_empty():
    """A guard that removed everything would also pass the checks above."""
    ro = _names(build_server(read_only=True))
    assert len(ro) > 50
    assert "get_swing_candidates" in ro
    assert "get_option_chain" in ro


def test_stale_denylist_entry_aborts_startup():
    """Rename a mutating tool without updating MUTATING_TOOLS and the
    server must refuse to start, not serve it under the new name."""
    server = build_server()
    server.remove_tool("flatten_short_options")
    with pytest.raises(RuntimeError, match="no longer registered"):
        _apply_read_only(server)


def test_verb_tripwire_catches_an_undeclared_mutator():
    """A NEW mutating tool nobody added to the denylist must abort
    startup rather than land on the public endpoint."""
    server = build_server()

    @server.tool()
    def cancel_everything() -> str:  # pragma: no cover - never called
        return "{}"

    with pytest.raises(RuntimeError, match="mutating-looking tools"):
        _apply_read_only(server)


def test_trace_tools_are_exempt_from_the_tripwire():
    """record_step & friends are verb-shaped but write only this
    process's own trace file."""
    ro = _names(build_server(read_only=True))
    assert _READ_ONLY_TRACE_TOOLS <= ro

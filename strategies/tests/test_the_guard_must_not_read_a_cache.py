"""The broker guard must force a FRESH read, or it guards nothing.

22 Sep 2026, one day after the guard shipped, the runaway recurred:

    LRCX  held 18  ->  SHORT 252   (12 repeat sells)
    ASML  held  3  ->  SHORT  42   (12 repeat sells)

The guard's logic was right and its SOURCE was stale. It called
GET /api/integrations/ibkr/positions with no `fresh` parameter, and that
endpoint defaults to `forceFresh: false` — which serves IBKR's own cache.

IBKRClient.GetPositionsAsync documents the trap in terms:

    "IBKR SERVES THIS FROM A CACHE ... a stale read there means refusing a real
     close, or worse, BELIEVING A POSITION EXISTS THAT DOES NOT. Pass forceFresh
     after anything that MUTATES the book."

This guard runs immediately after selling — precisely "after something that
mutates the book". So it sold 18 LRCX, re-read the cache, was told it still held
18, and sold again. It never once fired.

One query parameter is the difference between a guard and a decoration.
"""
import ast
import pathlib

SRC = (pathlib.Path(__file__).resolve().parents[1] / "tradepro_strategies"
       / "paper" / "strategies" / "mean_reversion_swing.py").read_text()


def _guard() -> str:
    for n in ast.walk(ast.parse(SRC)):
        if isinstance(n, ast.FunctionDef) and n.name == "_broker_positions":
            return ast.get_source_segment(SRC, n) or ""
    raise AssertionError("_broker_positions not found")


def test_the_read_forces_a_fresh_position_fetch():
    g = _guard()
    assert '"fresh"' in g and '"true"' in g, (
        "without ?fresh=true this reads IBKR's cache and the guard is a decoration"
    )


def test_the_fresh_flag_is_on_the_POSITIONS_call():
    """Not on some other request that happens to live in the same function."""
    g = _guard()
    i = g.index("integrations/ibkr/positions")
    j = g.index("timeout=", i)
    assert '"fresh"' in g[i:j], "the fresh flag is not attached to the positions GET"


def test_an_unreadable_broker_still_returns_None_not_empty():
    """None means 'could not ask'. {} would mean 'we hold nothing' and would
    licence selling everything — the opposite of a guard."""
    g = _guard()
    assert "return None" in g


def test_the_reason_is_recorded_next_to_the_call():
    """So the next person to 'simplify' this sees the 252-share cost first."""
    g = _guard()
    assert "CACHE" in g
    assert "LRCX" in g or "252" in g

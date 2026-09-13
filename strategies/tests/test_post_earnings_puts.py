

def test_a_stock_that_rose_is_not_described_as_having_fallen():
    """Owner, 13 Sep 2026: "the comments are not clear to me".

    The why_not line read "fell {move}%" for EVERY near-miss regardless of sign,
    so SNOW — which gained 16.6% on its print — was described on screen as
    "fell 16.6% on the report". CIEN, ADBE, DELL and HPE all rose and all read
    as falls. A negative move produced "fell -3.8%", a double negative meaning
    the opposite.

    This screen is a candidate list a human acts on, and the direction of the
    earnings move is the single fact the setup turns on.
    """
    import inspect
    from tradepro_strategies.cli import post_earnings_puts as P

    src = inspect.getsource(P)
    # The sign must be branched on, not assumed.
    assert 'if pct < 0:' in src and 'elif pct > 0:' in src
    assert 'ROSE' in src
    # And the old unconditional wording must be gone.
    assert 'f"fell {100 * move:.1f}% on the report' not in src


def test_the_threshold_is_stated_as_a_direction_not_a_signed_number():
    """"needs -8%" made a reader work out whether that meant at least -8% or at
    most. It now says "needs a drop of 8% or more"."""
    import inspect
    from tradepro_strategies.cli import post_earnings_puts as P

    src = inspect.getsource(P)
    assert "needs a drop of" in src

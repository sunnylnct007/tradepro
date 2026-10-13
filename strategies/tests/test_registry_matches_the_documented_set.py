"""The registry must BE what the module says it is.

THE DIVERGENCE, 13 Oct 2026. `paper/strategies/__init__.py` has carried this
since the 10 Oct cut:

    # ichimoku_equity, ichimoku_fx_mr, intraday_flat and orb no longer
    # register.

Three of the four were right. ORB's import was never removed, so
`available()` returned it for three days while the comment said otherwise, and
it reached the live API's strategy catalogue as a registered sleeve — found by
reading `/api/paper/strategies`, not by reading the file, because the file read
as correct.

ORB had failed 3 of its 5 pre-registered gates (INTRADAY_VWAP_ORB_GATES_V1),
nothing schedules it, and the 1m parquet harvest it depended on has written
nothing for over a week. Every reason to be gone, and a comment saying it was.

WHY THIS TEST IS SHAPED LIKE THIS. It asserts on `available()` — the thing the
rest of the system actually calls — not on the presence of an import line or
the text of a comment. A grep test cannot see dead code, and a comment is not
a mechanism. Deregistration happens by NOT importing a module, which means the
only honest check is what the registry ends up holding.

To retire a strategy: add it to `registry.RETIRED` and drop it from EXPECTED
here. To bring one back: remove it from RETIRED and add it to EXPECTED. The
registry ENFORCES RETIRED by refusing registration, so an unrelated import can
no longer undo the decision — see the note above RETIRED for the lane-entry-
point import that did exactly that.
"""
from __future__ import annotations

import tradepro_strategies.paper.strategies as S
from tradepro_strategies.paper.registry import RETIRED

#: Every strategy that may register, and why it is here.
#:
#: mean_reversion_swing — the Swing sleeve. Live: mean_reversion_swing_ibkr.
#: momentum_pullback    — the Momentum sleeve. Live on IBKR (winding down) and
#:                        T212 (momentum_pullback_t212, placing since 13 Oct).
EXPECTED = {"mean_reversion_swing", "momentum_pullback"}

# RETIRED is imported from the registry, NOT restated here. It is the list the
# registry ENFORCES, so a test with its own copy would pass while the enforced
# set said something else — the duplicate-definition shape this file exists to
# catch. (13 Oct 2026: retirement became a declaration the registry refuses
# registration against, because "do not import it" was undone by the lane
# entry point importing it.)


def test_registry_is_exactly_the_documented_set():
    assert set(S.available()) == EXPECTED, (
        "the registry does not match EXPECTED. A strategy appearing here that "
        "nothing schedules will still reach the live catalogue, the health "
        "panel and the P&L board as if it were a live sleeve — which is how "
        "orb survived its own retirement for three days."
    )


def test_no_retired_strategy_registers():
    """Stated separately so the failure NAMES the strategy that came back."""
    back = sorted(RETIRED & set(S.available()))
    assert not back, (
        f"retired strategy/strategies registered again: {back}. registry.RETIRED "
        f"is meant to make this impossible by refusing registration — if this "
        f"fires, either the name was removed from RETIRED, or the refusal in "
        f"register_strategy was bypassed (e.g. a direct _REGISTRY write, or an "
        f"entry-point/dotted-path load that skips the decorator)."
    )


def test_expected_and_retired_do_not_overlap():
    """Guards the guard: a live strategy listed as retired could never run."""
    assert not (EXPECTED & set(RETIRED))


def test_every_registered_strategy_can_actually_be_built():
    """Registered but unbuildable is worse than absent — it fails at trade time.

    `available()` only proves the decorator ran. The lanes call build().
    """
    for name in sorted(S.available()):
        assert S.build(name, strategy_id=f"{name}_test") is not None, name


def test_importing_the_lane_entry_point_cannot_resurrect_a_strategy():
    """The 13 Oct bug, pinned at its actual cause.

    cli/paper_session.py imports OpeningRangeBreakout at module scope and the
    other three retired classes inside functions. Under the old rule —
    deregistration = nobody imports it — that import alone put orb back in
    every live lane process:

        import tradepro_strategies.paper.strategies   -> [swing, momentum]
        import tradepro_strategies.cli.paper_session  -> [swing, momentum, orb]

    Importing the lane is what the lanes do, so this asserts the registry is
    unchanged afterwards. It also makes the suite order-independent: this test
    used to pass alone and fail in the full run, because another test had
    already imported the module.
    """
    import tradepro_strategies.cli.paper_session  # noqa: F401
    assert set(S.available()) == EXPECTED, (
        "importing the lane entry point changed the registry — retirement is "
        "being enforced by absence-of-import again, not by declaration"
    )


def test_importing_a_retired_class_directly_is_still_allowed():
    """Retirement must not break the retired strategy's OWN tests.

    Several tests construct IchimokuEquityStrategy directly. Refusing the
    IMPORT (rather than the registration) would have broken them, so the
    registry skips registration and lets the class exist.
    """
    from tradepro_strategies.paper.strategies.ichimoku_equity import (
        IchimokuEquityStrategy,
    )
    assert IchimokuEquityStrategy is not None
    assert "ichimoku_equity" not in S.available()

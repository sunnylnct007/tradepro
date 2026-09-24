"""A strategy that argparse ACCEPTS must be one paper_session can BUILD.

THE BUG (24 Sep 2026). `_strategy_choices` reads the registry — added 23 Aug
precisely so "a strategy could be written, registered, tested and still be
unrunnable" could not happen again. CONSTRUCTION was left as a hardcoded chain
of `if strategy_name == "..."`. So momentum_pullback passed argparse, reached
the builder, and raised `ValueError: Unknown strategy 'momentum_pullback'` on
its first live run, thirty minutes before the close.

Half the idea was applied. This pins both halves together: anything the CLI
offers, the CLI must be able to construct.
"""
from __future__ import annotations

import pytest

from tradepro_strategies.cli.paper_session import _SWING_FAMILY, _strategy_choices
from tradepro_strategies.paper import strategies as _s  # noqa: F401 — registers
from tradepro_strategies.paper.registry import get as registry_get, list_names


def test_every_offered_choice_is_a_registered_strategy():
    for name in _strategy_choices():
        registry_get(name)      # raises KeyError if the CLI offers a ghost


@pytest.mark.parametrize("name", sorted(_SWING_FAMILY))
def test_every_swing_family_member_is_registered_and_constructible(name):
    """The family is built from the registry, so membership must resolve."""
    assert name in list_names(), f"{name} is in _SWING_FAMILY but not registered"
    cls = registry_get(name).cls
    from tradepro_strategies.paper.strategies.mean_reversion_swing import (
        MeanReversionSwingStrategy)
    assert issubclass(cls, MeanReversionSwingStrategy), (
        f"{name} is in _SWING_FAMILY but is not the swing engine — it would be "
        "built with the wrong constructor arguments")


def test_the_family_covers_every_subclass_of_the_swing_engine():
    """A new sibling that is NOT added to _SWING_FAMILY would be offered by
    argparse and then fail to build — the exact bug, reintroduced."""
    from tradepro_strategies.paper.strategies.mean_reversion_swing import (
        MeanReversionSwingStrategy)
    for name in list_names():
        cls = registry_get(name).cls
        if issubclass(cls, MeanReversionSwingStrategy):
            assert name in _SWING_FAMILY, (
                f"{name} subclasses the swing engine but is missing from "
                "_SWING_FAMILY, so paper_session cannot construct it")

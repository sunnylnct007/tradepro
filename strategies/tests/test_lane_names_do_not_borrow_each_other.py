"""No lane may borrow another lane's name.

Owner, 20 Sep 2026: *"we havent fixed the naming of swing and sing watch
yet"*. The board showed **Swing** and **SwingWatch** side by side. They have
nothing to do with each other:

  Swing      — the mean-reversion rule, tier "gated", pre-registered backtest,
               21,948 out-of-sample trades behind it
  SwingWatch — a hand-onboarded watchlist with armed levels, tier "unproven",
               no backtest at all

Sharing the word made the second read as a variant of the first — the exact
"a candidate from a strategy that has not passed its gates must not look like
one from a strategy that has" problem the board's own header warns about. It
is now "Watchlist", which is what it is.

These tests are cheap insurance against the next lane being called
"MomentumWatch" or "WheelScout".
"""
import re

import pytest

from tradepro_strategies.cli import preearnings_watch as pw

# Every lane the desk publishes today. The gated sleeves own their names.
GATED_SLEEVES = ("Swing", "Momentum", "Wheel", "Setups")
WATCH_LANES = ("Pre-Earn", "Watchlist", "Scout")


@pytest.mark.parametrize("lane", WATCH_LANES)
def test_no_watch_lane_contains_a_gated_sleeves_name(lane):
    for sleeve in GATED_SLEEVES:
        assert sleeve.lower() not in lane.lower().replace(" ", ""), (
            f"lane {lane!r} borrows the {sleeve!r} sleeve's name; a reader "
            f"cannot tell an unproven lane from a gated one")


def test_the_watchlist_lane_is_emitted_under_its_own_name():
    src = pw.__file__
    import pathlib
    text = pathlib.Path(src).read_text()
    # the emitted string, not the explanatory comment
    code = "\n".join(l for l in text.splitlines()
                     if not l.lstrip().startswith("#"))
    assert '"Watchlist"' in code
    assert '"SwingWatch"' not in code


def test_lane_names_are_plain_english_not_jargon():
    """Owner's standing complaint: 'i see scout, watch, hold, wait ... nothing
    too concrete'. A lane name must be a noun a trader recognises, not an
    internal state."""
    for lane in WATCH_LANES + GATED_SLEEVES:
        assert not re.search(r"[_A-Z]{2,}", lane), f"{lane!r} reads as a code"
        assert len(lane) <= 12, f"{lane!r} is too long for a pill"

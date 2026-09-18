"""A row that says BUY must not tell the owner to wait.

18 Sep 2026, owner on the desk screen: *"i see buy signal and contradicting
why column"*. CVS, BAC and VZ each showed ACTION "BUY today" beside a WHY of
"KNIFE — wait for a higher low" / "no reversal sign yet".

The row was right and the sentence was wrong. Waiting for a green bar is Q3 of
SWING_V3_GATES_V1 — pre-registered, measured on 3,028 trades, FAILED: the win
rate rises but mean return per trade halves (+1.00% control vs +0.42%
confirmed) because the edge lives in the close nobody wants to buy. The
structure verdict was kept for discretionary context, and then phrased as an
instruction to do the rejected thing.

Every swing row's action is "buy". So the why-text may describe the tape and
must never direct the owner against the rule the desk actually measured.
"""
import pytest

from tradepro_strategies.cli.swing_candidates import (
    CONFIRMATION_MEAN_PCT, CONTROL_MEAN_PCT, _structure_phrase,
)

# Phrases that tell the reader to NOT take the action the row advertises.
CONTRADICTIONS = ("wait for", "hold off", "do not buy", "don't buy", "avoid",
                  "stay out", "wait until", "not yet a buy")

STRUCTURES = ("KNIFE: 3 straight lower lows, 7/10 days down — no reversal sign",
              "basing: 4 sessions inside the prior range",
              "mixed: no clear structure",
              "")


@pytest.mark.parametrize("struct", STRUCTURES)
def test_no_structure_verdict_tells_a_buy_row_to_wait(struct):
    txt = _structure_phrase(struct).lower()
    for bad in CONTRADICTIONS:
        assert bad not in txt, f"{struct!r} produced an instruction to wait: {txt!r}"


def test_the_knife_case_still_describes_the_tape():
    """Removing the instruction must not remove the information."""
    txt = _structure_phrase("KNIFE: 3 straight lower lows, 7/10 days down")
    assert "lower lows" in txt


def test_it_states_what_waiting_would_cost():
    """The owner's standing rule: a warning must carry its number."""
    txt = _structure_phrase("KNIFE: 3 straight lower lows")
    assert f"+{CONFIRMATION_MEAN_PCT:.2f}%" in txt
    assert f"+{CONTROL_MEAN_PCT:.2f}%" in txt
    assert "HALVED" in txt


def test_the_measured_numbers_are_not_restated_anywhere_else():
    """One rule, one object — the study's figures live in one place.

    If these appear as literals elsewhere in the module they will drift from
    the study the moment it is re-run.
    """
    import pathlib
    import re
    src = pathlib.Path(
        "tradepro_strategies/cli/swing_candidates.py").read_text()
    body = src.split("def _structure_phrase")[0]
    body = body.replace(f"CONFIRMATION_MEAN_PCT = {CONFIRMATION_MEAN_PCT}", "")
    body = body.replace(f"CONTROL_MEAN_PCT = {CONTROL_MEAN_PCT:.2f}", "")
    assert not re.search(r"\+0\.42%", body)


def test_basing_is_left_alone_because_it_never_contradicted_anything():
    assert _structure_phrase("basing: tight range") == "basing — placeable as a bracket"

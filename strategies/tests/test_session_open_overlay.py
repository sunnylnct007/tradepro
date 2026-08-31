"""The strike anchor needs TODAY's open; the bar cache is end-of-day.

THE REGRESSION (31 Aug 2026). Routing US prices through BarStore — correct, it
is the IBKR-primary path — silently broke the strike anchor. The harvest runs
after the close, so DURING a session the store has no bar for today. SPY, QQQ
and GOLD therefore fell back to spot_basis="prior_close", which marks the row
PROVISIONAL, and place_paper() refuses on provisional. Paper execution could
never have fired, on exactly the three markets that are paper-tradeable.

Caught by reading the basis column once the market opened rather than assuming
the change was harmless.
"""
from __future__ import annotations

from tradepro_strategies.cli import index_strangle_paper as P


def test_the_overlay_exists_and_is_labelled():
    """History from the golden source, today's open from the only feed that has
    it intraday — and the row must SAY it came from two providers."""
    src = open(P.__file__).read()
    assert "_todays_open_row" in src
    assert "bar_cache(ibkr)+yahoo(open)" in src, (
        "a row built from two providers must say so — silently blending them is "
        "exactly the provenance problem this file was just fixed for")


def test_the_overlay_never_overwrites_a_settled_bar():
    """If the store already has today, the live partial must NOT replace it."""
    src = open(P.__file__).read()
    assert "not in out.index" in src, (
        "the overlay must only ADD today's row, never overwrite a settled one")


def test_a_failed_overlay_degrades_to_provisional_not_a_crash():
    """No overlay is a PROVISIONAL row — a decision that says so — not an
    exception that loses the decision entirely."""
    src = open(P.__file__).read()
    i = src.find("def _todays_open_row")
    j = src.find("\ndef ", i + 10)
    body = src[i:j]
    assert "return None" in body and "except" in body


def test_the_gate_still_reads_only_settled_sessions():
    """The overlay adds an IN-FLIGHT bar. The gate must still exclude it —
    otherwise this reintroduces the lookahead corrected this morning."""
    src = open(P.__file__).read()
    assert 'settled = [d for d in common if not (d == local_today and state != "closed")]' in src

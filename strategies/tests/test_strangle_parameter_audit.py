"""The parameter audit must REPORT drift and never re-tune.

Owner, 31 Aug 2026, after his live BANKNIFTY strangle came out narrower than
the emailed one: "it might be our stratgey is tight evaluate at regular
interval".

He is right that a parameter nobody re-checks goes stale. But an audit that
silently re-tunes on a schedule is worse than none: it chases noise, and it
invalidates every published figure without anyone deciding anything. So this
reports and stops.
"""
from __future__ import annotations

import os

from tradepro_strategies.cli import index_strangle_sim as S
from tradepro_strategies.cli import index_strangle_paper as P


def test_audit_never_mutates_the_configuration():
    """The property that matters. If the audit ever writes back a threshold or
    a strike multiple, published evidence silently stops describing the traded
    thing — the exact failure this project keeps hitting."""
    before = {m: c["vol_max"] for m, c in P.MARKETS.items()}
    mult_before = P.STRIKE_MULT
    src = open(S.__file__).read()
    i = src.find("def audit(")
    j = src.find("\ndef _returns_at_width", i)
    body = src[i:j]
    for forbidden in ("MARKETS[", "vol_max\"] =", "STRIKE_MULT =", "open(", "json.dump"):
        assert forbidden not in body.replace('MARKETS[m]', ''), \
            f"audit() appears to write configuration: {forbidden}"
    assert {m: c["vol_max"] for m, c in P.MARKETS.items()} == before
    assert P.STRIKE_MULT == mult_before


def test_audit_is_registered_as_a_lambda_job():
    """It has to be runnable on a schedule to be an audit rather than a
    one-off. Registered monthly, deliberately not daily."""
    handler = open(os.path.join(
        os.path.dirname(os.path.dirname(S.__file__)), "..", "lambda_handler.py")).read()
    assert '"strangle_param_audit"' in handler
    assert "--audit" in handler


def test_width_grid_brackets_the_configured_multiple():
    """An audit that only tests widths near the current setting cannot detect
    that the setting has drifted to an edge."""
    assert min(S.WIDTH_GRID) < P.STRIKE_MULT < max(S.WIDTH_GRID)


def test_drift_is_reported_not_swallowed():
    """A scheduled audit whose failure looks like success is worthless — the
    exit code is what an alert hangs off."""
    src = open(S.__file__).read()
    assert 'return 1 if rep["drift"] else 0' in src
    assert 'if rep["drift"]:' in src
    assert "NOTHING WAS CHANGED" in src

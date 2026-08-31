"""A screen triggered from the UI must produce the SAME artifact as a cron run.

THE DEFECT, 31 Aug 2026. `worker._run_screen_job` — the path behind the desk's
"Run now" button — re-implemented the post-earnings-puts artifact inline and
never called `price_candidates`. So pressing the button pushed a board with:

  * no premium, yield, annualised yield, break-even, IV, delta or assignment
    probability, and
  * no `rule` / `evidence` block, which the desk renders underneath the table,

straight over a board that had all of them. The button made the screen strictly
worse, silently, and reported success.

This is the repo's dominant defect shape: nothing raises, two components quietly
disagree. The scheduled path and the button now call one `build_artifact`.

Also covers the wheel screen, added to the on-demand registry the same day. Its
launchd agent was retired in the 22 Aug desk cut and this does NOT resurrect it
— on demand is the whole point.
"""
from __future__ import annotations

import pytest

from tradepro_strategies.cli import post_earnings_puts as P
from tradepro_strategies.cli import worker as W


def test_the_ui_registry_names_both_screens():
    assert "post_earnings_puts" in W.UI_SCREENS
    assert "options_screen" in W.UI_SCREENS


def test_an_unknown_screen_names_the_ones_that_exist():
    """A typo in the button must not fail with a bare KeyError."""
    with pytest.raises(ValueError) as ei:
        W._run_screen_job("wheel", None)
    assert "post_earnings_puts" in str(ei.value)
    assert "options_screen" in str(ei.value)


def test_the_worker_no_longer_assembles_its_own_puts_artifact():
    """THE regression, asserted structurally: the worker must DELEGATE, not
    rebuild. A second inline artifact is exactly what drifted."""
    import inspect
    src = inspect.getsource(W._run_screen_job)
    assert "build_artifact" in src, "the worker must call the shared builder"
    assert '"kind": "post_earnings_puts"' not in src, (
        "the worker is assembling its own artifact again — that is the bug")


def test_the_shared_builder_prices_and_carries_the_evidence(monkeypatch):
    """Whatever calls it, the artifact has the priced fields AND the evidence
    block. The UI path lost both."""
    monkeypatch.setattr(P, "scan", lambda base: (
        [{"symbol": "MRVL", "strike": 194.96, "dte_target": 30}],
        [],
        {"ok": True, "reason": "SPY above its 200-day average"},
    ))

    def _fake_price(cands, base, token):
        cands[0].update(premium_usd=472.0, annual_yield_pct=29.4, delta=-0.22)
        return 1

    monkeypatch.setattr(P, "price_candidates", _fake_price)
    monkeypatch.setattr(
        "tradepro_strategies.cli.push_to_api.load_credentials",
        lambda: ("http://api.test", "tok"))

    art, cands, near = P.build_artifact("http://api.test", "tok")

    assert art["priced"] == 1, art
    assert cands[0]["premium_usd"] == 472.0
    # The half the UI path silently dropped.
    assert art["evidence"]["verdict"].startswith("PAPER FORWARD TEST"), art["evidence"]
    assert art["evidence"]["limits"], "the limits must travel with the numbers"
    assert art["rule"]["dte"] == P.DTE_TARGET


def test_pricing_failure_still_yields_a_usable_artifact(monkeypatch):
    """Pricing is additive. If the chain is dark the board must still publish
    its bars-derived strike and size rather than nothing."""
    monkeypatch.setattr(P, "scan", lambda base: (
        [{"symbol": "MRVL", "strike": 194.96}], [], {"ok": True, "reason": "x"}))

    def _boom(cands, base, token):
        raise RuntimeError("chain unavailable")

    monkeypatch.setattr(P, "price_candidates", _boom)
    monkeypatch.setattr(
        "tradepro_strategies.cli.push_to_api.load_credentials",
        lambda: ("http://api.test", "tok"))

    art, cands, _ = P.build_artifact("http://api.test", "tok")
    assert art["priced"] == 0
    assert cands[0]["strike"] == 194.96, "the bars-derived candidate must survive"
    assert art["evidence"], "evidence must still travel"

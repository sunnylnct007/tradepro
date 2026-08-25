"""The wheel FAILED its backtest, and every surface must say so.

25 Aug 2026. The nightly wheel email carried the label "backtested
(WHEEL_BACKTEST_GATES_V2.md — 8/9 gates pass, G4 open)" for TEN DAYS after the
v3 run returned FAIL — DO NOT FUND. The owner received a mail listing NKE and
others with strikes, premiums and annualised yields, under an evidence line
that was out of date AND known to have been wrong when it was current — v2's
"8/9 pass" was flattered because the harness never graded G4 on the full
sample, and G4 is the gate that fails.

He read it as a trade list. It was indistinguishable from one.

These tests pin the DECISION, not the wording: the recorded verdict and the
label the screen publishes must not be able to drift apart again.
"""
from __future__ import annotations

import json
from pathlib import Path

from tradepro_strategies.cli import options_screen as ws


def _recorded_verdict() -> str:
    p = Path(__file__).resolve().parents[1] / "backtests" / "studies.json"
    raw = json.loads(p.read_text())
    rows = raw if isinstance(raw, list) else raw.get("studies", raw)
    wheel = [r for r in rows if str(r.get("id", "")).startswith("wheel_")]
    assert wheel, "no wheel study recorded — the verdict must exist somewhere"
    return max(wheel, key=lambda r: r.get("date", ""))["verdict"]


def test_the_screen_states_the_verdict_that_was_actually_recorded():
    """ONE source of truth. The screen's constant and studies.json must agree,
    so a future re-grade cannot leave the email quoting a stale pass."""
    assert ws.WHEEL_VERDICT in _recorded_verdict()


def test_the_verdict_is_a_failure_and_is_worded_as_one():
    assert "DO NOT FUND" in ws.WHEEL_VERDICT.upper()


def test_the_published_evidence_line_does_not_claim_gates_passed():
    """The specific regression: an evidence string that reads like a pass."""
    ev = ws.WHEEL_EVIDENCE.lower()
    assert "do not fund" in ev
    # The old headline may APPEAR — explaining why a number was wrong is worth
    # more than deleting it — but never as a standing claim. If "8/9" is
    # present it must be accompanied by the reason it was flattered.
    if "8/9" in ev:
        assert "flattered" in ev, "the v2 headline may only appear with its retraction"
    assert "gates pass (" not in ev
    assert not ev.startswith("backtested"), \
        "the line must not OPEN by asserting it was validated"


def test_the_evidence_line_says_why_the_old_number_was_wrong():
    """A correction that does not explain itself gets reverted by whoever
    finds the old number in git history and assumes it was better."""
    ev = ws.WHEEL_EVIDENCE.lower()
    assert "g4" in ev and "flattered" in ev


def test_the_email_subject_cannot_read_as_a_trade_alert():
    """'3 eligible · best NKE' is a trade alert. The verdict has to be in the
    subject, because that is all a phone shows."""
    import inspect
    src = inspect.getsource(ws)
    assert '"[NOT FUNDED] TradePro Wheel' in src or "[NOT FUNDED]" in src

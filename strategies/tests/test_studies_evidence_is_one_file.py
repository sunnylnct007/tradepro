"""The evidence file has ONE copy. The UI cannot show a different story.

`strategies/backtests/studies.json` is where a study is recorded when it runs.
`frontend/src/data/studies.json` is what the Research screen imports. They are
the same document in two places, which is this repo's dominant failure mode
applied to the one artefact whose entire purpose is being checkable.

Found 29 Aug 2026: three studies run that day existed only in the strategies
copy, so the desk showed 9 studies while 12 had been run. Nothing raised — the
screen was simply out of date about what we had tested, which is worse than
having no screen, because it looks authoritative.

The owner's standing objection, the same day: *"i still want to see the result
and mechanism of all these backtest u d in background. i cant seee them from UI
and cant evidence it"*. A screen that silently lags the evidence does not
answer that.

The fix is a build-time copy plus this guard. If the frontend ever imports the
strategies file directly, delete this test — one file needs no guard.
"""
from __future__ import annotations

import json
import os

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SRC = os.path.join(ROOT, "strategies", "backtests", "studies.json")
UI = os.path.join(ROOT, "frontend", "src", "data", "studies.json")


def _load(p):
    if not os.path.exists(p):
        pytest.skip(f"{p} not present")
    with open(p) as fh:
        return json.load(fh)


def test_the_ui_shows_every_study_that_has_been_run():
    src, ui = _load(SRC), _load(UI)
    a = {s["id"] for s in src["studies"]}
    b = {s["id"] for s in ui["studies"]}
    assert not (a - b), (
        f"studies missing from the desk: {sorted(a - b)}. The Research screen "
        "would under-report what has been tested. Copy "
        "strategies/backtests/studies.json to frontend/src/data/studies.json."
    )
    assert not (b - a), (
        f"the desk shows studies that are not in the source of record: "
        f"{sorted(b - a)}"
    )


def test_no_study_says_two_different_things():
    src, ui = _load(SRC), _load(UI)
    s = {x["id"]: x for x in src["studies"]}
    u = {x["id"]: x for x in ui["studies"]}
    drifted = [i for i in s.keys() & u.keys() if s[i] != u[i]]
    assert not drifted, (
        f"{drifted} differ between the record and the screen. A verdict that "
        "reads one way in git and another on the desk is worse than no verdict."
    )


def test_every_study_carries_a_checkable_gate_commit():
    """The note in the file promises `git show <sha>` proves the gates predate
    the run. A study without one cannot be audited and its verdict is a claim."""
    for s in _load(SRC)["studies"]:
        assert s.get("gatesFile"), f"{s['id']} has no gates file"
        assert s.get("gatesCommit"), (
            f"{s['id']} has no gatesCommit — the pre-registration is unverifiable"
        )
        assert s.get("verdict"), f"{s['id']} has no verdict"


def test_failed_studies_are_kept():
    """Deleting failures turns the record into marketing. The file's own note
    says they are kept deliberately."""
    studies = _load(SRC)["studies"]
    failed = [s for s in studies if "FAIL" in s.get("verdict", "").upper()]
    assert failed, (
        "no failed studies on record — either nothing has ever failed, which is "
        "not credible, or failures are being pruned"
    )

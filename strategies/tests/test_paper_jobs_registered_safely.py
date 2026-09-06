"""Paper sleeves on Lambda — registered, but DISARMED.

Owner, 6 Sep 2026: "we are not leveraging olama so i would prefer all to
lambda", and immediately after: "ensure we do not create more regression".

The regression to avoid is specific and expensive. The Mac agents are STILL
RUNNING. `paper-swing-ibkr` and `paper-equity` place orders every 15 minutes
through the session. If the Lambda copy ran in `--placement-mode auto` with
`--push` while the Mac copy was live, the SAME SIGNAL WOULD BE PLACED TWICE —
two positions, double the size, on a strategy whose whole edge is ~1% a year.

So these are registered for MANUAL invocation only: manual placement mode, no
--push, and no EventBridge rule. They exist to be run by hand and compared
against the Mac's output. Auto mode and a schedule come only after that
comparison passes AND the Mac agent is unloaded — in that order.
"""
import pathlib
import re

HANDLER = next(
    p for p in pathlib.Path(__file__).resolve().parents
    if (p / "lambda_handler.py").exists()
) / "lambda_handler.py"
SRC = HANDLER.read_text()


def _entry(job: str) -> str:
    i = SRC.index(f'"{job}"')
    return SRC[i:i + 600]


def test_both_paper_sleeves_are_registered():
    for job in ("paper_swing_dryrun", "paper_equity_dryrun"):
        assert f'"{job}"' in SRC


def test_neither_can_place_automatically():
    # The single most expensive mistake available here.
    for job in ("paper_swing_dryrun", "paper_equity_dryrun"):
        e = _entry(job)
        assert '"manual"' in e, f"{job} must be in manual placement mode"
        assert '"auto"' not in e, f"{job} must NOT be armed while the Mac still runs"


def test_neither_pushes_a_ledger_that_would_collide_with_the_mac():
    for job in ("paper_swing_dryrun", "paper_equity_dryrun"):
        assert '"--push"' not in _entry(job)


def test_the_job_names_say_they_are_not_live():
    # A name is the only warning an operator sees in the invoke dialog.
    for job in ("paper_swing_dryrun", "paper_equity_dryrun"):
        assert job.endswith("_dryrun")


def test_the_existing_strangle_jobs_are_untouched():
    # Adding to the registry must not disturb what already runs. The strangle
    # keeps --place; that is the one sleeve already proven on Lambda.
    i = SRC.index('"index_strangle_paper"')
    e = SRC[i:i + 300]
    for flag in ('"--email"', '"--place"', '"--place-shadow"', '"--quote"'):
        assert flag in e, f"index_strangle_paper lost {flag}"

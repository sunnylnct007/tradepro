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
    """The job's OWN registry entry, bounded by the parser.

    Was SRC[i:i+600]. That window is not the entry — it is 600 characters of
    whatever follows, and on 20 Sep it began spilling into the next job's
    arguments when eight were added below. It still passed, purely because the
    new block opened with a long comment that pushed the next "--push" past
    600. A guard that holds by accident is worse than one that fails, so this
    now reads the actual dict value. Third time a fixed-width slice has been
    the wrong tool in this repo.
    """
    import ast
    tree = ast.parse(SRC)
    for node in ast.walk(tree):
        if isinstance(node, ast.Dict):
            for k, v in zip(node.keys, node.values):
                if isinstance(k, ast.Constant) and k.value == job:
                    return ast.get_source_segment(SRC, v) or ""
    raise AssertionError(f"job {job!r} is not in the JOBS registry")


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


# ── the evening block, moved off the laptop 20 Sep 2026 ──────────────

EVENING = ("earnings_harvest", "live_portfolio", "fill_replay",
           "today_setups_large50", "today_setups_highbeta",
           "signal_audit_equity", "signal_audit_equity_ibkr",
           "option_chain_capture")


def test_every_evening_job_is_registered():
    for job in EVENING:
        assert f'"{job}"' in SRC, f"{job} was not registered"


def test_each_evening_job_points_at_a_real_module():
    """A registry entry naming a module that does not import is a job that
    fails at 21:35 with nobody watching."""
    import importlib
    from lambda_handler import JOBS
    for job in EVENING:
        module, _ = JOBS[job]
        importlib.import_module(module)


def test_the_looping_scripts_became_one_job_per_iteration():
    """today-setups and signal-audit each looped over two values on the Mac.

    Registered as separate jobs, not a loop inside one: a loop that dies on
    item 1 silently takes item 2 with it, and one EventBridge rule cannot show
    that half of it failed.
    """
    from lambda_handler import JOBS
    assert "large_50" in JOBS["today_setups_large50"][1]
    assert "high_beta" in JOBS["today_setups_highbeta"][1]
    assert "ichimoku_equity" in JOBS["signal_audit_equity"][1]
    assert "ichimoku_equity_ibkr" in JOBS["signal_audit_equity_ibkr"][1]


def test_the_evening_jobs_keep_the_flags_the_mac_used():
    """These replace a working Mac agent. Different flags would be a silent
    behaviour change dressed as a migration."""
    from lambda_handler import JOBS
    for job in ("live_portfolio", "fill_replay",
                "today_setups_large50", "today_setups_highbeta",
                "signal_audit_equity", "signal_audit_equity_ibkr"):
        assert "--push" in JOBS[job][1], f"{job} lost --push; it would compute and discard"
    assert JOBS["option_chain_capture"][1] == ["--rights", "PC", "--strangle-dte", "7,21"]


def test_the_paper_sleeves_did_NOT_get_armed_by_this_change():
    """The evening batch moves; the order-placing sleeves do not. They stay in
    manual with no --push until a resting broker stop exists — a Lambda killed
    at 900s can place one leg and die, which a sleeping Mac cannot."""
    for job in ("paper_swing_dryrun", "paper_equity_dryrun"):
        e = _entry(job)
        assert '"manual"' in e
        assert '"--push"' not in e
        assert '"auto"' not in e

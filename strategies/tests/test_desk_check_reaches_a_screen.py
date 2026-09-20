"""The desk check must reach a SCREEN, not only a terminal.

Owner, 20 Sep 2026: "this is so frustrating. we shd be highlighting that on our
dashboard if we are not able to action certian things so we can fix it.
observability and diagnostic is key."

tradepro-desk-check already computed the right answer and could only print or
mail it. The first time it was run by hand it reported:

    [FAIL] mean_reversion_swing_ibkr: 91 orders · 12 reached the broker ·
           12 filled · exits 0/47 — it can OPEN positions and has never CLOSED one

Twelve positions open, forty-seven exit attempts, zero closes — and no screen
anywhere said so. A check whose output nobody sees is not observability.
"""
import ast
import pathlib

SRC = (pathlib.Path(__file__).resolve().parents[1]
       / "tradepro_strategies" / "cli" / "desk_check.py")


def _main_src() -> str:
    src = SRC.read_text()
    for n in ast.walk(ast.parse(src)):
        if isinstance(n, ast.FunctionDef) and n.name == "main":
            return ast.get_source_segment(src, n) or ""
    raise AssertionError("main() not found")


def test_the_check_can_publish():
    assert '"--push"' in _main_src(), "desk-check has no way to reach the desk"


def test_it_publishes_the_whole_check_list_not_just_a_headline():
    """The banner shows the failing lanes with their numbers. A verdict string
    alone would force a second call, or worse, a lane count with no detail."""
    m = _main_src()
    assert '"checks"' in m
    for field in ('"lane"', '"status"', '"detail"', '"fix"'):
        assert field in m, f"published checks drop {field}"


def test_it_publishes_BEFORE_it_mails():
    """Mail is the flakier leg — SMTP creds, a Lambda without them, a full
    mailbox. The screen must not go stale because the mailer had a bad night."""
    m = _main_src()
    assert m.index("args.push") < m.index("args.no_mail"), (
        "push must run before the mail block"
    )


def test_a_failure_to_publish_is_itself_shouted():
    """Silently failing to publish a failure is the exact shape this guards."""
    m = _main_src()
    i = m.index("args.push")
    seg = m[i:i + 1600]
    assert "could not be published" in seg


def test_worst_lane_first_so_the_banner_can_take_checks_zero():
    m = _main_src()
    assert "_SEVERITY_ORDER" in m
    from tradepro_strategies.cli.desk_check import _SEVERITY_ORDER, BROKEN, OK, WARN
    assert _SEVERITY_ORDER[BROKEN] < _SEVERITY_ORDER[WARN] < _SEVERITY_ORDER[OK]


# ── the banner's own rules ───────────────────────────────────────────

BANNER = (pathlib.Path(__file__).resolve().parents[2]
          / "frontend" / "src" / "components" / "desk" / "DeskHealthBanner.tsx")


def test_the_banner_is_silent_when_healthy():
    """A banner that is always on screen stops being read."""
    s = BANNER.read_text()
    assert "if (broken.length === 0 && warns.length === 0) return null;" in s


def test_an_absent_or_stale_check_is_NOT_rendered_as_a_pass():
    """'No news' and 'all clear' must never look the same — the single most
    repeated failure on this desk."""
    s = BANNER.read_text()
    assert "present === false" in s          # never published
    assert "STALE_HOURS" in s and "stale" in s
    assert "not as passing" in s or "not \"all clear\"" in s


def test_a_failed_fetch_says_so_rather_than_showing_nothing():
    s = BANNER.read_text()
    assert "Desk health unknown" in s


def test_the_banner_shows_the_numbers_not_just_a_verdict():
    s = BANNER.read_text()
    assert "c.detail" in s, "a lane name without its number is a mood, not a warning"

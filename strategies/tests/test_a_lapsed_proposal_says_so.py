"""A BUY we emailed must not quietly stop being true.

Owner, 19 Sep 2026: "i see PLTR in swingwatch but mail said to buy it". Both
were correct, which was the problem.

ORDER_PROPOSAL fires on a RECLAIM BAR — an event at a moment, not a standing
order. PLTR proposed BUY 25 @ ~175.58 LMT on 18 Sep, then ran to 177.64. The
limit never filled, the engine correctly stopped proposing rather than chasing,
and reverted to WAIT. Nothing anywhere said the proposal had lapsed.

An alert that can silently stop being true is the failure this desk keeps
paying for. These pin the lapse notice, its arithmetic, and the reason.
"""
import ast
import pathlib

SRC = (pathlib.Path(__file__).resolve().parents[1]
       / "tradepro_strategies" / "cli" / "preearnings_watch.py")


def _source() -> str:
    return SRC.read_text()


def _fn(name: str) -> str:
    src = _source()
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return ast.get_source_segment(src, node) or ""
    raise AssertionError(f"{name}() not found")


def test_an_open_proposal_is_remembered_between_ticks():
    src = _source()
    assert 'state["open_proposal"]' in src, (
        "nothing records that a proposal was made, so nothing can notice it lapsing"
    )


def test_the_lapse_is_an_alert_of_its_own():
    src = _source()
    assert '"PROPOSAL_LAPSED"' in src
    # Deduped on the PROPOSAL's timestamp, so one lapse mails exactly once
    # rather than every 5-minute tick forever.
    assert 'str(prop.get("at"))' in src


def test_the_lapse_names_its_cause_with_the_numbers():
    """'No longer valid' does not tell you if you missed a fill or dodged a loser."""
    src = _source()
    assert "never filled: price" in src
    assert "ABOVE the" in src and "limit" in src
    assert "setup invalidated: price" in src
    assert "below" in src and "stop the proposal was built on" in src


def test_the_row_keeps_what_the_engine_says_now():
    # Prepend, never replace. The lapse explains the past; WAIT/level info is
    # still the current answer and must survive.
    src = _source()
    assert 'f"{head} Now: {row.get(\'why\', \'\')}"' in src


def test_a_fresh_proposal_supersedes_rather_than_lapsing():
    src = _source()
    i = src.index('if action == "ORDER_PROPOSAL" and row:')
    j = src.index("elif prop and row:", i)
    branch = src[i:j]
    assert 'state["open_proposal"] = {' in branch, (
        "a new proposal must overwrite the stored one, not fall through to the "
        "lapse branch and mail a lapse alongside the replacement"
    )


def test_the_mail_card_tells_you_NOT_to_place_it():
    card = _fn("_mail_item")
    assert "PROPOSAL_LAPSED" in card
    assert "LAPSED" in card
    assert "Do NOT place the earlier proposal" in card
    # Severity 1, not 0: nothing needs doing, but it contradicts a mail already
    # sent, so it cannot be filed under "for information only" either.
    i = card.index("PROPOSAL_LAPSED")
    assert '"sev": 1' in card[i:i + 400]


def test_the_severity_heading_describes_the_band_not_one_alert():
    """'a level you set was hit' would be untrue above a lapsed proposal."""
    src = _source()
    assert "HEADS-UP — worth knowing, nothing to place" in src
    assert "HEADS-UP — a level you set was hit" not in src

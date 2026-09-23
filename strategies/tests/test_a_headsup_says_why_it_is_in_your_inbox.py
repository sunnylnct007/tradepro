"""A watch alert must say why the symbol is on your screen, and what to wait for.

Owner, 23 Sep 2026, on receiving "MRVL has run too far to chase":

    "now i never got email saying buy MRVL"

Exactly. The mail warned him off chasing a name it had never proposed, and
never said MRVL was on the watch list at all — so a do-not-chase read as a
warning about a trade nobody had offered. The band heading said "nothing to
place", which answers what to DO and not what this IS.

Second defect in the same mail: "Wait for its pullback zone" withheld the
number. The desk had already computed that level (prox_hi, the same one the
EMA20_PULLBACK_ZONE alert quotes) and did not print it, so the reader could not
set an alert on it. Standing rule: a warning must STATE the number.
"""
import re

from tradepro_strategies.cli.preearnings_watch import (
    _buy_zone_sentence, _mail_item)


MRVL = ("MRVL closed 260.90 on 2026-09-23 — at or above 257.12, which is 1.5x "
        "its daily range above the 20-day average (236.01). That is an "
        "extension, not an entry. Prices may have moved since that close. "
        "Its buy zone starts at 239.53, 8.9% below here — that is the level "
        "this watch is waiting for, and you will get a separate alert if it "
        "gets there.")


def test_the_advice_says_the_desk_did_NOT_propose_this():
    it = _mail_item("EXTENDED_DO_NOT_CHASE", "MRVL", MRVL)
    act = it["act"]
    assert "has NOT proposed buying this" in act, act
    assert "watch list" in act, act


def test_the_zone_sentence_quotes_MRVLs_real_numbers():
    # MRVL's own mail gives 20-day average 236.01 and an extended line of
    # 257.12 at 1.5x ATR, so ATR14 = 14.07 and the zone top is
    # 236.01 + 0.25 * 14.07 = 239.53 — 8.9% below the 260.90 close.
    ema, atr = 236.01, (257.12 - 236.01) / 1.5
    said = _buy_zone_sentence(ema + 0.25 * atr, 260.90)
    assert "239.53" in said, said
    assert "8.9% below here" in said, said


def test_the_zone_sentence_INVENTS_NOTHING_when_the_level_is_unknown():
    # An absent level must produce no sentence at all. A fabricated round
    # number here would be a price the owner could act on.
    assert _buy_zone_sentence(None, 260.90) == ""
    assert _buy_zone_sentence(0, 260.90) == ""
    # An unknown current price still allows the level itself to be stated.
    said = _buy_zone_sentence(239.53, None)
    assert "239.53" in said and "below here" not in said


def test_the_advice_no_longer_says_wait_for_an_unnamed_zone():
    # The old text was "Wait for its pullback zone." — a level with no number.
    it = _mail_item("EXTENDED_DO_NOT_CHASE", "MRVL", MRVL)
    assert "pullback zone." not in it["act"]
    assert "quoted above" in it["act"]


def test_the_body_carries_the_actual_level_to_wait_for():
    it = _mail_item("EXTENDED_DO_NOT_CHASE", "MRVL", MRVL)
    # A price, not a phrase: something the reader can set an alert on.
    assert re.search(r"buy zone starts at \d+\.\d\d", it["body"]), it["body"]


def test_every_band_states_why_those_names_are_there():
    # The why-lines live beside SEV_LABEL in main()'s mail composer; assert on
    # the module source's STRUCTURE rather than re-running the whole CLI, but
    # assert the dict is real and complete rather than that a phrase appears.
    import ast
    import inspect
    from tradepro_strategies.cli import preearnings_watch as W

    tree = ast.parse(inspect.getsource(W))
    found = {}
    for node in ast.walk(tree):
        if (isinstance(node, ast.Assign)
                and any(getattr(t, "id", None) == "SEV_WHY" for t in node.targets)
                and isinstance(node.value, ast.Dict)):
            for k, v in zip(node.value.keys, node.value.values):
                key = ast.literal_eval(k)
                found[key] = ast.literal_eval(v) if isinstance(v, ast.Constant) \
                    else "".join(ast.literal_eval(p) for p in v.values) \
                    if isinstance(v, ast.JoinedStr) else str(v)
    assert set(found) == {0, 1, 2}, f"a band with no explanation: {found.keys()}"
    # The HEADS-UP band is the one that confused the owner: it must deny being
    # a proposal, in words, not merely omit the claim.
    assert "not a proposal" in found[1].lower().replace("—", "")
    assert "watch" in found[1].lower()
    # And the ACTION band must still own the proposals, or the denial above
    # would leave the reader unable to tell where a real order appears.
    assert "proposal" in found[0].lower()

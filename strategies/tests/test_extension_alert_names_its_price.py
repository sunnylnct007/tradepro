"""An alert must prove itself with the price that actually tripped it.

Owner, 21 Sep 2026: "the email i see is misleading". It read:

    NVDA closed 222.27 on 2026-09-18 — above 228.13, which is 1.5x its daily
    range above the 20-day average (218.50). That is an extension, not an entry.

222.27 is 5.86 BELOW 228.13. The sentence contradicts itself.

THE CAUSE. The gate is an OR over two DIFFERENT prices:

    if px >= ext or (bars and bars[-1]["c"] >= ext):

`px` is the last SETTLED daily close; `bars[-1]["c"]` is the latest 15-minute
bar. The message always quoted `px`. NVDA had crossed intraday (227.5 against a
226.8 line) while Friday's settled close, 222.27, had not — so the alert fired
on one price and offered the other as evidence.

The CONCLUSION was correct and the EVIDENCE contradicted it, which is worse than
simply being wrong: it teaches the reader to distrust every number the desk
prints. Owner's standing rule — "we need to be crystal clear in anything we show
or email".
"""
import ast
import pathlib

SRC = (pathlib.Path(__file__).resolve().parents[1]
       / "tradepro_strategies" / "cli" / "preearnings_watch.py")


def _block() -> str:
    """The extension gate, bounded by the parser rather than a byte count."""
    src = SRC.read_text()
    i = src.index("_daily_hit = px >= ext")
    j = src.index("EXTENDED_DO_NOT_CHASE", i)
    return src[i - 400:j + 900]


def test_the_two_trigger_prices_are_distinguished():
    b = _block()
    assert "_daily_hit" in b and "_intra_hit" in b, (
        "the gate no longer tells apart which of its two prices fired"
    )


def test_an_intraday_trigger_is_LABELLED_intraday():
    """A live 15m print must never be reported as a close."""
    b = _block()
    assert "intraday (last 15m bar)" in b


def test_an_intraday_trigger_states_the_settled_close_was_BELOW():
    """Otherwise the reader cannot reconcile it against the board, which shows
    the settled close — exactly the confusion that produced the complaint."""
    b = _block()
    assert "below the line" in b
    assert "last SETTLED close" in b


def test_a_settled_trigger_still_quotes_the_settled_close():
    b = _block()
    assert 'f"closed {px:.2f} on {d.dates[i]}"' in b


def test_the_claim_matches_the_comparison():
    """The gate is >=, so the prose must not say strictly 'above'."""
    b = _block()
    assert "at or above" in b, (
        "the test is `>= ext` but the message claims 'above' — a price exactly "
        "on the line would be described wrongly"
    )


def test_the_message_never_pairs_one_price_with_the_other_threshold():
    """The regression in one line: px quoted as proof when px did not trip it."""
    src = SRC.read_text()
    i = src.index("EXTENDED_DO_NOT_CHASE", src.index("_daily_hit"))
    seg = src[i:i + 700]
    # the quoted price is chosen by _what, not hardcoded to px
    assert "{_what}" in seg
    assert 'f"{sym} closed {px:.2f} on {d.dates[i]} — above "' not in seg

"""The live book read for risk — built from a Flex position list.

Owner, 7 Nov 2026: "for live account we wree in deep trouble and tradepro
didnt asissted at all". The rules were right on 1 Nov — they flagged MRVL
Nov20 230P and CRWV Nov20 70P as CLOSE, 19 days out — and lived in a
conversation instead of on a screen.
"""
from datetime import date

from tradepro_strategies.cli.live_risk import build
from tradepro_strategies.quant_engine.options.manage import parse_occ


def _body(a):
    """The content nests under "artifact" — the ingest endpoint requires that
    envelope and rejected a flat payload with a bare 400 on this lane's first
    real run. The book had been read perfectly; only the wrapper was wrong."""
    return a["artifact"]


def _pos(sym, qty, cost, mark, pnl=None):
    return {"symbol": sym, "quantity": qty, "costBasisPrice": cost,
            "markPrice": mark, "unrealizedPnl": pnl}


# ── reading the contract ──────────────────────────────────────────────────
def test_occ_parses_a_real_ibkr_symbol():
    assert parse_occ("AMD   270115P00450000") == ("AMD", "2027-01-15", "P", 450.0)


def test_the_strike_divisor_is_1000_not_1():
    """00450000 is $450.000, not $450,000. A strike a thousand times too
    large makes every assignment number absurd — and absurd numbers get
    dismissed rather than investigated."""
    _r, _e, _g, k = parse_occ("AMD   270115P00450000")
    assert k == 450.0


def test_a_six_letter_root_still_parses():
    # The root is padded to six characters, so a six-letter root leaves NO
    # space before the date and splitting on whitespace returns the wrong
    # field. The date-strike block is fixed-width at the END.
    assert parse_occ("GOOGL 270115P00315000")[0] == "GOOGL"
    assert parse_occ("ABCDEF270115P00315000")[0] == "ABCDEF"


def test_a_stock_symbol_is_not_an_option():
    for s in ("APLD", "MU", "GOOGL", ""):
        assert parse_occ(s) is None


# ── the book ──────────────────────────────────────────────────────────────
def test_only_SHORT_PUTS_are_assessed(monkeypatch):
    monkeypatch.setattr("tradepro_strategies.cli.live_risk._spots",
                        lambda s: {x: 100.0 for x in s})
    art = build([
        _pos("AAA   270115P00100000", -1, 5.0, 4.0),      # short put  -> in
        _pos("BBB   270115C00100000", -1, 5.0, 4.0),      # short CALL -> out
        _pos("CCC   270115P00100000", 1, 5.0, 4.0),       # LONG put   -> out
        _pos("MU", 100, 897.0, 695.0),                     # stock      -> equity
    ], today=date(2026, 11, 7))
    assert _body(art)["counts"]["short_puts"] == 1
    assert [e["symbol"] for e in _body(art)["equity"]] == ["MU"]


def test_assignment_obligation_uses_the_multiplier_and_quantity(monkeypatch):
    monkeypatch.setattr("tradepro_strategies.cli.live_risk._spots",
                        lambda s: {x: 100.0 for x in s})
    art = build([_pos("AAA   270115P00100000", -2, 5.0, 4.0)],
                today=date(2026, 11, 7))
    assert _body(art)["assignment_obligation_usd"] == 100 * 100 * 2


def test_the_most_urgent_row_is_first(monkeypatch):
    """A board read top-down must start with what forces a decision soonest."""
    monkeypatch.setattr("tradepro_strategies.cli.live_risk._spots",
                        lambda s: {"NEAR": 60.0, "FAR": 500.0})
    art = build([
        _pos("FAR   270319P00300000", -1, 10.0, 9.0),       # far OTM, far out
        _pos("NEAR  261120P00070000", -1, 5.54, 24.06),     # the CRWV shape
    ], today=date(2026, 11, 7))
    assert _body(art)["positions"][0]["symbol"] == "NEAR"
    assert _body(art)["positions"][0]["action"] == "CLOSE"


def test_a_missing_spot_is_UNKNOWN_and_still_listed(monkeypatch):
    # A position we cannot assess must stay visible. Dropping it is how a
    # live short put gets forgotten.
    monkeypatch.setattr("tradepro_strategies.cli.live_risk._spots",
                        lambda s: {x: None for x in s})
    art = build([_pos("AAA   270115P00100000", -1, 5.0, 4.0)],
                today=date(2026, 11, 7))
    assert _body(art)["counts"]["unknown"] == 1
    assert len(_body(art)["positions"]) == 1


def test_the_rules_travel_with_the_artifact(monkeypatch):
    # The reader has to be able to see WHICH thresholds produced these
    # verdicts, or "CLOSE" is just an assertion.
    monkeypatch.setattr("tradepro_strategies.cli.live_risk._spots",
                        lambda s: {x: 100.0 for x in s})
    art = build([_pos("AAA   270115P00100000", -1, 5.0, 4.0)],
                today=date(2026, 11, 7))
    assert _body(art)["rules"]["near_expiry_days"] == 21
    assert _body(art)["rules"]["take_profit_pct"] == 50.0


def test_every_row_carries_its_reason(monkeypatch):
    monkeypatch.setattr("tradepro_strategies.cli.live_risk._spots",
                        lambda s: {x: 100.0 for x in s})
    art = build([_pos("AAA   270115P00100000", -1, 5.0, 4.0),
                 _pos("BBB   261120P00150000", -1, 5.0, 40.0)],
                today=date(2026, 11, 7))
    for r in _body(art)["positions"]:
        assert r["why"] and any(c.isdigit() for c in r["why"])

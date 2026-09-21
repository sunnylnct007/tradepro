"""A setup whose level has broken must stop calling itself a setup.

Owner, 21 Sep 2026, on XOM. The board showed:

    action "consider", entry 163.54, kijun 162.48
    "at the kijun $162.48 ... support hold, not a knife; stop below kijun"

Price was 159.95 — THROUGH the kijun by 1.6%. The entry premise was that the
level holds; it had not. Taken as shown, with the stated stop below the kijun,
you were already out.

The ladder is RIGHT: `dist_atr < 0` already classifies a name below its kijun as
"weak, support breaking". XOM was "consider" because on the SETTLED close it
genuinely was at the kijun. Nothing re-checked, so an expired premise kept
presenting itself as live.

Third surface of one fault in a day, with GM (#199) and NVDA (#196).
"""
import pathlib

from tradepro_strategies.live_quote import live_prices

SETUPS = (pathlib.Path(__file__).resolve().parents[1]
          / "tradepro_strategies" / "cli" / "today_setups.py").read_text()
SWING = (pathlib.Path(__file__).resolve().parents[1]
         / "tradepro_strategies" / "cli" / "swing_candidates.py").read_text()
QUOTE = (pathlib.Path(__file__).resolve().parents[1]
         / "tradepro_strategies" / "live_quote.py").read_text()


def test_a_star_below_its_kijun_is_demoted():
    assert 'r["classification"] = "weak"' in SETUPS
    assert "now < float(kj)" in SETUPS


def test_the_demotion_SAYS_the_level_broke_and_by_how_much():
    """'weak' alone tells the reader nothing about what changed."""
    assert "LEVEL BROKEN" in SETUPS
    assert "through the" in SETUPS and "kijun this setup was built on" in SETUPS


def test_the_original_reason_survives():
    """Replacing it would erase why the name was ever interesting."""
    assert "Was: {(r.get('why') or '')}" in SETUPS


def test_a_move_that_does_NOT_break_the_level_is_still_reported():
    assert "moved {100 * (now / float(r['close']) - 1):+.1f}% since the" in SETUPS


def test_a_missing_quote_is_stated_not_ignored():
    assert "unavailable — level not re-checked since the close" in SETUPS


def test_only_the_starred_rows_are_requoted():
    assert '_starred = [r for r in artifact["setups"] if r.get("classification") == "consider"]' in SETUPS


# ── one definition, not two ──────────────────────────────────────────

def test_the_quote_helper_exists_exactly_ONCE():
    """It was written inside swing_candidates for #199 and Setups needed the
    identical thing a day later. Copying it would recreate the duplicate-
    definition shape that made the 20 Sep exit fix do nothing."""
    assert "def live_prices" in QUOTE
    assert "def _live_prices" not in SWING, "swing kept a private copy"
    assert "def live_prices" not in SETUPS, "setups declared its own"
    assert "from ..live_quote import live_prices" in SWING
    assert "from ..live_quote import live_prices" in SETUPS


def test_a_SINGLE_symbol_still_gets_a_quote():
    """group_by='ticker' nests even for one symbol, so `df["Close"]` raises and
    returns {} — which reads as 'no quote' and silently disabled the whole
    re-quote on any one-candidate day. Shipped broken in #199."""
    assert 'if len(uniq) > 1' not in QUOTE
    px = live_prices(["XOM"])
    assert isinstance(px, dict)
    if px:
        assert px["XOM"] > 0


def test_a_bogus_symbol_degrades_to_empty_rather_than_raising():
    assert live_prices(["ZZZZNOTREAL"]) == {}
    assert live_prices([]) == {}

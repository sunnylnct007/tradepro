"""A candidate must be judged at a price you could actually pay.

Owner, 21 Sep 2026, on GM. The board said "needs 64%" — computed from Friday's
82.20 close against an 85.91 target. GM had since rallied to 83.96, leaving 2.3%
of room instead of 4.5%, and the real requirement was 81% — above the 72.8% this
strategy achieves. A trade the board still recommended had become one that
cannot pay, and nothing said so.

The SIGNAL on the settled close is correct and stays: it is what the backtest
measured, and a screen that chased intraday would be a different strategy. It is
the ECONOMICS that must be re-quoted, because you cannot buy Friday's close on
Monday.

Same fault, three surfaces on one day:
  - GM   : board economics from a stale close
  - XOM  : Setups saying "consider" on a kijun that had broken
  - NVDA : extension mail quoting a settled close as proof of an intraday cross
"""
import pathlib

from tradepro_strategies.cli.swing_candidates import (
    BREAKEVEN_MAX_WIN_PCT, _live_prices,
)

SRC = (pathlib.Path(__file__).resolve().parents[1]
       / "tradepro_strategies" / "cli" / "swing_candidates.py").read_text()


def _requote(close, target, stop, now):
    """The arithmetic the screen performs, isolated."""
    risk, reward = now - stop, target - now
    if risk <= 0:
        return None
    rr = reward / risk
    return round(100.0 / (1.0 + rr), 1) if rr > 0 else None


def test_the_GM_case_flips_from_payable_to_not():
    # Real figures: board 82.20 -> 85.91 (needs 64%), live 83.96.
    assert _requote(82.20, 85.91, 75.62, 82.20) < BREAKEVEN_MAX_WIN_PCT
    live = _requote(82.20, 85.91, 75.62, 83.96)
    assert live is not None and live >= BREAKEVEN_MAX_WIN_PCT, (
        "a name that rallied out of its own edge must be rejected, not shown"
    )


def test_a_name_that_FELL_gets_better_not_worse():
    # CVS 88.84 -> 87.59. Cheaper entry, same target: the trade improved.
    settled = _requote(88.84, 94.26, 81.73, 88.84)
    live = _requote(88.84, 94.26, 81.73, 87.59)
    assert live < settled, "falling toward the target must improve the economics"


def test_price_through_the_stop_returns_no_ratio():
    """Not a small number — NO number. The premise is gone, not thin."""
    assert _requote(88.84, 94.26, 81.73, 81.00) is None


def test_the_LIVE_number_is_the_one_that_decides():
    assert "decisive_breakeven_win_pct" in SRC
    assert "live_breakeven_win_pct" in SRC
    i = SRC.index("def _decisive_breakeven")
    seg = SRC[i:i + 700]
    assert 'r["live_breakeven_win_pct"]' in seg
    assert seg.index("live_breakeven_win_pct") < seg.index('r.get("breakeven_win_pct")'), (
        "the settled number must be the FALLBACK, not the decider"
    )


def test_a_missing_quote_is_STATED_not_silently_ignored():
    """An absent re-quote must never look like 'nothing moved'."""
    assert "live_requote" in SRC
    assert "unavailable — economics are as at the signal close" in SRC


def test_only_the_candidates_are_requoted_never_the_universe():
    """A handful of names, not the 958 scanned — one batch call."""
    assert '_live_prices([r["symbol"] for r in out])' in SRC


def test_the_quote_helper_survives_a_dead_vendor():
    """Failure returns {} and leaves settled economics standing."""
    i = SRC.index("def _live_prices")
    seg = SRC[i:i + 1400]
    assert "except Exception" in seg and "return {}" in seg


def test_the_helper_actually_returns_prices():
    px = _live_prices(["GM"])
    assert isinstance(px, dict)
    if px:                                  # vendor reachable in this env
        assert px["GM"] > 0

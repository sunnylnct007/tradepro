"""The MCP rule description must be READ from the code, never restated.

Owner, 27 Sep 2026: *"ensure our logic shd be exposed via mcp tool so any agent
can cross check what we doing for any of our strategy"*.

A cross-check is only worth having if it cannot agree with itself while
disagreeing with reality. A tool that retypes the constants drifts from the
code and then gives a confident, wrong answer — worse than no tool at all.

This desk has watched the description and the rule diverge twice already:
momentum's gates were measured on 256 symbols while its screen runs 956, and
its board said "pullback" for an entry that admits a close 0.5% ABOVE the
average. Both were true statements about something other than the live rule.
"""
from __future__ import annotations

from tradepro_strategies.mcp.tools import get_strategy_rules
from tradepro_strategies.signals import mean_reversion as MR
from tradepro_strategies.signals import momentum_pullback as MOM


def test_every_swing_constant_is_the_live_one():
    c = get_strategy_rules("swing")["rule"]["constants"]
    assert c["sigma"] == MR.SIGMA
    assert c["band_window"] == MR.BB_WINDOW
    assert c["trend_window"] == MR.TREND_WINDOW
    assert c["stop_pct"] == MR.STOP_PCT
    assert c["max_hold_sessions"] == MR.MAX_HOLD


def test_every_momentum_constant_is_the_live_one():
    c = get_strategy_rules("momentum")["rule"]["constants"]
    assert c["stop_pct"] == MOM.STOP_PCT
    assert c["trail_from_peak"] == MOM.TRAIL_FROM_PEAK
    assert c["max_hold_sessions"] == MOM.MAX_HOLD


def test_changing_a_rule_changes_what_the_tool_reports(monkeypatch):
    """The actual anti-drift property: move the rule, the description moves."""
    monkeypatch.setattr(MR, "SIGMA", 9.99)
    assert get_strategy_rules("swing")["rule"]["constants"]["sigma"] == 9.99, (
        "the tool restated the constant instead of reading it — it would keep "
        "describing the old rule after the rule changed")


def test_the_universe_mismatch_is_disclosed_not_buried():
    """Momentum's headline was measured on 256 symbols and it screens ~956.
    A reviewer must not have to know that already to find it."""
    r = get_strategy_rules("momentum")
    assert r["rule"]["measured_on"]["symbols"] == 256
    assert r["live_universe_symbols"] > 500
    joined = " ".join(r["rule"]["caveats"]).upper()
    assert "256" in joined and "FAILS G5" in joined, (
        "the universe mismatch and the failed tail gate must both be stated in "
        "the caveats, not left for the reader to discover")


def test_the_wheel_says_it_failed():
    r = get_strategy_rules("wheel")["rule"]
    assert "FAILED" in r["status"].upper() and "NOT FUNDED" in r["status"].upper()
    assert r["headline"]["worst_name_drawdown_pct"] == -71.4


def test_an_unknown_strategy_is_a_gap_not_an_endorsement():
    r = get_strategy_rules("does_not_exist")
    assert "error" in r and "known" in r
    assert "gap" in r["note"].lower(), (
        "absence must read as 'no rule exposed', never as 'nothing to worry "
        "about'")

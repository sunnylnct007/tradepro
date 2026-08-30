"""The eight-market expansion must not reintroduce the repo's dominant bug.

Going from 2 markets to 8 is exactly the moment a config splits into several
dicts that quietly disagree. These tests pin the properties that make that
impossible to do silently, plus the two claims the daily email makes about
itself: that its evidence covers every market it can print, and that it always
carries the number that would talk a reader OUT of the trade.
"""
from __future__ import annotations

import json
import os

import pytest

from tradepro_strategies.cli import index_strangle_paper as P


def test_per_market_values_have_exactly_one_definition():
    """VIX_MAX/RATE/STRIKE_GRID must DERIVE from MARKETS, never restate it.

    Before this, a market's threshold lived in both MARKETS and VIX_MAX. Nothing
    raises when two such dicts disagree — the screen simply gates on one number
    while the email explains a different one, which is the failure shape that has
    cost this repo more time than any other.
    """
    for m, cfg in P.MARKETS.items():
        assert P.VIX_MAX[m] == cfg["vol_max"], m
        assert P.RATE[m] == cfg["rate"], m
        assert P.STRIKE_GRID[m] == cfg["grid"], m
    assert set(P.VIX_MAX) == set(P.MARKETS)


def test_every_market_row_is_complete():
    """A market missing a key fails at render time, inside a Lambda, at 04:00."""
    required = {"index", "vol", "vol_scale", "vol_max", "rate", "grid",
                "lot", "family", "ccy", "product", "note"}
    for m, cfg in P.MARKETS.items():
        assert required <= set(cfg), f"{m} missing {required - set(cfg)}"
        assert cfg["vol_scale"] > 0 and cfg["grid"] > 0 and cfg["lot"] > 0, m


def test_markets_sharing_an_underlying_share_a_family():
    """SPX/XSP/SPY are one bet at three sizes; the email groups on `family` to
    say so. If a new market is added with the wrong family, the email would
    present correlated positions as independent ones — the specific mistake that
    kills a premium-selling account."""
    by_index: dict[str, set[str]] = {}
    for m, cfg in P.MARKETS.items():
        by_index.setdefault(cfg["index"], set()).add(cfg["family"])
    for index, fams in by_index.items():
        assert len(fams) == 1, f"{index} spans families {fams}"
    # And the S&P trio must actually be grouped, since that is the case the
    # grouping exists for.
    assert len({P.MARKETS[m]["family"] for m in ("SPX", "XSP", "SPY")}) == 1


def test_simulator_prices_the_strikes_the_email_prints():
    """One source of truth for the strike rule.

    The simulator must IMPORT strike_pair rather than recompute it. When a
    backtest and a live screen each own their own copy of the entry rule they
    drift, and the published evidence stops describing the traded thing — which
    is exactly the harness-vs-screen mismatch already sitting in the Swing
    numbers.
    """
    src = open(os.path.join(os.path.dirname(P.__file__),
                            "index_strangle_sim.py")).read()
    assert "strike_pair" in src and "from .index_strangle_paper import" in src
    # And it must not have grown its own copy of the forward maths.
    assert "math.exp(rate" not in src, "simulator recomputes the forward itself"


def test_strikes_are_on_the_listed_grid_and_straddle_the_forward():
    for m, cfg in P.MARKETS.items():
        put, call, fwd = P.strike_pair(1000.0, 0.02, 7, cfg["rate"], cfg["grid"])
        assert put < fwd < call, m
        for k in (put, call):
            assert abs(k / cfg["grid"] - round(k / cfg["grid"])) < 1e-9, (m, k)


def test_evidence_file_covers_every_market():
    """The email quotes committed simulation output. A market present in the
    screen but absent from the evidence renders a position with no numbers
    behind it — the exact 'trust me' row this email exists to avoid."""
    ev = P._evidence()
    assert ev, "evidence file missing or unreadable"
    missing = set(P.MARKETS) - set(ev)
    assert not missing, f"no simulation evidence for {sorted(missing)}"


def test_evidence_carries_the_gate_failure_stress():
    """Win rate without the cost of the losses is a mis-sold product."""
    for m, e in P._evidence().items():
        s = e.get("stress") or {}
        assert s.get("worst_ungated_pct") is not None, m
        # The stress must be genuinely worse than anything the gate allowed,
        # else it is not measuring gate failure at all.
        assert s["worst_ungated_pct"] < e["historical"]["worst_pct"], m


@pytest.mark.parametrize("part", [
    "What would make this wrong",     # the honest section must exist
    "How to read this",               # the reading guide
    "not a worst case",               # the Monte Carlo caveat, stated inline
    "upper bound",                    # what "caught in a crash" actually means
    "no bid-ask spread charged",      # the modelled-price admission
    "NOT FUNDED",
])
def test_email_always_carries_its_own_caveats(part, monkeypatch):
    """These are load-bearing. An email that shows an 88% win rate and drops the
    section explaining what the other 12% costs is a different product from the
    one this code was reviewed as."""
    rows = [{"market": "GOLD", "index": "GLD", "status": "stand aside",
             "reason": "test", "spot": 400.0, "family": "Gold", "ccy": "$",
             "product": "ETF option", "vol_index": 25.0, "vol_threshold": 16.0,
             "width_pct": 2.4, "iv_used": 25.0, "expected_daily_move_pct": 1.6,
             "lot": 100, "legs": {}}]
    _subj, (text, html) = P._email_body(rows)
    assert part in text or part in html


def test_stand_aside_days_are_never_silently_dropped():
    """A stand-aside row is evidence about the threshold. Hiding it turns the
    email into a highlight reel and makes the gate untestable."""
    rows = [{"market": "GOLD", "index": "GLD", "status": "stand aside",
             "reason": "GVZ 25.17 above the 16.0 gate", "spot": 400.0,
             "family": "Gold", "ccy": "$", "product": "ETF option",
             "vol_index": 25.0, "vol_threshold": 16.0, "width_pct": 2.4,
             "iv_used": 25.0, "expected_daily_move_pct": 1.6, "lot": 100,
             "legs": {}}]
    subj, (text, html) = P._email_body(rows)
    assert "GOLD" in text and "GOLD" in html
    assert "above the 16.0 gate" in text
    assert "0 of 1" in subj


def test_monte_carlo_block_bootstrap_beats_iid_on_drawdown():
    """The blocked bootstrap must be the reported one because it is the more
    pessimistic model of drawdown for a strategy whose losses cluster. If this
    ever inverts across the board, the block length has stopped doing anything
    and the headline drawdown is understated."""
    ev = P._evidence()
    worse_or_equal = sum(1 for e in ev.values()
                         if e["mc_blocked"]["p95_max_drawdown_pct"]
                         <= e["mc_iid"]["p95_max_drawdown_pct"] + 1e-9)
    assert worse_or_equal >= len(ev) // 2, (
        "clustering no longer deepens the simulated drawdown anywhere — "
        "check MC_BLOCK")


def test_rejected_markets_stay_rejected():
    """Russell and Dow have NO usable volatility index (^RVX returns zero bars,
    ^VXD one). NIFTY MIDCAP was measured and lost. Re-adding any of them needs
    new data, not a new opinion — this test is the tripwire."""
    banned = {"^RUT", "^DJI", "^NSEMDCP50", "IWM", "DIA"}
    used = {c["index"] for c in P.MARKETS.values()}
    assert not (used & banned), f"re-added a rejected market: {used & banned}"


def test_thresholds_are_the_rules_output_not_a_judgement():
    """Every `vol_max` must equal what `choose_threshold` returns.

    The thresholds used to be a mix: SPY's 14 and India's 12 came from a
    documented sweep, while VXN<=18 and GVZ<=16 were picked and justified
    afterwards. One of the guesses was wrong — GVZ<=16 traded through 31
    sessions of the 2022 bear and 4 of COVID, which is a gate failing at the one
    job it has.

    The rule ("largest threshold on a half-point grid admitting ZERO trades in
    any declared crisis window") independently reproduces SPY's 14, which is why
    it is trusted over judgement. This test is what stops the next person -
    including me - nudging a threshold up because the sample felt thin.

    Checked against the COMMITTED evidence file, so it needs no network.
    """
    ev = P._evidence()
    assert ev, "evidence file missing"
    for m, e in ev.items():
        rule = e.get("threshold_rule") or {}
        assert rule.get("status") == "ok", f"{m}: rule never ran"
        assert rule["chosen"] is not None, f"{m}: rule found no clean threshold"
        assert P.MARKETS[m]["vol_max"] == rule["chosen"], (
            f"{m}: configured {P.MARKETS[m]['vol_max']} but the rule says "
            f"{rule['chosen']} — change the rule or the windows, not the number")


def test_chosen_threshold_admits_no_crisis_trades():
    """The property the rule exists to guarantee, asserted directly against the
    persisted working rather than trusting the rule's own summary."""
    for m, e in P._evidence().items():
        rule = e["threshold_rule"]
        row = next((g for g in rule["grid"]
                    if g["threshold"] == rule["chosen"]), None)
        assert row is not None, m
        assert row["leaks"] == {}, f"{m}: chosen gate leaks {row['leaks']}"


def test_one_step_looser_would_have_leaked():
    """The chosen threshold must be the LARGEST clean one.

    Without this, the rule silently degenerates into 'any clean threshold', and
    the frequency it is meant to maximise - the scarce resource that made the US
    sample too thin to forward-test - gets given away for nothing.
    """
    for m, e in P._evidence().items():
        rule = e["threshold_rule"]
        looser = [g for g in rule["grid"] if g["threshold"] > rule["chosen"]]
        if not looser:
            continue
        assert looser[0]["leaks"], (
            f"{m}: {looser[0]['threshold']} is also clean — chosen "
            f"{rule['chosen']} is not the largest clean gate")


def test_economics_never_shows_a_gain_without_its_loss():
    """Money figures are the most persuasive thing in the email and the easiest
    to mis-sell. Any row quoting a credit or a typical gain MUST also carry the
    worst day and the gate-failure loss."""
    ev = P._evidence()
    for m in P.MARKETS:
        row = {"market": m, "spot": 1000.0, "iv_used": 12.0, "lot": P.MARKETS[m]["lot"],
               "ccy": P.MARKETS[m]["ccy"],
               "legs": {"weekly": {"dte": 7, "put_strike": 990.0,
                                   "call_strike": 1010.0, "forward": 1001.0}}}
        e = P.economics(row, ev.get(m))
        assert e is not None, m
        for k in ("credit_modelled", "typical_gain", "worst_day", "gate_failure",
                  "margin_estimate", "winners_per_gate_failure"):
            assert e.get(k) is not None, f"{m} missing {k}"
        assert e["worst_day"] < 0 and e["gate_failure"] < 0, m
        assert e["gate_failure"] < e["worst_day"], m
        # The whole point: a gate failure must cost many winners, and the email
        # says so. If this ratio ever came back near 1 the strategy would be
        # something else entirely and the copy would be wrong.
        assert e["winners_per_gate_failure"] > 10, m


def test_margin_basis_is_labelled_as_an_estimate():
    """Quoting a broker margin as fact invites someone to size a position on it."""
    src = open(P.__file__).read()
    assert "MARGIN_PCT" in src
    assert "ESTIMATE" in src or "estimate" in src
    ev = P._evidence()
    row = {"market": "GOLD", "spot": 400.0, "iv_used": 12.0, "lot": 100, "ccy": "$",
           "legs": {"weekly": {"dte": 7, "put_strike": 390.0,
                               "call_strike": 410.0, "forward": 400.3}}}
    e = P.economics(row, ev.get("GOLD"))
    assert e["margin_pct_assumed"] == P.MARGIN_PCT


def test_the_gate_never_sees_the_day_it_trades():
    """No lookahead in the volatility filter.

    The trade is entered at the OPEN, so the gate may only use the PREVIOUS
    session's vol close. Until 29 Aug 2026 the backtest gated on the same day's
    close — letting the filter see the very move it exists to avoid, and
    silently excluding the days that would have hurt. Mean return fell 10-17%
    when corrected and SPY's worst day went -0.80% -> -1.89%.

    This asserts on the source because the property lives in how the series is
    built, and a numeric check would need the network.
    """
    src = open(os.path.join(os.path.dirname(P.__file__),
                            "index_strangle_sim.py")).read()
    assert src.count('j["V"] = j["V"].shift(1)') >= 2, (
        "the one-session lag is missing from trade_returns or choose_threshold "
        "— the gate would be using information it cannot have at entry")


def test_published_worst_days_are_the_lagged_ones():
    """A sanity floor on the evidence file.

    With the lag applied, the S&P family's worst day is around -2%. If the
    evidence is ever regenerated WITHOUT the lag those worst days collapse back
    to about -0.8%, which reads as a much safer strategy than the one being
    traded. This catches that regression at the number, not the source.
    """
    ev = P._evidence()
    for m in ("SPX", "SPY", "XSP"):
        if m not in ev:
            continue
        assert ev[m]["historical"]["worst_pct"] < -1.0, (
            f"{m} worst day is {ev[m]['historical']['worst_pct']}% — that is the "
            f"same-day-gated figure; the evidence was regenerated without the lag")

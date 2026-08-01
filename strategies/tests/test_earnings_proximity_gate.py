"""Earnings-proximity gate — spec §11 fixtures + acceptance criteria.

The load-bearing guarantee (AC2): NO input combination returns CLEAR when an
earnings date is missing. The gate makes earnings a first-class veto/penalty and
fails loud on a degraded feed.
"""
import datetime as _dt

import pytest

from tradepro_strategies.gates.earnings_proximity import (
    EarningsGate,
    EarningsGateConfig,
    classify,
    route,
    sessions_between,
    stale_feed_canary,
    VETO,
    PENALIZE,
)

CFG = EarningsGateConfig()  # spec defaults


# ── §11 worked-slate fixtures (as-of 2026-07-21) ──────────────────────────────

@pytest.mark.parametrize("to_next,since_last,est,expected", [
    (None, 2, False, EarningsGate.POST_DIGEST),   # just reported → digest veto
    (63, 5, False, EarningsGate.POST_DRIFT),      # WFC — drift
    (63, 4, False, EarningsGate.POST_DRIFT),      # JNJ — drift
    (63, 4, False, EarningsGate.POST_DRIFT),      # BLK — drift
    (3, 60, False, EarningsGate.PRE_BLACKOUT),    # pre-blackout
    (6, 60, True, EarningsGate.PRE_BLACKOUT),     # estimated → buffer widens pre to 7, 6≤7
    (30, 30, False, EarningsGate.CLEAR),          # clear
    (None, None, False, EarningsGate.UNKNOWN),    # stale feed → UNKNOWN, never CLEAR
])
def test_classify_states(to_next, since_last, est, expected):
    assert classify(to_next, since_last, est, CFG) == expected


def test_estimated_buffer_boundary():
    # Without the estimate buffer, 6 sessions is OUTSIDE the 5-session pre-window.
    assert classify(6, 60, False, CFG) == EarningsGate.CLEAR
    # With the estimate flag, the window widens to 5+2=7 → 6 vetoes.
    assert classify(6, 60, True, CFG) == EarningsGate.PRE_BLACKOUT


def test_report_today_is_digest_veto():
    assert classify(None, 0, False, CFG) == EarningsGate.POST_DIGEST  # since_last==0


def test_pre_takes_priority_over_post_when_both_windows_hit():
    # A name reporting in 2 sessions AND that reported 2 sessions ago: pre wins
    # (evaluation order — imminent binary event is the dominant risk).
    assert classify(2, 2, False, CFG) == EarningsGate.PRE_BLACKOUT


def test_etf_no_earnings_is_clear_not_unknown():
    # has_earnings=False (ETF) with no dates → CLEAR, distinct from feed-failed.
    assert classify(None, None, False, CFG, has_earnings=False) == EarningsGate.CLEAR


# ── AC1/AC2: routing (veto / penalize / clear) ────────────────────────────────

def test_routing_actions():
    assert route(EarningsGate.PRE_BLACKOUT, CFG).action == "veto"
    assert route(EarningsGate.POST_DIGEST, CFG).action == "veto"
    drift = route(EarningsGate.POST_DRIFT, CFG, sessions_since_last=4)
    assert drift.action == "penalize" and drift.flag == "EARNINGS_DRIFT"
    assert drift.score_mult == 0.5 and drift.rank_cap is True
    unk = route(EarningsGate.UNKNOWN, CFG)
    assert unk.action == "penalize" and unk.flag == "EARNINGS_UNKNOWN"
    assert route(EarningsGate.CLEAR, CFG).action == "clear"


def test_veto_penalize_sets():
    assert EarningsGate.PRE_BLACKOUT in VETO and EarningsGate.POST_DIGEST in VETO
    assert EarningsGate.POST_DRIFT in PENALIZE and EarningsGate.UNKNOWN in PENALIZE


def test_ac2_never_clear_on_missing_date():
    # Any combo with a missing date must NOT be CLEAR (unless genuinely no earnings).
    for to_next in (None, 3, 30):
        for since_last in (None, 4, 30):
            if to_next is None and since_last is None:
                assert classify(to_next, since_last, False, CFG) == EarningsGate.UNKNOWN
            # a present date can still be CLEAR — that's fine; only both-missing is the trap.


# ── session counting (XNYS) ───────────────────────────────────────────────────

def test_sessions_between_skips_weekend():
    # Fri 2026-07-17 → Mon 2026-07-20 is ONE trading session (weekend skipped).
    assert sessions_between(_dt.date(2026, 7, 17), _dt.date(2026, 7, 20)) == 1


def test_sessions_between_same_day_zero_and_signed():
    assert sessions_between(_dt.date(2026, 7, 20), _dt.date(2026, 7, 20)) == 0
    # signed: past after future → negative
    assert sessions_between(_dt.date(2026, 7, 20), _dt.date(2026, 7, 17)) == -1


def test_sessions_between_bad_date_none():
    assert sessions_between("not-a-date", _dt.date(2026, 7, 20)) is None


# ── AC4: stale-feed canary ────────────────────────────────────────────────────

def test_canary_raises_when_guaranteed_reporter_returns_none():
    degraded, dead = stale_feed_canary({
        "AAPL": (None, None),   # a guaranteed quarterly reporter came back empty
        "JPM": (5, 60),
    })
    assert degraded is True and "AAPL" in dead


def test_canary_clean_when_reporters_return_dates():
    degraded, dead = stale_feed_canary({"AAPL": (30, 20), "JPM": (5, 60)})
    assert degraded is False and dead == []


# ── Canary resolution (the MA hole — alert-not-suppress, 2026-08-01) ────────
def _unknown_dec():
    from tradepro_strategies.gates.earnings_proximity import (
        EarningsGateConfig, classify, route)
    cfg = EarningsGateConfig()
    return route(classify(None, None, False, cfg, has_earnings=True), cfg)


def test_degraded_unknown_stays_visible_with_alert():
    # User 2026-08-01: continue WITH an alert, don't fully suppress. Degraded
    # feed + no news evidence → penalize (visible) with EARNINGS_UNVERIFIED,
    # rank-capped (never a star), score halved — NOT a veto.
    from tradepro_strategies.gates.earnings_proximity import resolve_unknown_when_degraded
    dec = resolve_unknown_when_degraded(_unknown_dec(), feed_degraded=True,
                                        recent_earnings_hint=None)
    assert dec.action == "penalize" and dec.flag == "EARNINGS_UNVERIFIED"
    assert dec.rank_cap is True and dec.score_mult == 0.5
    assert "VERIFY" in dec.reason


def test_degraded_unknown_with_news_evidence_vetoes():
    # Configured news feeds mention a recent report → positive evidence it just
    # reported → hard veto (the MA post-digest case).
    from tradepro_strategies.gates.earnings_proximity import resolve_unknown_when_degraded
    dec = resolve_unknown_when_degraded(_unknown_dec(), feed_degraded=True,
                                        recent_earnings_hint=True)
    assert dec.action == "veto" and dec.flag == "EARNINGS_RECENT_NEWS"


def test_healthy_feed_unknown_unchanged():
    from tradepro_strategies.gates.earnings_proximity import resolve_unknown_when_degraded
    dec = _unknown_dec()
    assert resolve_unknown_when_degraded(dec, feed_degraded=False) == dec


def test_non_unknown_states_never_resolved():
    from tradepro_strategies.gates.earnings_proximity import (
        EarningsGateConfig, classify, route, resolve_unknown_when_degraded)
    cfg = EarningsGateConfig()
    clear = route(classify(15, 40, False, cfg, has_earnings=True), cfg)
    assert resolve_unknown_when_degraded(clear, True, True) == clear

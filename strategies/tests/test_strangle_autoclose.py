"""The exit half of the index strangle — what it closes, and what it MUST NOT.

Owner, 31 Aug 2026: "a auto close one on either profit or end of day", and
"lets get in and out at end".

These guard the two ways an auto-close goes wrong: failing to close what it
should (leaving an overnight short the published evidence does not describe),
and closing something it was never meant to touch.
"""
import datetime as _dt

from tradepro_strategies.cli.index_strangle_close import (
    EOD_MINUTES_BEFORE_CLOSE, TARGET_PCT, decide_close, parse_occ, _market_for,
)
from tradepro_strategies.cli.index_strangle_paper import MARKETS


def test_occ_parses_the_contracts_actually_held():
    # The three puts open in the paper account on 31 Aug 2026.
    got = parse_occ("SPY    SEP2026 759 P [SPY   260918P00759000 100]")
    assert got == {"symbol": "SPY", "expiry": "2026-09-18",
                   "right": "P", "strike": 759.0}
    call = parse_occ("SPY    SEP2026 800 C [SPY   260918C00800000 100]")
    assert call["right"] == "C" and call["strike"] == 800.0


def test_an_unparseable_contract_returns_none_rather_than_guessing():
    # The caller LEAVES IT ALONE on None. Closing the wrong contract is worse
    # than closing nothing.
    assert parse_occ("AAPL common stock") is None
    assert parse_occ("") is None


def test_it_only_recognises_configured_strangle_markets():
    # The account also holds wheel and hand-placed positions. A close job that
    # swept every short would flatten those too — which is why this uses the
    # single-leg close, not /options/flatten.
    assert _market_for("SPY", MARKETS)[0] == "SPY"
    assert _market_for("MRVL", MARKETS) is None


def _cfg(close_local="16:00"):
    return {"tz": "America/New_York", "open_local": "09:30",
            "close_local": close_local, "index": "SPY"}


def _at(hh, mm):
    """A UTC instant corresponding to a New York wall-clock time (EDT, -4)."""
    return _dt.datetime(2026, 9, 1, hh + 4, mm, tzinfo=_dt.UTC)


def test_time_exit_fires_even_at_a_loss():
    # Load-bearing: the strikes sit ~2.4 SD away for ONE day but ~0.92 across a
    # week. Carried overnight the geometry changes and none of the published
    # evidence describes the position any more.
    v = decide_close({"credit": 6.0, "current_cost": 9.0}, _cfg(),
                     _at(15, 50))  # 10 min to the bell, deeply underwater
    assert v["close"] is True
    assert v["trigger"] == "end_of_day"


def test_profit_target_banks_it_mid_session():
    v = decide_close({"credit": 6.0, "current_cost": 3.0}, _cfg(), _at(13, 0))
    assert v["close"] is True
    assert v["trigger"] == "profit_target"
    assert v["decayed_pct"] == 50.0


def test_it_holds_when_the_target_is_not_met():
    v = decide_close({"credit": 6.0, "current_cost": 5.0}, _cfg(), _at(13, 0))
    assert v["close"] is False
    assert "target" in v["reason"]


def test_it_does_nothing_when_the_market_is_shut():
    v = decide_close({"credit": 6.0, "current_cost": 1.0}, _cfg(), _at(6, 0))
    assert v["close"] is False
    assert "not open" in v["reason"]


def test_every_verdict_states_a_reason():
    # A close decision with no stated reason cannot be graded later, which is
    # the entire point of recording these.
    for cost, when in ((9.0, _at(15, 50)), (3.0, _at(13, 0)),
                       (5.0, _at(13, 0)), (1.0, _at(6, 0))):
        v = decide_close({"credit": 6.0, "current_cost": cost}, _cfg(), when)
        assert v.get("reason")


def test_the_eod_window_is_wide_enough_to_get_filled():
    assert EOD_MINUTES_BEFORE_CLOSE >= 10
    assert 0 < TARGET_PCT < 1


# ---------------------------------------------------------------------------
# The profit target is judged on the PAIR, never on one leg.
#
# Options level 4 was granted on the evening of 31 Aug 2026, so from the next
# session both legs of a strangle actually fill. A per-leg target would buy
# back whichever leg had decayed and leave the other — the losing one — open
# and NAKED. Strictly worse than holding or closing.
# ---------------------------------------------------------------------------

def test_a_leg_at_target_does_not_close_when_the_pair_is_not():
    # Put decayed 6.00 -> 2.00 (67%, past target on its own).
    # Call moved against us 2.00 -> 5.00.
    # Pair: credit 8.00, cost 7.00 = 12.5% decayed. NOWHERE NEAR the target.
    pair = decide_close({"credit": 8.0, "current_cost": 7.0}, _cfg(), _at(13, 0))
    assert pair["close"] is False

    # The winning leg alone WOULD have closed — this is the trap.
    leg = decide_close({"credit": 6.0, "current_cost": 2.0}, _cfg(), _at(13, 0))
    assert leg["close"] is True
    assert leg["trigger"] == "profit_target"


def test_the_pair_closes_when_both_legs_have_decayed_together():
    pair = decide_close({"credit": 8.0, "current_cost": 3.5}, _cfg(), _at(13, 0))
    assert pair["close"] is True
    assert pair["trigger"] == "profit_target"


def test_an_unmarkable_pair_holds_rather_than_guessing():
    # One leg with no live mark makes the PAIR unmarkable. Half-counting it
    # would understate the cost and fire the target early.
    v = decide_close({"credit": None, "current_cost": None}, _cfg(), _at(13, 0))
    assert v["close"] is False


def test_time_exit_still_fires_on_an_unmarkable_pair():
    # The overnight rule cannot depend on having a mark.
    v = decide_close({"credit": None, "current_cost": None}, _cfg(), _at(15, 50))
    assert v["close"] is True
    assert v["trigger"] == "end_of_day"


# ---------------------------------------------------------------------------
# The close must recognise a position by the BROKER's root, not the data symbol.
#
# 1 Sep 2026: an SPX strangle filled as SPXW — the PM-settled weekly, which is
# what a third-Friday index order actually fills as. _market_for compared
# against cfg["index"], i.e. "^GSPC", matched nothing, and the close job logged
# "not a configured strangle market, LEFT ALONE" for both legs. It would have
# carried a ~$754k index strangle OVERNIGHT: precisely what the time exit
# exists to prevent, and a position the published evidence does not describe.
# ---------------------------------------------------------------------------

def test_an_index_strangle_is_recognised_under_its_weekly_root():
    assert _market_for("SPXW", MARKETS)[0] == "SPX"
    assert _market_for("NDXP", MARKETS)[0] == "NDX"


def test_the_plain_roots_still_match():
    for root, market in (("SPX", "SPX"), ("SPY", "SPY"),
                         ("QQQ", "QQQ"), ("GLD", "GOLD")):
        hit = _market_for(root, MARKETS)
        assert hit and hit[0] == market, f"{root} should map to {market}"


def test_positions_we_did_not_open_are_still_left_alone():
    # The account also holds wheel and hand-placed options. Widening the match
    # must not turn the close into a blanket sweep.
    for foreign in ("MRVL", "AAPL", "ARWR"):
        assert _market_for(foreign, MARKETS) is None


def test_roots_are_declared_not_inferred_by_prefix():
    # A prefix rule would happen to work for SPX/SPXW today and misfire the day
    # a market whose symbol prefixes another is added. Every index market
    # declares its roots explicitly.
    for m in ("SPX", "XSP", "NDX"):
        assert MARKETS[m].get("broker_roots"), f"{m} must declare broker_roots"


# ---------------------------------------------------------------------------
# THE CLOSE REQUEST MUST CARRY EVERY FIELD THE ENDPOINT REQUIRES.
#
# 1 Sep 2026, 19:45Z: the time exit fired and all four legs failed with
#   "side must be BUY or SELL"
# because the request never included one. Four short legs — SPY and SPX, about
# $830k of collateral — were carried OVERNIGHT.
#
# It could never have worked. It was invisible because the dry-run path and
# every "hold" tick return before the POST, so six hours of green "hold —
# decayed 8% of 50% target" logs said nothing about whether the close itself
# was reachable. The one path that mattered had never been executed.
#
# This test asserts the CONTRACT between job and endpoint, which is the only
# thing that would have caught it without a live fill.
# ---------------------------------------------------------------------------

def test_the_close_request_carries_every_field_the_endpoint_requires():
    import inspect
    import tradepro_strategies.cli.index_strangle_close as C
    src = inspect.getsource(C.main)
    i = src.index("/api/integrations/ibkr/option-leg")
    # Wide enough to span the whole request literal INCLUDING its
    # comments — a window that just fits today silently stops
    # covering a field the moment anyone adds a line.
    body = src[i:i + 2000]
    # OptionLegRequest rejects a missing/blank side, right, strike or symbol.
    for field in ('"side"', '"symbol"', '"expiry"', '"strike"', '"right"',
                  '"contracts"', '"closingOnly"'):
        assert field in body, f"close request is missing {field}"
    # And the side must be BUY — SELL would DOUBLE the short, not close it.
    assert '"side": "BUY"' in body


def test_closing_a_short_is_a_buy_never_a_sell():
    # Guard against the worst possible typo here: selling again would double
    # the position while reporting success.
    import inspect
    import tradepro_strategies.cli.index_strangle_close as C
    src = inspect.getsource(C.main)
    assert '"side": "SELL"' not in src


# ---------------------------------------------------------------------------
# A LEFTOVER POSITION GOES AT THE FIRST OPPORTUNITY, not at tonight's bell.
#
# 1 Sep 2026: four legs survived the 19:45 time exit because the close request
# was malformed. The close job would then have treated them exactly like fresh
# positions on 2 Sep and held them until 19:45 AGAIN — turning one accidental
# overnight into two.
# ---------------------------------------------------------------------------

def test_a_stale_position_is_flattened_rather_than_held_to_the_bell():
    import inspect
    import tradepro_strategies.cli.index_strangle_close as C
    src = inspect.getsource(C.main)
    assert "stale_overnight" in src
    # It must still require an OPEN market — never invent a fill out of hours.
    assert "_minutes_to_close(cfg) is not None" in src


def test_an_unreadable_decision_log_treats_NOTHING_as_stale():
    # Failing safe here means HOLDING. Wrongly declaring a fresh position stale
    # would close a trade the moment it was opened.
    import inspect
    import tradepro_strategies.cli.index_strangle_close as C
    src = inspect.getsource(C._placed_today)
    assert "return None" in src
    main_src = inspect.getsource(C.main)
    assert "if fresh is not None:" in main_src


def test_staleness_is_judged_on_todays_PLACED_rows_only():
    import inspect
    import tradepro_strategies.cli.index_strangle_close as C
    src = inspect.getsource(C._placed_today)
    # a decision that was never placed says nothing about what we hold
    assert 'if not d.get("placed")' in src
    # And yesterday's placement must not make today's position look fresh.
    # Asserted on the PROPERTY, not the phrasing: the guard was an early
    # `continue` on `when != today` and is now `if when == today: add`. Pinning
    # the exact wording made a behaviour-preserving rewrite fail.
    assert "when == today" in src or "when != today" in src
    assert "placed_at_utc" in src, "freshness must come from WHEN it was placed"


# ---------------------------------------------------------------------------
# AN EXIT BELONGS TO THE SESSION THAT OPENED THE POSITION.
#
# 2 Sep 2026: four legs closed successfully — "4 position(s) closed" — and not
# one exit was recorded. Two separate key mistakes:
#
#   1. _record_exit stamped TODAY. A stale position closes the morning AFTER it
#      was opened, so the write looked for a 2 Sep row that never existed.
#   2. The execution endpoint still matched `as_of` while migration 073 had
#      moved the decision key to `exchange_date`. Two keys for one row.
#
# Net: the round trip stayed unanswerable even though the close worked.
# ---------------------------------------------------------------------------

def test_the_exit_is_filed_against_the_opening_session_not_today():
    import inspect
    import tradepro_strategies.cli.index_strangle_close as C
    src = inspect.getsource(C._record_exit)
    assert "session or _dt.date.today()" in src, \
        "must prefer the OPENING session and fall back to today only as a last resort"


def test_the_session_map_survives_a_position_opened_earlier():
    # The lookback must exceed one day, or yesterday's decision is invisible
    # to today's close and the exit can never be attached.
    import inspect
    import tradepro_strategies.cli.index_strangle_close as C
    src = inspect.getsource(C._placed_today)
    assert '"days": 3' in src
    assert "sessions" in src


def test_staleness_and_session_are_computed_from_the_same_rows():
    # One read, two answers: which contracts are FRESH (placed today) and which
    # SESSION each belongs to. Splitting them would let the two disagree.
    import inspect
    import tradepro_strategies.cli.index_strangle_close as C
    src = inspect.getsource(C._placed_today)
    assert "when == today" in src      # freshness
    assert "exchange_date" in src      # session

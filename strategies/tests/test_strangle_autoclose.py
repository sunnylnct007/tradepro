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


# ---------------------------------------------------------------------------
# RECORDED MONEY IS DOLLARS. THE PROFIT TARGET IS A RATIO.
#
# 2 Sep 2026, the first complete round trip this project ever recorded, stored
#   realised_pnl = 0.32
# for a trade that made about $32. Per-share prices were summed as though they
# were dollars. Everything else reports money in dollars, so the column was
# wrong by 100x — in the table built specifically to hold honest numbers.
#
# The two must stay separate: decide_close divides, so the multiplier cancels
# and must NOT be applied there; _record_exit stores, so it must be.
# ---------------------------------------------------------------------------

def test_the_profit_target_is_unaffected_by_the_multiplier():
    # A ratio. Per-share and dollar inputs must give the SAME verdict, which is
    # why applying the multiplier to the target would be wrong.
    ps = decide_close({"credit": 8.35, "current_cost": 4.0}, _cfg(), _at(13, 0))
    money = decide_close({"credit": 835.0, "current_cost": 400.0}, _cfg(), _at(13, 0))
    assert ps["close"] == money["close"] is True
    assert ps["trigger"] == money["trigger"] == "profit_target"
    assert ps["decayed_pct"] == money["decayed_pct"]


def test_recorded_money_carries_the_contract_multiplier():
    import inspect
    import tradepro_strategies.cli.index_strangle_close as C
    src = inspect.getsource(C.main)
    assert "credit_money" in src and "cost_money" in src
    assert 'float(p.get("multiplier")' in src, "the multiplier must come from the POSITION"
    # and the recorder gets the MONEY, not the per-share figures
    assert "_record_exit(base, tok, market, expiry, credit_money, cost_money" in src


def test_the_multiplier_is_read_not_assumed():
    # Assuming 100 is how the "-99.06%" cost-basis bug happened. A default is
    # a last resort, not the source of truth.
    import inspect
    import tradepro_strategies.cli.index_strangle_close as C
    src = inspect.getsource(C.main)
    assert 'p.get("multiplier")' in src


def test_the_xsp_case_that_prompted_this():
    # credit 7.54+7.77 per share, bought back for 0.32 less, multiplier 100.
    credit_ps, cost_ps, mult = 15.31, 14.99, 100.0
    assert round((credit_ps - cost_ps) * mult, 2) == 32.0
    # the ratio is identical either way — that is the point
    assert round((credit_ps - cost_ps) / credit_ps, 4) == \
           round((credit_ps * mult - cost_ps * mult) / (credit_ps * mult), 4)


# ---------------------------------------------------------------------------
# PHASE 2 — position-level exits. Spec v1.0 §6, and §8: "The stop loss is not
# optional."
#
# Until 8 Sep 2026 this desk had NO STOP. A short strangle's loss is unbounded
# on the call side and bounded only by the strike on the put; the time exit at
# the bell was the sole thing between a bad session and an arbitrarily bad one.
# On 1 Sep the time exit ITSELF failed and four legs ran overnight — the only
# reason that was survivable is that the market did not gap.
# ---------------------------------------------------------------------------

def test_the_stop_fires_at_twice_the_credit():
    # credit 10, cost 30 -> decayed -2.0 -> exactly the stop.
    v = decide_close({"credit": 10.0, "current_cost": 30.0}, _cfg(), _at(13, 0))
    assert v["close"] is True and v["trigger"] == "stop_loss"


def test_a_loss_INSIDE_the_stop_still_holds():
    v = decide_close({"credit": 10.0, "current_cost": 25.0}, _cfg(), _at(13, 0))
    assert v["close"] is False


def test_the_stop_outranks_the_profit_target():
    # A position cannot be both, but the ORDER must put the worse outcome
    # first — a stop checked after a target is a stop that can be skipped.
    import inspect
    from tradepro_strategies.cli import index_strangle_close as C
    src = inspect.getsource(C.decide_close)
    assert src.index("stop_loss") < src.index("profit_target")


def test_the_time_exit_still_outranks_the_stop():
    # Overnight is the one risk nothing else caps. Deep underwater at the bell
    # must leave on the BELL, not wait for a stop it may never reach.
    v = decide_close({"credit": 10.0, "current_cost": 12.0}, _cfg(), _at(15, 50))
    assert v["trigger"] == "end_of_day"


def test_vol_shock_closes_the_position():
    # §6: VIX up more than 40% from the entry reading.
    v = decide_close({"credit": 10.0, "current_cost": 9.0,
                      "vol_at_entry": 14.0, "vol_now": 20.0}, _cfg(), _at(13, 0))
    assert v["close"] is True and v["trigger"] == "vol_shock"


def test_a_vol_rise_below_the_threshold_does_not_close():
    v = decide_close({"credit": 10.0, "current_cost": 9.0,
                      "vol_at_entry": 14.0, "vol_now": 18.0}, _cfg(), _at(13, 0))
    assert v["close"] is False


def test_a_MISSING_vol_reading_skips_the_check_rather_than_closing():
    # A missing reading is not a calm market — and it is not a shock either.
    # Closing on an absence would be the worst kind of guess.
    v = decide_close({"credit": 10.0, "current_cost": 9.0,
                      "vol_at_entry": None, "vol_now": None}, _cfg(), _at(13, 0))
    assert v["close"] is False


def test_the_stop_is_stated_as_a_MULTIPLE_of_credit():
    # §8: measured on CUMULATIVE credit, so the stop cannot widen with each
    # roll once rolling exists. No rolls yet, so cumulative == entry credit.
    from tradepro_strategies.cli.index_strangle_close import STOP_LOSS_MULTIPLE, VOL_SHOCK_RISE
    assert STOP_LOSS_MULTIPLE == 2.0
    assert VOL_SHOCK_RISE == 0.40


# ---------------------------------------------------------------------------
# FRESHNESS IS PER MARKET, NOT PER STRIKE — and an empty answer means HOLD.
#
# 8 Sep 2026: SPX and XSP were placed at 13:52 with placed=true and a real
# credit_actual (5,316.74 and 424.78). The 14:00 tick flattened both as
# "stale_overnight" — thirty minutes old.
#
# Strike matching is too brittle to decide whether to CLOSE. Any drift between
# the row's strikes and the fill's — a re-run, a rounding difference, an
# earlier provisional row updated later — turns a fresh position into a
# leftover. A market that placed today has no leftovers in it: this desk closes
# every position the same session.
# ---------------------------------------------------------------------------

def test_market_level_freshness_is_recorded_alongside_the_strike():
    import inspect
    import tradepro_strategies.cli.index_strangle_close as C
    src = inspect.getsource(C._placed_today)
    assert "out.add((m, '*', 0.0))" in src or 'out.add((m, "*", 0.0))' in src


def test_a_market_that_placed_today_is_not_stale_whatever_the_strike():
    import inspect
    import tradepro_strategies.cli.index_strangle_close as C
    src = inspect.getsource(C.main)
    assert '(market, "*", 0.0) not in fresh' in src


def test_an_EMPTY_fresh_set_holds_rather_than_flattening():
    # Nothing placed today anywhere, yet positions exist? That is our RECORD
    # being wrong, not a book full of leftovers. Flattening on an empty answer
    # is exactly what closed two fresh positions on 8 Sep.
    import inspect
    import tradepro_strategies.cli.index_strangle_close as C
    src = inspect.getsource(C.main)
    assert "if stale and not fresh:" in src
    assert "our record is wrong" in src


def test_the_real_close_states_its_reason_not_just_hold_and_dry_run():
    """The branch that MOVES MONEY must explain itself.

    'hold' printed a reason and --dry-run printed a reason, but the live close
    printed only "OK CLOSED SPX 7630P x1". So on 8 Sep 2026 four legs opened at
    13:53Z were closed at 14:00Z and the log could not distinguish a profit
    target from a stale-session flatten. The verdict already carries the reason;
    only the print dropped it.

    Asserted against the source with COMMENTS STRIPPED. Three earlier tests in
    this repo passed by matching a comment or a default rather than the code,
    which is worse than no test: it reports green for an absent behaviour.
    """
    import io, tokenize
    from pathlib import Path

    from tradepro_strategies.cli import index_strangle_close as _mod
    src = Path(_mod.__file__).read_text()
    out, last = [], (1, 0)
    for tok in tokenize.generate_tokens(io.StringIO(src).readline):
        if tok.type == tokenize.COMMENT:
            continue
        if tok.start[0] > last[0]:
            out.append("\n" * (tok.start[0] - last[0]))
        out.append(tok.string)
        last = tok.end
    code = "".join(out)

    assert "CLOSING" in code, (
        "the live close no longer announces itself before acting"
    )
    # A fixed window, not a line: the call spans two source lines and the
    # f-string itself contains newlines once comments are stripped.
    stmt = code[code.index("CLOSING"):][:300]
    assert "verdict['reason']" in stmt or 'verdict["reason"]' in stmt, (
        "the CLOSING line must carry the verdict's reason — a close with no "
        "stated reason cannot be graded later"
    )
    assert "trigger" in stmt, (
        "the CLOSING line must name the trigger, so a stale_overnight flatten "
        "is distinguishable from a profit target in the log"
    )

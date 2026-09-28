"""A position this strategy did not open must not consume its own limits.

24 Sep 2026. mean_reversion_swing_ibkr rejected EVERY entry, on every
15-minute cycle, all day:

    order rejected · symbol=AMP  · max_open_positions 15 < projected open positions 18
    order rejected · symbol=ABNB · max_open_positions 15 < projected open positions 18
    order rejected · symbol=ALL  · ...
    order rejected · symbol=PAYX · ...

The IBKR paper account is SHARED. Of the 18:

    13  swing's own
     3  DIS, ABBV, COP   ichimoku_equity_ibkr — the DORMANT clone, last order
                      20 Aug 2026. NOT ichimoku_equity, which is a different
                      sleeve, still trading, on T212. Both held these tickers
                      in their own accounts, so an attribution that ignores
                      the broker names the wrong one.
     2  XSP 758P/780C    the strangle's option legs, qty -1 each

An "ignore-inherited" rule already existed in on_bar and worked — the sleeve
correctly refused to TRADE those names. But it ran per-bar, AFTER the position
had been counted into the cap, so the sleeve was refusing to manage them and
being blocked BY them at the same time. Half a rule, applied at one of two
sites — the same shape as the expiry_kind fix earlier the same day.

Raising the cap would have hidden it, and hidden something worse: that another
strategy's holdings were inside this one's book at all, where an exit could
reach them.
"""
import pytest

from tradepro_strategies.paper.strategies.mean_reversion_swing import (
    MeanReversionSwingStrategy)


def _strategy(held: dict, ours: dict):
    s = MeanReversionSwingStrategy(strategy_id="mean_reversion_swing_ibkr")
    s.seed_positions(held, {})
    # What _seed_from_oms would have produced from OUR fills alone.
    for sym, px in ours.items():
        s._fill_price[sym] = px
        s._entry_bar[sym] = "2026-09-20"
    return s


THE_ACCOUNT = {"AWK": 56, "BAC": 84, "BLK": 4,            # ours
               "ABBV": 2, "COP": 12, "DIS": 14,           # Ichimoku's
               "XSP": -2}                                 # the strangle's
OURS = {"AWK": 133.27, "BAC": 59.07, "BLK": 1078.59}


def test_only_our_own_positions_are_managed():
    s = _strategy(THE_ACCOUNT, OURS)
    assert sorted(s.managed_positions()) == ["AWK", "BAC", "BLK"]


def test_the_inherited_names_are_still_VISIBLE_to_the_strategy():
    # It must still SEE them: that is how on_bar declines to manage them, and
    # how it never buys more of a name the account already holds. Excluded from
    # the COUNT, not from the book.
    s = _strategy(THE_ACCOUNT, OURS)
    assert "DIS" in s.positions and int(s.positions["DIS"].quantity) == 14


def test_a_SHORT_leg_we_did_not_open_is_excluded_too():
    # on_bar gates on held > 0 because it is deciding whether to exit a long.
    # Ownership does not depend on direction: this sleeve is long-only, so a
    # short in its book came from somewhere else — the strangle's XSP legs.
    s = _strategy(THE_ACCOUNT, OURS)
    assert "XSP" not in s.managed_positions()


def test_the_cap_arithmetic_that_blocked_the_desk():
    from tradepro_strategies.paper.risk import _projected_open_count
    from tradepro_strategies.paper.strategy import Order, OrderSide, OrderType
    s = _strategy(THE_ACCOUNT, OURS)
    order = Order(strategy_id=s.strategy_id, symbol="AMP", side=OrderSide.BUY,
                  quantity=10, type=OrderType.LIMIT, limit_price=1.0, tag="t")
    before = _projected_open_count(s.positions, order, 10)
    after = _projected_open_count(s.managed_positions(), order, 10)
    assert before == 8, before          # 7 held + the new one
    assert after == 4, after            # 3 ours + the new one
    assert after < before


def test_one_definition_serves_both_sites():
    # is_inherited is the predicate on_bar uses to leave a position alone AND
    # the one managed_positions uses to keep it off our limits. If these ever
    # disagree the sleeve is back to refusing to manage what it is charged for.
    s = _strategy(THE_ACCOUNT, OURS)
    for sym in ("ABBV", "COP", "DIS", "XSP"):
        assert s.is_inherited(sym) is True
        assert sym not in s.managed_positions()
    for sym in ("AWK", "BAC", "BLK"):
        assert s.is_inherited(sym) is False
        assert sym in s.managed_positions()


def test_it_fails_toward_INHERITED_when_the_OMS_is_unreadable():
    # _seed_from_oms fails closed: no fills read means empty maps. The sleeve
    # must then manage NOTHING rather than manage what it cannot verify —
    # matching the existing on_bar behaviour, not overriding it.
    s = _strategy(THE_ACCOUNT, {})
    assert s.managed_positions() == {}


def test_a_strategy_with_its_own_account_is_unaffected():
    # The base-class default must stay "everything I hold", so a strategy that
    # does not share an account is not silently narrowed.
    from tradepro_strategies.paper.strategy import Strategy
    assert Strategy.managed_positions is not MeanReversionSwingStrategy.managed_positions
    s = _strategy(THE_ACCOUNT, OURS)
    assert Strategy.managed_positions(s) == s.positions


def test_the_ENGINE_feeds_the_risk_gate_managed_positions():
    """The half that actually closes the gap.

    The override is inert unless the engine uses it: with the engine reading
    `.positions` the same account projects 8 where managed projects 4. Asserted
    on the ast TREE rather than by grepping the text, because a grep cannot
    tell a live call from one in a comment or an unreachable branch.
    """
    import ast
    import inspect

    from tradepro_strategies.paper import engine as E

    tree = ast.parse(inspect.getsource(E))
    found = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if getattr(node.func, "id", None) != "RiskContext":
            continue
        for kw in node.keywords:
            if kw.arg != "current_positions":
                continue
            # dict(reg.strategy.managed_positions())
            attrs = [n.attr for n in ast.walk(kw.value)
                     if isinstance(n, ast.Attribute)]
            found.append(attrs)
    assert found, "no RiskContext(current_positions=...) call found in the engine"
    for attrs in found:
        assert "managed_positions" in attrs, (
            f"the engine builds the risk gate from {attrs} — an inherited "
            f"position will be charged against this strategy's own cap")

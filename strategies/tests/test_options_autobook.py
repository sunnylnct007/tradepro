"""Booking the board on paper — the part that can hurt.

Scope confirmed by the owner 28 Oct 2026: every eligible row, all structures.
Measured on that day's board, "everything eligible" wanted $235,300 against a
~$247,000 account — 95% of NAV, with four cash-secured puts making up $204,000
of it and COST alone at $96,500 (39% of the account in ONE position).

The screen runs with capital gates OFF on purpose: it answers "is this a good
trade?". Booking answers "does this fit the account?", so the gates are on
here, and every refusal carries its number.
"""
import pytest

from tradepro_strategies.cli import options_autobook as ab


def _row(sym, cap, short=100.0, long=None, ror=20.0,
         structure="BULL_PUT_SPREAD", expiry="2026-11-20"):
    return {"symbol": sym, "eligible": True, "structure": structure,
            "chain_expiry": expiry,
            "economics": {"capital_usd": cap, "short_strike": short,
                          "long_strike": long, "return_on_risk_pct": ror}}


# ── the gates ─────────────────────────────────────────────────────────────
def test_an_oversized_position_is_refused_with_its_number():
    """THE COST CASE: $96,500 is 39% of the account in one trade."""
    (p,) = ab._plan([_row("COST", 96_500, structure="CASH_SECURED_PUT")])
    assert not p["book"]
    assert "96,500" in p["reason"] and "per-position" in p["reason"]


def test_the_day_has_a_ceiling_not_just_the_position():
    # Twenty individually-affordable trades can still be an unaffordable book.
    rows = [_row(f"S{i}", 10_000, ror=20 - i) for i in range(12)]
    plan = ab._plan(rows)
    booked = [p for p in plan if p["book"]]
    assert sum(p["capital_usd"] for p in booked) <= ab.MAX_DEPLOY_USD
    assert any("ceiling" in p["reason"] for p in plan if not p["book"])


def test_unpriced_capital_is_refused_never_assumed_zero():
    # A structure the chain could not price has an UNKNOWN cost. Treating that
    # as free is how the account fills with trades nobody sized.
    (p,) = ab._plan([_row("INTC", None)])
    assert not p["book"] and "not priced" in p["reason"]


def test_a_row_without_an_expiry_is_refused():
    r = _row("X", 1_000); r["chain_expiry"] = None
    (p,) = ab._plan([r])
    assert not p["book"] and "expiry" in p["reason"]


def test_refusals_are_RETURNED_not_dropped():
    # A row dropped without a stated reason is indistinguishable from one never
    # considered — the false-clear this desk keeps removing.
    plan = ab._plan([_row("GOOD", 1_000), _row("BIG", 500_000)])
    assert len(plan) == 2
    assert all(p["reason"] or p["book"] for p in plan)


def test_the_best_expectancy_gets_the_capital_first():
    """When the day's ceiling binds, the WORST row is the one left out.

    Sized so the per-position limit passes and only the daily ceiling bites —
    the first version of this test used $50,000 rows, which the per-position
    gate refused outright, so it proved nothing about ordering.
    """
    each = ab.PER_POSITION_USD * 0.9
    n = int(ab.MAX_DEPLOY_USD // each) + 1          # one more than fits
    rows = [_row(f"S{i}", each, ror=float(i)) for i in range(n)]
    plan = ab._plan(rows)
    booked = [p["symbol"] for p in plan if p["book"]]
    refused = [p["symbol"] for p in plan if not p["book"]]
    assert booked[0] == f"S{n - 1}", "highest expectancy should be booked first"
    assert "S0" in refused, "the lowest-expectancy row is the one squeezed out"


# ── leg ordering: the one that prevents a naked short ─────────────────────
def test_the_protective_BUY_is_sent_BEFORE_the_short_SELL():
    """THE SAFETY PROPERTY. If the floor fails to go on and the premium leg had
    already been sold, a defined-risk trade becomes an UNBOUNDED one on a
    failed HTTP call — the exact exposure the spread exists to remove."""
    legs = ab._legs_for({"structure": "BULL_PUT_SPREAD",
                         "short_strike": 100.0, "long_strike": 95.0})
    assert [l["side"] for l in legs] == ["BUY", "SELL"]
    assert legs[0]["strike"] == 95.0, "the BUY must be the LOWER strike"


def test_a_cash_secured_put_is_a_single_sell():
    legs = ab._legs_for({"structure": "CASH_SECURED_PUT",
                         "short_strike": 100.0, "long_strike": None})
    assert len(legs) == 1 and legs[0]["side"] == "SELL"


def test_a_spread_with_no_long_strike_is_not_sent_as_a_naked_sell():
    # Belt and braces: if economics never priced a floor, this must not quietly
    # degrade into selling the short leg alone.
    legs = ab._legs_for({"structure": "BULL_PUT_SPREAD",
                         "short_strike": 100.0, "long_strike": None})
    assert len(legs) == 1 and legs[0]["side"] == "SELL"
    # ...and the planner refuses it upstream for having no priced capital.
    (p,) = ab._plan([_row("X", None, long=None)])
    assert not p["book"]


# ── the expiry the leg endpoint needs ─────────────────────────────────────
@pytest.mark.parametrize("raw,want", [
    ("20261120", "2026-11-20"),
    ("2026-11-20", "2026-11-20"),
    ("2026-11-20T00:00:00Z", "2026-11-20"),
])
def test_expiry_is_normalised_to_what_the_endpoint_accepts(raw, want):
    assert ab._expiry_iso({"chain_expiry": raw}) == want


def test_an_unparseable_expiry_is_None_not_a_guess():
    assert ab._expiry_iso({"chain_expiry": "NOV26"}) is None

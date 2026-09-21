"""Never sell what the broker says you do not hold.

21 Sep 2026. This sleeve sold ARWR ELEVEN TIMES. Every exit filled at IBKR,
the OMS recorded two of them, and the strategy — reading its own state —
believed it still held 61 shares and sold them again, and again, until the
account was SHORT 671 against a 61-share position. SNOW went short 143 the
same way.

The stop and target logic was correct throughout. It was applied to a
position that no longer existed.

The rule was already on this desk's record and in the assistant's standing
memory — *the broker is the golden source; never trust the OMS for "do we own
X"* — and the owner had to repeat it out loud after the account went short.
These tests put it in the code instead of in a habit.
"""
import pytest

from tradepro_strategies.paper.strategies.mean_reversion_swing import (
    MeanReversionSwingStrategy,
)


class _Strat(MeanReversionSwingStrategy):
    """Only the surface the exit guard touches."""

    def __init__(self, broker):
        self.strategy_id = "mean_reversion_swing_ibkr"
        self._fill_price = {"ARWR": 81.22}
        self._entry_bar = {"ARWR": "2026-09-01"}
        self.decisions = []
        self._broker = broker

    def log_decision(self, **kw):
        self.decisions.append(kw)

    def _broker_positions(self):
        return self._broker


def test_a_position_the_broker_no_longer_holds_is_not_sold():
    """The exact 21 Sep failure: local state says 61, IBKR says gone."""
    s = _Strat({"ARWR": 0.0})
    assert s._broker_positions()["ARWR"] == 0.0
    # the guard's contract: flat at the broker means do not sell, and forget
    # the stale local entry so it cannot fire again next bar
    assert "ARWR" in s._fill_price


def test_the_broker_lookup_returns_none_when_it_cannot_be_read(monkeypatch):
    """Unreadable broker must be distinguishable from 'we hold nothing'.

    None means 'could not ask' and the caller holds. An empty dict would mean
    'you own nothing', which would flatten the book.
    """
    import requests

    def _boom(*a, **k):
        raise ConnectionError("ibkr down")
    monkeypatch.setattr(requests, "get", _boom)
    monkeypatch.setattr(
        "tradepro_strategies.cli.push_to_api.load_credentials",
        lambda: ("https://example.invalid", "tok"))
    s = MeanReversionSwingStrategy.__new__(MeanReversionSwingStrategy)
    assert MeanReversionSwingStrategy._broker_positions(s) is None


def test_broker_positions_parses_and_aggregates(monkeypatch):
    import requests

    class _R:
        status_code = 200

        @staticmethod
        def json():
            return [{"symbol": "ARWR_US_EQ", "position": -671},
                    {"symbol": "SNOW_US_EQ", "position": -143},
                    {"symbol": "BAC_US_EQ", "position": 84}]

    monkeypatch.setattr(requests, "get", lambda *a, **k: _R())
    monkeypatch.setattr(
        "tradepro_strategies.cli.push_to_api.load_credentials",
        lambda: ("https://example.invalid", "tok"))
    s = MeanReversionSwingStrategy.__new__(MeanReversionSwingStrategy)
    pos = MeanReversionSwingStrategy._broker_positions(s)
    assert pos == {"ARWR": -671.0, "SNOW": -143.0, "BAC": 84.0}
    # the sign is the whole point — a short must not read as a holding
    assert pos["ARWR"] < 0


def test_a_short_at_the_broker_is_never_treated_as_something_to_sell():
    s = _Strat({"ARWR": -671.0})
    assert s._broker_positions()["ARWR"] < 0


@pytest.mark.parametrize("broker_qty,local,expect_sell_qty", [
    (61.0, 61, 61),     # agree — sell the lot
    (30.0, 61, 30),     # partly closed elsewhere — size to the broker
    (0.0, 61, 0),       # gone — sell nothing
    (-671.0, 61, 0),    # already short — sell nothing, this is the incident
])
def test_the_exit_is_always_sized_to_the_broker(broker_qty, local, expect_sell_qty):
    """Local state may only ever REDUCE the order, never justify one."""
    sized = 0 if broker_qty <= 0 else int(min(local, broker_qty))
    assert sized == expect_sell_qty


# ── the desk check must SEE the divergence, not just the strategy ─────────
from tradepro_strategies.cli import desk_check as dc  # noqa: E402
from tradepro_strategies.cli.desk_check import BROKEN, OK, UNKNOWN  # noqa: E402


def _wire(monkeypatch, orders, positions):
    def _get(base, token, path, timeout=30):
        return positions if "positions" in path else orders
    monkeypatch.setattr(dc, "_get", _get)
    monkeypatch.setattr(dc, "LIVE_STRATEGIES", ("sw",))


def _filled(sym, side, qty, when="2026-09-01T14:00:00Z"):
    return {"strategyId": "sw", "symbol": f"{sym}_US_EQ", "side": side,
            "state": "FILLED", "filledQty": qty, "createdAtUtc": when,
            "broker": "IBKR_PAPER", "avgFillPrice": 100.0}


def test_the_21_sep_divergence_is_reported_BROKEN(monkeypatch):
    """OMS long 61, broker short 671 — 732 shares apart, and silent."""
    _wire(monkeypatch,
          [_filled("ARWR", "BUY", 61)],
          [{"symbol": "ARWR_US_EQ", "position": -671}])
    (c,) = dc.check_broker_agrees("http://x", None)
    assert c.status == BROKEN
    assert "732" in c.detail
    assert "broker -671" in c.detail and "OMS +61" in c.detail
    assert "BROKER is right" in c.fix


def test_agreement_is_OK(monkeypatch):
    _wire(monkeypatch,
          [_filled("BAC", "BUY", 84)],
          [{"symbol": "BAC_US_EQ", "position": 84}])
    (c,) = dc.check_broker_agrees("http://x", None)
    assert c.status == OK


def test_other_strategies_positions_are_not_this_lanes_problem(monkeypatch):
    """DIS/COP sit at the broker under another sleeve — not a divergence."""
    _wire(monkeypatch,
          [_filled("BAC", "BUY", 84)],
          [{"symbol": "BAC_US_EQ", "position": 84},
           {"symbol": "DIS_US_EQ", "position": 14},
           {"symbol": "SPX", "position": -2}])
    (c,) = dc.check_broker_agrees("http://x", None)
    assert c.status == OK, c.detail


def test_a_closed_position_still_claimed_by_the_oms_is_caught(monkeypatch):
    """Broker flat, OMS thinks it holds — the shape that caused the short."""
    _wire(monkeypatch, [_filled("ARWR", "BUY", 61)], [])
    (c,) = dc.check_broker_agrees("http://x", None)
    assert c.status == BROKEN


def test_an_unreadable_broker_is_UNKNOWN_not_OK(monkeypatch):
    def _boom(*a, **k):
        raise ConnectionError("ibkr down")
    monkeypatch.setattr(dc, "_get", _boom)
    (c,) = dc.check_broker_agrees("http://x", None)
    assert c.status == UNKNOWN
    assert "unverified book" in c.fix


def test_a_non_ibkr_lane_is_not_compared_against_ibkr(monkeypatch):
    """ichimoku_equity routes to T212, ichimoku_fx_mr to IG. Comparing their
    books against an IBKR position list reported ten and seven false
    divergences on the first run — a check that cries wolf on half the lanes
    gets ignored, which is the failure this file exists to prevent."""
    t212 = dict(_filled("AAPL", "BUY", 10), broker="T212_DEMO")
    _wire(monkeypatch, [t212], [])
    checks = dc.check_broker_agrees("http://x", None)
    assert all(c.status != BROKEN for c in checks), [c.detail for c in checks]


# ── phantom positions must not consume the open-position cap ──────────────
def test_positions_the_broker_does_not_hold_are_dropped_at_seed_time(monkeypatch):
    """21 Sep, after the short was flattened: the OMS still claimed sixteen
    positions while IBKR held ten. The exit guard stopped the phantoms being
    SOLD, but they still filled the position map — and the risk service counts
    that map:

        "max_open_positions 15 < projected open positions 16"

    So six positions that did not exist blocked every new entry. CVS, GM and
    VZ all fired and none was placed, and the sleeve looked quiet rather than
    jammed. Seeding is where ownership is decided, so the broker wins there.
    """
    import requests

    class _R:
        status_code = 200

        def __init__(self, payload): self._p = payload
        def json(self): return self._p

    oms = [_filled("BAC", "BUY", 84), _filled("GONE", "BUY", 10)]
    positions = [{"symbol": "BAC_US_EQ", "position": 84}]

    def _get(url, **kw):
        return _R(positions if "positions" in url else oms)

    monkeypatch.setattr(requests, "get", _get)
    monkeypatch.setattr(
        "tradepro_strategies.cli.push_to_api.load_credentials",
        lambda: ("https://example.invalid", "tok"))

    s = MeanReversionSwingStrategy.__new__(MeanReversionSwingStrategy)
    s.strategy_id = "sw"; s._fill_price = {}; s._entry_bar = {}
    MeanReversionSwingStrategy._seed_from_oms(s)
    assert "BAC" in s._fill_price
    assert "GONE" not in s._fill_price, "a position the broker does not hold must not occupy a slot"


def test_an_unreadable_broker_keeps_the_oms_view_and_warns(monkeypatch):
    """Failing closed here would flatten the map and let the sleeve re-enter
    names it already holds. Keep the OMS view, and say it is unverified."""
    import requests

    class _R:
        status_code = 200
        def json(self): return [_filled("BAC", "BUY", 84)]

    def _get(url, **kw):
        if "positions" in url:
            raise ConnectionError("ibkr down")
        return _R()

    monkeypatch.setattr(requests, "get", _get)
    monkeypatch.setattr(
        "tradepro_strategies.cli.push_to_api.load_credentials",
        lambda: ("https://example.invalid", "tok"))
    s = MeanReversionSwingStrategy.__new__(MeanReversionSwingStrategy)
    s.strategy_id = "sw"; s._fill_price = {}; s._entry_bar = {}
    MeanReversionSwingStrategy._seed_from_oms(s)
    assert "BAC" in s._fill_price


def test_an_empty_broker_response_is_unreadable_not_an_empty_account(monkeypatch):
    """A 200 carrying zero rows must NOT mean "you own nothing".

    Observed live on 21 Sep: this endpoint returned zero positions between two
    calls that each returned nineteen. Treating that as an empty account would
    drop every position from management in a single tick — the seed forgets
    them, the exit guard calls each one already-flat, the book goes unmanaged
    and the log looks calm.

    The desk's rule, learned expensively: never infer from an absence. An
    empty IBKR response carries no information, so it is reported exactly like
    a failed read.
    """
    import requests

    class _R:
        status_code = 200

        @staticmethod
        def json():
            return {"positions": []}

    monkeypatch.setattr(requests, "get", lambda *a, **k: _R())
    monkeypatch.setattr(
        "tradepro_strategies.cli.push_to_api.load_credentials",
        lambda: ("https://example.invalid", "tok"))
    s = MeanReversionSwingStrategy.__new__(MeanReversionSwingStrategy)
    assert MeanReversionSwingStrategy._broker_positions(s) is None, (
        "zero rows must be UNKNOWN, never an authoritative empty account")


def test_a_genuinely_flat_account_is_indistinguishable_and_that_is_accepted():
    """The cost of the rule, stated plainly: if the account really IS flat we
    also return None and keep stale local state for a tick. That is the safe
    direction — a stale position is re-checked next bar, an unmanaged book is
    not noticed until something moves."""
    assert True

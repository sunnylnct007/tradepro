"""Risk controls for IchimokuEquityStrategy — stop-loss, take-profit, and a
per-sector concentration cap. ALL OFF BY DEFAULT.

The contract these tests pin:
  (a) DEFAULT-OFF parity — with the three new params at their defaults
      (None), on_bar output is byte-for-byte identical to before: no risk
      exits, no sector cap, same orders.
  (b) stop_loss_pct fires a SELL that flattens a held long when its
      unrealised return breaches the stop.
  (c) take_profit_pct fires a SELL when the long is up past the target.
  (d) max_per_sector blocks a 3rd same-sector ENTRY when cap=2, while
      leaving existing holdings untouched.
  (e) NEVER goes short — every emitted order is a BUY, or a SELL sized to
      exactly flatten (qty == held long); no order ever takes qty net
      below zero.

All tests drive on_bar directly with synthetic daily data injected via
`_data_fn`, and use a STALE bar timestamp so the live market-hours gate is
bypassed (the same historical/replay context the parity tests rely on).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd
import pytest

from tradepro_strategies.paper.strategies.ichimoku_equity import IchimokuEquityStrategy
from tradepro_strategies.paper.strategies._equity_sectors import sector_for, SECTOR_MAP
from tradepro_strategies.paper.strategy import Bar, OrderSide, OrderType


# ── Synthetic daily data ────────────────────────────────────────────────
def _uptrend_df(n: int = 300, start: float = 100.0, end: float = 250.0) -> pd.DataFrame:
    """A clean monotone uptrend → price above the Ichimoku cloud, tenkan>kijun
    → the trader's stateful long signal = 1 on the latest bar."""
    close = np.linspace(start, end, n)
    high = close * 1.01
    low = close * 0.99
    return pd.DataFrame({"High": high, "Low": low, "Close": close})


def _flat_signal_df(n: int = 300, level: float = 100.0) -> pd.DataFrame:
    """Sideways/declining series → no long signal (price not above cloud)."""
    close = np.linspace(level * 1.5, level, n)  # steady decline → flat/0
    high = close * 1.01
    low = close * 0.99
    return pd.DataFrame({"High": high, "Low": low, "Close": close})


def _stale_ts() -> datetime:
    """A timestamp older than a week → on_bar treats the bar as a historical
    replay and the live market-hours gate is skipped (deterministic tests)."""
    return datetime.now(timezone.utc) - timedelta(days=30)


def _bar(symbol: str, close: float) -> Bar:
    return Bar(
        symbol=symbol,
        timestamp=_stale_ts(),
        open=close,
        high=close * 1.01,
        low=close * 0.99,
        close=close,
        volume=1_000_000,
        timeframe_seconds=86400,
        is_live=False,
    )


def _make_strategy(symbols, data_map, **extra_params) -> IchimokuEquityStrategy:
    """Build an IchimokuEquityStrategy whose data + regime resolve from an
    in-memory {symbol: DataFrame} map. Regime filter OFF so a long signal is
    not blocked by an unavailable SPY series."""
    def data_fn(sym: str):
        return data_map.get(sym)

    params = {
        "symbols": list(symbols),
        "use_regime_filter": False,
        "_data_fn": data_fn,
        # Tiny capital so sizing yields a small whole-share qty.
        "capital_usd": 100_000.0,
        "sleeve_size": 20,
        **extra_params,
    }
    strat = IchimokuEquityStrategy(strategy_id="test-ichimoku-equity", params=params)
    strat.on_session_start(_stale_ts())
    return strat


# ─────────────────────────────────────────────────────────────────────────
# (a) DEFAULT-OFF parity: new params absent ⇒ identical behaviour
# ─────────────────────────────────────────────────────────────────────────
def test_defaults_are_off():
    """All three risk-control params default to OFF (None)."""
    d = IchimokuEquityStrategy.default_params()
    assert d["stop_loss_pct"] is None
    assert d["take_profit_pct"] is None
    assert d["max_per_sector"] is None


def test_default_off_no_risk_exit_on_a_loser():
    """A held long sitting at a big LOSS emits NO exit when stop_loss_pct is
    unset — exactly the legacy behaviour (the Ichimoku signal alone decides)."""
    df = {"AAPL": _uptrend_df()}
    strat = _make_strategy(["AAPL"], df)  # no risk params → all OFF
    # Seed a held long with a high cost basis so mark << entry (deep loss).
    strat.seed_positions({"AAPL": 10})
    strat._positions["AAPL"] = 10
    strat.position_for("AAPL").avg_entry_price = 1000.0  # entry far above mark
    orders = strat.on_bar(_bar("AAPL", 200.0))  # ~80% underwater
    # Signal is long (uptrend) and we're already long → skip-already-long,
    # NO stop fires because the control is off.
    assert orders == []


def test_default_off_entry_still_fires():
    """Default-off must not block a normal entry — a flat name with a long
    signal still buys."""
    df = {"AAPL": _uptrend_df()}
    strat = _make_strategy(["AAPL"], df)
    orders = strat.on_bar(_bar("AAPL", 250.0))
    assert len(orders) == 1
    assert orders[0].side == OrderSide.BUY
    assert orders[0].quantity > 0


def test_default_off_no_sector_cap():
    """With max_per_sector unset, opening a 3rd same-sector name is allowed."""
    # AAPL, MSFT, GOOGL are all 'tech'.
    df = {s: _uptrend_df() for s in ("AAPL", "MSFT", "GOOGL")}
    strat = _make_strategy(["AAPL", "MSFT", "GOOGL"], df)
    # Already hold two tech names.
    strat._positions["AAPL"] = 10
    strat._positions["MSFT"] = 10
    orders = strat.on_bar(_bar("GOOGL", 250.0))
    assert len(orders) == 1
    assert orders[0].side == OrderSide.BUY  # 3rd tech entry NOT capped


# ─────────────────────────────────────────────────────────────────────────
# (b) stop_loss_pct fires a SELL to flatten a breached long
# ─────────────────────────────────────────────────────────────────────────
def test_stop_loss_fires_sell_to_flatten():
    df = {"AAPL": _uptrend_df()}
    strat = _make_strategy(["AAPL"], df, stop_loss_pct=0.08)  # 8% stop
    strat.seed_positions({"AAPL": 10})
    strat._positions["AAPL"] = 10
    strat.position_for("AAPL").avg_entry_price = 300.0
    # Mark 270 ⇒ −10% < −8% stop ⇒ fire.
    orders = strat.on_bar(_bar("AAPL", 270.0))
    assert len(orders) == 1
    o = orders[0]
    assert o.side == OrderSide.SELL
    assert o.quantity == 10          # flattens exactly the held qty
    assert o.type == OrderType.MARKET
    assert "STOP" in o.tag


def test_stop_loss_does_not_fire_within_threshold():
    df = {"AAPL": _uptrend_df()}
    strat = _make_strategy(["AAPL"], df, stop_loss_pct=0.08)
    strat._positions["AAPL"] = 10
    strat.position_for("AAPL").avg_entry_price = 300.0
    # Mark 285 ⇒ −5% (inside the 8% stop) ⇒ no stop; long signal + already
    # long ⇒ skip-already-long ⇒ no order.
    orders = strat.on_bar(_bar("AAPL", 285.0))
    assert orders == []


def test_stop_loss_skipped_when_cost_basis_unknown():
    """A broker-seeded leftover with unknown entry (avg_entry_price=0) must
    NOT be stopped — we can't compute a genuine loss %."""
    df = {"AAPL": _uptrend_df()}
    strat = _make_strategy(["AAPL"], df, stop_loss_pct=0.01)  # trivially tight
    strat._positions["AAPL"] = 10
    # avg_entry_price left at its 0.0 default (unknown cost basis).
    orders = strat.on_bar(_bar("AAPL", 50.0))
    assert orders == []  # no fabricated stop


# ─────────────────────────────────────────────────────────────────────────
# (c) take_profit_pct fires
# ─────────────────────────────────────────────────────────────────────────
def test_take_profit_fires_sell_to_flatten():
    df = {"AAPL": _uptrend_df()}
    strat = _make_strategy(["AAPL"], df, take_profit_pct=0.15)  # +15% target
    strat._positions["AAPL"] = 7
    strat.position_for("AAPL").avg_entry_price = 200.0
    # Mark 250 ⇒ +25% ≥ +15% ⇒ fire take-profit.
    orders = strat.on_bar(_bar("AAPL", 250.0))
    assert len(orders) == 1
    o = orders[0]
    assert o.side == OrderSide.SELL
    assert o.quantity == 7
    assert "TAKE-PROFIT" in o.tag


def test_take_profit_does_not_fire_below_target():
    df = {"AAPL": _uptrend_df()}
    strat = _make_strategy(["AAPL"], df, take_profit_pct=0.30)
    strat._positions["AAPL"] = 7
    strat.position_for("AAPL").avg_entry_price = 200.0
    # +25% < +30% ⇒ no take-profit; already long ⇒ no order.
    orders = strat.on_bar(_bar("AAPL", 250.0))
    assert orders == []


# ─────────────────────────────────────────────────────────────────────────
# (d) max_per_sector blocks a 3rd tech entry at cap=2; holdings untouched
# ─────────────────────────────────────────────────────────────────────────
def test_max_per_sector_blocks_third_tech_entry():
    df = {s: _uptrend_df() for s in ("AAPL", "MSFT", "GOOGL")}
    strat = _make_strategy(["AAPL", "MSFT", "GOOGL"], df, max_per_sector=2)
    # Already hold two 'tech' names.
    strat._positions["AAPL"] = 10
    strat._positions["MSFT"] = 10
    orders = strat.on_bar(_bar("GOOGL", 250.0))  # 3rd tech, signal long
    assert orders == []  # capped, no entry


def test_max_per_sector_allows_entry_in_different_sector():
    # Hold two tech; JPM is 'financials' → cap not reached for that sector.
    df = {s: _uptrend_df() for s in ("AAPL", "MSFT", "JPM")}
    strat = _make_strategy(["AAPL", "MSFT", "JPM"], df, max_per_sector=2)
    strat._positions["AAPL"] = 10
    strat._positions["MSFT"] = 10
    orders = strat.on_bar(_bar("JPM", 250.0))
    assert len(orders) == 1
    assert orders[0].side == OrderSide.BUY


def test_max_per_sector_does_not_touch_existing_holdings():
    """The cap applies ONLY to NEW entries — a held name over the cap is
    never force-exited by the concentration rule."""
    df = {s: _uptrend_df() for s in ("AAPL", "MSFT", "GOOGL")}
    strat = _make_strategy(["AAPL", "MSFT", "GOOGL"], df, max_per_sector=2)
    # Three tech names already held (over the cap of 2).
    strat._positions["AAPL"] = 10
    strat._positions["MSFT"] = 10
    strat._positions["GOOGL"] = 10
    # Bar for a held name with a long signal: already-long, NO sell emitted.
    orders = strat.on_bar(_bar("AAPL", 250.0))
    assert orders == []


# ─────────────────────────────────────────────────────────────────────────
# (e) never goes short
# ─────────────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("params,seed_qty,entry,mark", [
    ({"stop_loss_pct": 0.05}, 10, 300.0, 250.0),   # stop fires
    ({"take_profit_pct": 0.05}, 10, 200.0, 260.0),  # take-profit fires
])
def test_risk_exit_never_oversells(params, seed_qty, entry, mark):
    """Any risk exit sells AT MOST the held quantity — never more — so the
    projected position is never net short (long-only)."""
    df = {"AAPL": _uptrend_df()}
    strat = _make_strategy(["AAPL"], df, **params)
    strat._positions["AAPL"] = seed_qty
    strat.position_for("AAPL").avg_entry_price = entry
    orders = strat.on_bar(_bar("AAPL", mark))
    assert len(orders) == 1
    o = orders[0]
    assert o.side == OrderSide.SELL
    assert o.quantity == seed_qty       # exactly flat, not beyond
    projected = seed_qty - o.quantity   # post-fill position
    assert projected == 0
    assert projected >= 0               # never net short


def test_no_sell_when_flat():
    """Risk controls never open a SHORT — a flat name with stop+TP enabled
    and a non-long signal emits nothing on the sell side."""
    df = {"AAPL": _flat_signal_df()}
    strat = _make_strategy(
        ["AAPL"], df, stop_loss_pct=0.05, take_profit_pct=0.05,
    )
    # Flat book.
    orders = strat.on_bar(_bar("AAPL", 100.0))
    for o in orders:
        # The only possible order here would be a BUY entry; never a SELL
        # (no long to close, and we never initiate a short).
        assert o.side != OrderSide.SELL


# ─────────────────────────────────────────────────────────────────────────
# sector classifier coverage
# ─────────────────────────────────────────────────────────────────────────
def test_sector_for_known_and_unknown():
    assert sector_for("AAPL") == "tech"
    assert sector_for("NVDA") == "semis"
    assert sector_for("AVGO") == "semis"
    assert sector_for("CRM") == "software"
    assert sector_for("NOW") == "software"
    assert sector_for("SNOW") == "software"
    assert sector_for("CIEN") == "software"
    assert sector_for("MPWR") == "semis"
    assert sector_for("JPM") == "financials"
    assert sector_for("KO") == "consumer"
    assert sector_for("XOM") == "energy"
    assert sector_for("GLD") == "other"
    # Unknown → default bucket, never raises.
    assert sector_for("ZZZZ") == "other"
    assert sector_for("") == "other"


def test_sector_for_strips_broker_suffix():
    assert sector_for("AAPL_US_EQ") == "tech"
    assert sector_for("NVDA_US_EQ") == "semis"


def test_sector_map_covers_large_50():
    """Every large_50 name + GLD is explicitly mapped (not just defaulted)."""
    from tradepro_strategies.quant_engine.config import QuantEngineConfig  # type: ignore
    cfg = QuantEngineConfig()
    for tkr in cfg.large_50:
        assert tkr in SECTOR_MAP, f"large_50 name {tkr} missing from SECTOR_MAP"
    for tkr in cfg.gold_tickers:
        assert tkr in SECTOR_MAP, f"gold name {tkr} missing from SECTOR_MAP"

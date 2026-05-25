"""IchimokuEquityStrategy — daily Ichimoku trend-following paper strategy.

Converts the quant_engine daily sleeve signals into live paper orders
via the TradePro paper engine.

Execution model (Market-on-Open):
  Signal computed ONCE at session start using prior-day cached daily data.
  -> Entry/exit order placed at the FIRST bar of the new session (MOO).
  -> Position held until the daily signal flips.

Regime gate:
  SPY < 200-SMA -> no new longs (AMBER/BEAR mode, existing positions hold
  until their own exit signal fires).

Vol-targeted sizing:
  qty = min(max_leverage, target_vol / realised_vol_60d) x capital_per_slot / price

Manual overrides (checked on every bar via OverrideRegistry):
  PAUSE         -> skip all signal generation this session
  VETO_ORDER    -> discard the pending order for this symbol (one-shot)
  PRICE_OVERRIDE -> convert MARKET to LIMIT at specified price (one-shot)
  SIZE_OVERRIDE  -> change qty before submission (one-shot)
  FORCE_CLOSE   -> emit opposing market order immediately (one-shot)

Injectable _data_fn for testing:
  Pass `_data_fn` in params to replace `ensure_cached` with a synthetic
  DataFrame supplier. Signature: _data_fn(symbol) -> pd.DataFrame | None
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

import pandas as pd

from ..overrides import OverrideRegistry
from ..registry import register_strategy
from ..signal_bridge import (
    ichimoku_daily_signal,
    realised_vol_from_closes,
    size_from_vol_target,
)
from ..strategy import Bar, Fill, Order, OrderSide, OrderType, Strategy


_log = logging.getLogger("tradepro.paper.ichimoku_equity")


@register_strategy("ichimoku_equity")
@dataclass
class IchimokuEquityStrategy(Strategy):
    """Daily Ichimoku trend-following with regime gate + vol-targeted sizing.

    One long-or-flat position per symbol. Signal recomputed once per
    session using cached daily history; entry/exit fires MOO on the
    first bar of the new session.
    """

    # Internal state (NOT in default_params — set in __post_init__).
    _positions: dict[str, int] = field(default_factory=dict)
    _daily_signals: dict[str, tuple[float, float, dict]] = field(default_factory=dict)
    _realised_vols: dict[str, float | None] = field(default_factory=dict)
    _moo_fired: set[str] = field(default_factory=set)
    _overrides: OverrideRegistry | None = None

    @staticmethod
    def default_params() -> dict[str, Any]:
        return {
            "symbols": [],
            "sleeve_size": 20,
            "capital_usd": 100_000.0,
            "tenkan": 5,
            "kijun": 32,
            "senkou_b": 50,
            "displacement": 32,
            "target_vol": 0.12,
            "max_leverage": 1.5,
            "vol_lookback": 60,
            "regime_sma_period": 200,
            "use_regime_filter": True,
            "regime_symbol": "SPY",
            "provider": "yahoo",
            "moo_window_bars": 1,
            "_data_fn": None,
            "_override_registry": None,
        }

    # ------------------------------------------------------------------ #
    # Lifecycle                                                            #
    # ------------------------------------------------------------------ #

    def __post_init__(self) -> None:
        p = self._p()
        reg = p.get("_override_registry")
        if reg is None:
            # Module-level lazy singleton -- one registry per process,
            # shared across strategies that didn't explicitly inject one.
            reg = _default_registry()
        self._overrides = reg

    def on_session_start(self, session_date) -> None:  # type: ignore[override]
        self._daily_signals.clear()
        self._realised_vols.clear()
        self._moo_fired.clear()

    def on_bar(self, bar: Bar) -> list[Order]:
        p = self._p()
        sym = bar.symbol

        # Pause gate — short-circuit before any work.
        if self._overrides is not None and self._overrides.is_paused(self.strategy_id):
            return []

        # Symbols whitelist.
        symbols = p.get("symbols") or []
        if symbols and sym not in symbols:
            return []

        # Force-close trumps signal logic (one-shot).
        if self._overrides is not None and self._overrides.consume_force_close(
            self.strategy_id, sym
        ):
            pos = self._positions.get(sym, 0)
            if pos != 0:
                side = OrderSide.SELL if pos > 0 else OrderSide.BUY
                return [Order(
                    strategy_id=self.strategy_id,
                    symbol=sym,
                    side=side,
                    quantity=abs(pos),
                    type=OrderType.MARKET,
                    tag=f"IchimokuEquity FORCE_CLOSE {sym} qty={abs(pos)}",
                )]
            return []

        # MOO model: at most one decision per symbol per session.
        if sym in self._moo_fired:
            return []
        self._moo_fired.add(sym)

        # Compute signal + realised vol.
        signal, vol, meta = self._compute_signal(sym, bar, p)

        # Veto suppresses the resulting order (one-shot).
        if self._overrides is not None and self._overrides.consume_veto(
            self.strategy_id, sym
        ):
            return []

        position = self._positions.get(sym, 0)

        # Long entry.
        if signal >= 1.0 and position == 0:
            qty = size_from_vol_target(
                price=bar.close,
                capital=p["capital_usd"] / max(1, int(p["sleeve_size"])),
                target_vol=p["target_vol"],
                realised_vol=vol,
                max_leverage=p["max_leverage"],
            )
            if self._overrides is not None:
                size_ov = self._overrides.get_size_override(self.strategy_id, sym)
                if size_ov is not None and size_ov > 0:
                    qty = size_ov
                price_ov = self._overrides.get_price_override(self.strategy_id, sym)
            else:
                price_ov = None

            if qty <= 0:
                return []

            cloud_pos = meta.get("cloud_position", "?") if meta else "?"
            tag = (
                f"IchimokuEquity MOO entry {sym} "
                f"signal=1 cloud={cloud_pos} vol={vol:.3f}"
                if vol is not None
                else f"IchimokuEquity MOO entry {sym} signal=1 cloud={cloud_pos}"
            )

            if price_ov is not None:
                return [Order(
                    strategy_id=self.strategy_id,
                    symbol=sym,
                    side=OrderSide.BUY,
                    quantity=qty,
                    type=OrderType.LIMIT,
                    limit_price=float(price_ov),
                    tag=tag + f" LIMIT@{price_ov:.2f}",
                )]
            return [Order(
                strategy_id=self.strategy_id,
                symbol=sym,
                side=OrderSide.BUY,
                quantity=qty,
                type=OrderType.MARKET,
                tag=tag,
            )]

        # Long exit (signal flipped flat while we held).
        if signal < 1.0 and position > 0:
            return [Order(
                strategy_id=self.strategy_id,
                symbol=sym,
                side=OrderSide.SELL,
                quantity=position,
                type=OrderType.MARKET,
                tag=f"IchimokuEquity MOO exit {sym} signal=0",
            )]

        return []

    def on_fill(self, fill: Fill) -> None:
        prev = self._positions.get(fill.symbol, 0)
        if fill.side == OrderSide.BUY:
            self._positions[fill.symbol] = prev + fill.quantity
        else:
            self._positions[fill.symbol] = prev - fill.quantity

    def on_session_end(self, session_date) -> None:  # type: ignore[override]
        held = {s: q for s, q in self._positions.items() if q != 0}
        if held:
            _log.info(
                "IchimokuEquity session_end holdings: %s",
                ", ".join(f"{s}={q}" for s, q in held.items()),
            )

    # ------------------------------------------------------------------ #
    # Internal helpers                                                    #
    # ------------------------------------------------------------------ #

    def _p(self) -> dict[str, Any]:
        return {**self.default_params(), **(self.params or {})}

    def _fetch_df(self, symbol: str, p: dict[str, Any]) -> pd.DataFrame | None:
        """Pluggable data lookup. Tests inject `_data_fn`; production
        falls back to the on-disk cache (no live network call here)."""
        fn: Callable[[str], pd.DataFrame | None] | None = p.get("_data_fn")
        if fn is not None:
            try:
                return fn(symbol)
            except Exception as exc:  # noqa: BLE001
                _log.debug("IchimokuEquity _data_fn failed for %s: %s", symbol, exc)
                return None

        try:
            from ...cache import ensure_cached
            end = datetime.now(timezone.utc)
            # Enough history for Ichimoku cloud + 200-SMA regime + 60d vol.
            start = end - timedelta(days=700)
            return ensure_cached(
                p.get("provider", "yahoo"), symbol, start, end, interval="1d"
            )
        except Exception as exc:  # noqa: BLE001
            _log.debug("IchimokuEquity cache fetch failed for %s: %s", symbol, exc)
            return None

    def _compute_signal(
        self,
        symbol: str,
        bar: Bar,
        p: dict[str, Any],
    ) -> tuple[float, float | None, dict]:
        """Returns (signal_0_or_1, realised_vol, metadata). Memoised per session."""
        if symbol in self._daily_signals:
            sig, vol, meta = self._daily_signals[symbol]
            return sig, (vol if vol > 0 else None), meta

        df = self._fetch_df(symbol, p)
        if df is None or df.empty:
            self._daily_signals[symbol] = (0.0, 0.0, {})
            self._realised_vols[symbol] = None
            return 0.0, None, {}

        # Normalise column names: indicators expect lower-case high/low/close.
        cols = {c.lower(): c for c in df.columns}
        try:
            high = df[cols["high"]]
            low = df[cols["low"]]
            close = df[cols["close"]]
        except KeyError:
            self._daily_signals[symbol] = (0.0, 0.0, {})
            return 0.0, None, {}

        signal, meta = ichimoku_daily_signal(
            high=high,
            low=low,
            close=close,
            tenkan=p["tenkan"],
            kijun=p["kijun"],
            senkou_b=p["senkou_b"],
            displacement=p["displacement"],
        )

        # Regime gate -- only blocks NEW long entries.
        if p.get("use_regime_filter", True) and signal >= 1.0:
            if not self._regime_ok(p):
                signal = 0.0
                meta = {**meta, "regime_block": True}

        # Vol for sizing.
        lookback = int(p.get("vol_lookback", 60))
        closes_for_vol = close.tail(lookback + 1).tolist()
        vol = realised_vol_from_closes(closes_for_vol)
        self._realised_vols[symbol] = vol
        self._daily_signals[symbol] = (signal, vol or 0.0, meta)
        return signal, vol, meta

    def _regime_ok(self, p: dict[str, Any]) -> bool:
        """SPY > 200-SMA = GREEN, allow new longs. Missing data = OK
        (don't block trading because the regime feed is broken)."""
        regime_sym = p.get("regime_symbol", "SPY")
        df = self._fetch_df(regime_sym, p)
        if df is None or df.empty:
            return True
        cols = {c.lower(): c for c in df.columns}
        if "close" not in cols:
            return True
        close = df[cols["close"]]
        period = int(p.get("regime_sma_period", 200))
        if len(close) < period:
            return True
        sma = close.tail(period).mean()
        return float(close.iloc[-1]) > float(sma)


# ---------------------------------------------------------------------- #
# Process-wide default registry — strategies that don't inject their own  #
# share this one so manual overrides from the trader UI propagate to all. #
# ---------------------------------------------------------------------- #

_DEFAULT_REGISTRY: OverrideRegistry | None = None


def _default_registry() -> OverrideRegistry:
    global _DEFAULT_REGISTRY
    if _DEFAULT_REGISTRY is None:
        _DEFAULT_REGISTRY = OverrideRegistry()
    return _DEFAULT_REGISTRY


__all__ = ["IchimokuEquityStrategy"]

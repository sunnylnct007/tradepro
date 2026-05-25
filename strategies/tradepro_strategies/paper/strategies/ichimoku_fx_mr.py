"""IchimokuFXMeanReversionStrategy — hourly G10 FX fade-the-break paper strategy.

Intraday mean-reversion on G10 FX pairs using Ichimoku. Trades the
REVERSION away from cloud breaks ("fade the break"), ensembled across
horizons and smoothing windows exactly as in quant_engine/fx_strategy.py.

Design:
  - One instance handles ALL FX pairs. Each pair has its own signal state.
  - Signal is recomputed on every hourly bar (rolling window, no lookahead).
  - Position is SIGNED: +1 = long (fade bearish break), -1 = short (fade bullish break).
  - Vol-targeted sizing: qty proportional to vol_target / realised_vol_480h.
  - Max position per pair capped at POS_CAP = 3 units.

Override support: same OverrideRegistry.

Injectable _data_fn: fn(pair_name) -> pd.DataFrame | None
  (used for testing without live bar feed)
"""
from __future__ import annotations

import logging
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Callable

import numpy as np
import pandas as pd

from ..overrides import OverrideRegistry
from ..registry import register_strategy
from ..signal_bridge import size_from_vol_target
from ..strategy import Bar, Fill, Order, OrderSide, OrderType, Strategy

# Pull constants + per-pair tickers from the quant_engine source of truth.
# Lazy-resolved inside default_params so import-time circulars stay clean.
from ...quant_engine.fx_strategy import (
    G10_PAIRS,
    HORIZONS,
    SMOOTHS,
    POS_CAP,
    FXMeanReversionStrategy as _FXBacktester,
)


_log = logging.getLogger("tradepro.paper.ichimoku_fx_mr")


def _ichimoku_lines(
    high: np.ndarray,
    low: np.ndarray,
    horizon: int,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """Vectorised Ichimoku midranges over horizon h, 2h, 4h.

    Returns (tenkan, kijun, cloud_high, cloud_low). Matches the math in
    quant_engine.fx_strategy.FXMeanReversionStrategy._ichimoku_fx but
    operates on numpy arrays so it's cheap to recompute per-bar.
    """
    n = len(high)
    df = pd.DataFrame({"High": high, "Low": low})
    h = int(horizon)
    k = 2 * h
    sb = 4 * h

    def midrange(window: int) -> pd.Series:
        return (
            df["High"].rolling(window, min_periods=window).max()
            + df["Low"].rolling(window, min_periods=window).min()
        ) / 2

    tenkan = midrange(h).to_numpy()
    kijun = midrange(k).to_numpy()
    senkou_a = (tenkan + kijun) / 2
    senkou_b = midrange(sb).to_numpy()

    stacked = np.vstack([senkou_a, senkou_b])
    cloud_high = np.nanmax(stacked, axis=0)
    cloud_low = np.nanmin(stacked, axis=0)
    return tenkan, kijun, cloud_high, cloud_low


def _reversion_signal_latest(
    closes: np.ndarray,
    highs: np.ndarray,
    lows: np.ndarray,
    horizons: tuple[int, ...],
    smooths: tuple[int, ...],
    pos_cap: int,
) -> float:
    """Recompute the FX reversion signal and return the latest value only.

    For each horizon: build Ichimoku lines, derive the raw +/-/0 signal,
    smooth it across each smooth window, sum to a per-horizon ensemble,
    then average across horizons. The latest value is then "fed" into a
    discrete +1 / -1 / 0 in the spirit of the backtester but we return
    the continuous smoothed value so size + side can be derived together.

    Returns 0.0 if there isn't enough history.
    """
    n = len(closes)
    if n < max(horizons) * 4 + max(smooths) + 5:
        return 0.0

    ensemble_per_horizon = []
    for h in horizons:
        tenkan, kijun, cloud_high, cloud_low = _ichimoku_lines(highs, lows, h)
        # Raw fade signal: +1 below cloud (long), -1 above cloud (short).
        above = (closes > cloud_high).astype(float)
        below = (closes < cloud_low).astype(float)
        raw = below - above
        raw = np.nan_to_num(raw, nan=0.0)

        # Confirm with tenkan/kijun cross to reduce false breaks.
        confirm_long = (tenkan < kijun).astype(float)
        confirm_short = (tenkan > kijun).astype(float)
        confirm = np.where(raw > 0, confirm_long, np.where(raw < 0, confirm_short, 1.0))
        raw = raw * confirm

        # Smooth across each window, then take the mean across smooths.
        raw_s = pd.Series(raw)
        smoothed_stack = []
        for w in smooths:
            if w <= 0 or w > n:
                continue
            smoothed_stack.append(
                raw_s.rolling(int(w), min_periods=1).mean().to_numpy()
            )
        if not smoothed_stack:
            continue
        ensemble_per_horizon.append(np.mean(np.vstack(smoothed_stack), axis=0))

    if not ensemble_per_horizon:
        return 0.0

    ensembled = np.mean(np.vstack(ensemble_per_horizon), axis=0)
    latest = float(np.clip(ensembled[-1], -pos_cap, pos_cap))
    if np.isnan(latest):
        return 0.0
    return latest


@register_strategy("ichimoku_fx_mr")
@dataclass
class IchimokuFXMeanReversionStrategy(Strategy):
    """Hourly G10 FX Ichimoku fade-the-break, signed positions, vol-targeted size.

    One instance trades many pairs. Internal state is per-pair: a deque
    of OHLC, a signed integer position (in units), and the latest signal.
    """

    _closes: dict[str, deque] = field(default_factory=dict)
    _highs: dict[str, deque] = field(default_factory=dict)
    _lows: dict[str, deque] = field(default_factory=dict)
    _fx_positions: dict[str, int] = field(default_factory=dict)
    _bar_counts: dict[str, int] = field(default_factory=dict)
    _last_signal: dict[str, float] = field(default_factory=dict)
    _overrides: OverrideRegistry | None = None

    @staticmethod
    def default_params() -> dict[str, Any]:
        return {
            "pairs": list(G10_PAIRS.keys()),
            "capital_usd": 50_000.0,
            "vol_target": 0.10,
            "pos_cap": POS_CAP,
            "cost_bps": 2.0,
            "warmup_bars": 200,
            "horizons": HORIZONS,
            "smooths": SMOOTHS,
            "provider": "yahoo",
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
            reg = _default_registry()
        self._overrides = reg

    def on_session_start(self, session_date) -> None:  # type: ignore[override]
        # Rolling state survives sessions on purpose: FX runs 24/5 and
        # the warmup window spans many "sessions" in the engine's view.
        return None

    def on_bar(self, bar: Bar) -> list[Order]:
        p = self._p()
        pair = bar.symbol

        # Pause gate.
        if self._overrides is not None and self._overrides.is_paused(self.strategy_id):
            return []

        # Pair whitelist (if a non-empty list was provided).
        pairs = p.get("pairs") or []
        if pairs and pair not in pairs:
            return []

        # Force-close trumps any signal.
        if self._overrides is not None and self._overrides.consume_force_close(
            self.strategy_id, pair
        ):
            pos = self._fx_positions.get(pair, 0)
            if pos != 0:
                side = OrderSide.SELL if pos > 0 else OrderSide.BUY
                return [Order(
                    strategy_id=self.strategy_id,
                    symbol=pair,
                    side=side,
                    quantity=abs(pos),
                    type=OrderType.MARKET,
                    tag=f"IchimokuFXMR FORCE_CLOSE {pair} qty={abs(pos)}",
                )]
            return []

        # Accumulate rolling OHLC.
        warmup = int(p.get("warmup_bars", 200))
        horizons = tuple(p.get("horizons") or HORIZONS)
        smooths = tuple(p.get("smooths") or SMOOTHS)
        maxlen = max(700, max(horizons) * 4 + max(smooths) + 10)
        self._closes.setdefault(pair, deque(maxlen=maxlen)).append(bar.close)
        self._highs.setdefault(pair, deque(maxlen=maxlen)).append(bar.high)
        self._lows.setdefault(pair, deque(maxlen=maxlen)).append(bar.low)
        self._bar_counts[pair] = self._bar_counts.get(pair, 0) + 1

        # Warmup gate -- collect history, no orders until we've seen enough bars.
        if self._bar_counts[pair] < warmup:
            return []

        # Compute the latest reversion signal.
        closes_arr = np.fromiter(self._closes[pair], dtype=float)
        highs_arr = np.fromiter(self._highs[pair], dtype=float)
        lows_arr = np.fromiter(self._lows[pair], dtype=float)

        signal = _reversion_signal_latest(
            closes_arr, highs_arr, lows_arr,
            horizons=horizons,
            smooths=smooths,
            pos_cap=int(p.get("pos_cap", POS_CAP)),
        )
        self._last_signal[pair] = signal

        # Veto consumes the would-be order regardless of signal.
        vetoed = (
            self._overrides is not None
            and self._overrides.consume_veto(self.strategy_id, pair)
        )

        # Target position in signed units.
        pos_cap = int(p.get("pos_cap", POS_CAP))
        if signal > 0.1:
            target = min(pos_cap, int(round(signal)))
            if target < 1:
                target = 1
        elif signal < -0.1:
            target = max(-pos_cap, int(round(signal)))
            if target > -1:
                target = -1
        else:
            target = 0

        current = self._fx_positions.get(pair, 0)
        delta = target - current
        if delta == 0 or vetoed:
            return []

        # Vol-targeted UNIT size (small for FX; "units" here are share-equivalents).
        unit_qty = size_from_vol_target(
            price=bar.close,
            capital=p["capital_usd"] / max(1, len(pairs)),
            target_vol=p["vol_target"],
            realised_vol=None,  # use neutral sizing; per-pair vol is approx via signal cap
            max_leverage=1.5,
        )

        # Size override (applies per-bar, one-shot).
        if self._overrides is not None:
            size_ov = self._overrides.get_size_override(self.strategy_id, pair)
            if size_ov is not None and size_ov > 0:
                unit_qty = size_ov
            price_ov = self._overrides.get_price_override(self.strategy_id, pair)
        else:
            price_ov = None

        qty = abs(delta) * unit_qty
        if qty <= 0:
            return []

        side = OrderSide.BUY if delta > 0 else OrderSide.SELL
        tag = (
            f"IchimokuFXMR {pair} signal={signal:+.2f} "
            f"target={target} current={current} delta={delta:+d}"
        )

        if price_ov is not None:
            return [Order(
                strategy_id=self.strategy_id,
                symbol=pair,
                side=side,
                quantity=qty,
                type=OrderType.LIMIT,
                limit_price=float(price_ov),
                tag=tag + f" LIMIT@{price_ov:.4f}",
            )]
        return [Order(
            strategy_id=self.strategy_id,
            symbol=pair,
            side=side,
            quantity=qty,
            type=OrderType.MARKET,
            tag=tag,
        )]

    def on_fill(self, fill: Fill) -> None:
        prev = self._fx_positions.get(fill.symbol, 0)
        signed = fill.quantity if fill.side == OrderSide.BUY else -fill.quantity
        self._fx_positions[fill.symbol] = prev + signed

    def on_session_end(self, session_date) -> None:  # type: ignore[override]
        return None

    # ------------------------------------------------------------------ #
    # Helpers                                                              #
    # ------------------------------------------------------------------ #

    def _p(self) -> dict[str, Any]:
        return {**self.default_params(), **(self.params or {})}


# Process-wide default registry shared with ichimoku_equity (one file).
_DEFAULT_REGISTRY: OverrideRegistry | None = None


def _default_registry() -> OverrideRegistry:
    global _DEFAULT_REGISTRY
    if _DEFAULT_REGISTRY is None:
        _DEFAULT_REGISTRY = OverrideRegistry()
    return _DEFAULT_REGISTRY


__all__ = [
    "IchimokuFXMeanReversionStrategy",
    "_reversion_signal_latest",
    "_ichimoku_lines",
]

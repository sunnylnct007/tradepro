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

LLM signal gate (optional, fail_open by default):
  New ENTRIES from flat (current == 0) are evaluated before order emission.
  VETOED  -> entry suppressed; exits always pass through.
  BOOSTED -> unit_qty scaled by scale_factor.
  Pass `_llm_gate` in params to inject a pre-built gate for testing.

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

from ..llm_gate import GateDecision, LLMSignalGate
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

    source = "trader-quant"
    caveats = [
        "DESIGN-LIMITED. Ichimoku is a TREND-confirmation tool "
        "originally tuned for daily Japanese equities. Using it for "
        "intraday FX mean-reversion is contrarian to its design and "
        "breaks down when EUR/USD / GBP/USD trends.",
        "Single-indicator at hourly bars — the 26-bar displacement "
        "lags real price by 26h. By the time the cloud shifts the MR "
        "opportunity is often gone.",
        "Missing: vol-regime filter (ATR z-score), session filter "
        "(London/NY overlap), pairs cointegration. Production FX MR "
        "usually layers all three on top of any single indicator.",
        "Roadmap: ichimoku_fx_mr_v2 keeps Ichimoku as a regime filter "
        "+ adds Bollinger Bands(20) + RSI(14) + ATR-based stop. Ask the "
        "quant before relying on v1 for live capital.",
    ]
    # Bars-needed (2573 of 1h) means default_lookback_days=200.
    default_lookback_days = 200

    _closes: dict[str, deque] = field(default_factory=dict)
    _highs: dict[str, deque] = field(default_factory=dict)
    _lows: dict[str, deque] = field(default_factory=dict)
    _fx_positions: dict[str, int] = field(default_factory=dict)
    _bar_counts: dict[str, int] = field(default_factory=dict)
    _last_signal: dict[str, float] = field(default_factory=dict)
    _overrides: OverrideRegistry | None = None
    _gate: LLMSignalGate | None = None

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
            # Injectable LLMSignalGate — set for tests or leave None to
            # disable the LLM layer. Production uses StrategyRunner to inject.
            "_llm_gate": None,
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
        self._gate = p.get("_llm_gate") or None

    def on_session_start(self, session_date) -> None:  # type: ignore[override]
        # Rolling state survives sessions on purpose: FX runs 24/5 and
        # the warmup window spans many "sessions" in the engine's view.
        # However, when params.initial_positions is supplied (intraday
        # daemon path), seed in case seed_positions wasn't called.
        p = self._p()
        initial = p.get("initial_positions") or {}
        if isinstance(initial, dict) and initial:
            for pair, qty in initial.items():
                try:
                    self._fx_positions[pair] = int(qty)
                except (TypeError, ValueError):
                    continue
        return None

    def seed_positions(self, positions: dict[str, int]) -> None:  # type: ignore[override]
        """Seed signed unit positions per pair so reruns compute the
        right delta (target - current) instead of re-emitting full
        entries on every run. Wired into paper_session via
        /api/oms/positions. See task #28."""
        for pair, qty in positions.items():
            self._fx_positions[pair] = int(qty)

    def on_bar(self, bar: Bar) -> list[Order]:
        p = self._p()
        pair = bar.symbol

        # Pause gate.
        if self._overrides is not None and self._overrides.is_paused(self.strategy_id):
            self.log_decision(
                symbol=pair, bar_ts=bar.timestamp,
                action="skip-paused",
                reason="strategy is paused via overrides registry",
            )
            return []

        # Pair whitelist (if a non-empty list was provided).
        pairs = p.get("pairs") or []
        if pairs and pair not in pairs:
            self.log_decision(
                symbol=pair, bar_ts=bar.timestamp,
                action="skip-not-whitelisted",
                reason=f"pair {pair} not in configured whitelist",
                whitelist_size=len(pairs),
            )
            return []

        # Force-close trumps any signal.
        if self._overrides is not None and self._overrides.consume_force_close(
            self.strategy_id, pair
        ):
            pos = self._fx_positions.get(pair, 0)
            if pos != 0:
                side = OrderSide.SELL if pos > 0 else OrderSide.BUY
                self.log_decision(
                    symbol=pair, bar_ts=bar.timestamp,
                    action="fire-force-close",
                    reason="override registry requested force-close",
                    side=side.value, quantity=abs(pos),
                )
                return [Order(
                    strategy_id=self.strategy_id,
                    symbol=pair,
                    side=side,
                    quantity=abs(pos),
                    type=OrderType.MARKET,
                    tag=f"IchimokuFXMR FORCE_CLOSE {pair} qty={abs(pos)}",
                )]
            self.log_decision(
                symbol=pair, bar_ts=bar.timestamp,
                action="skip-force-close-flat",
                reason="force-close requested but position already flat",
            )
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
            self.log_decision(
                symbol=pair, bar_ts=bar.timestamp,
                action="skip-warmup",
                reason=f"warmup {self._bar_counts[pair]}/{warmup} bars",
                bars_seen=self._bar_counts[pair],
                bars_required=warmup,
            )
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
        if vetoed:
            self.log_decision(
                symbol=pair, bar_ts=bar.timestamp,
                action="skip-vetoed",
                reason="override registry vetoed this bar",
                signal=signal, target=target, current=current,
            )
            return []
        if delta == 0:
            self.log_decision(
                symbol=pair, bar_ts=bar.timestamp,
                action="skip-no-delta",
                reason="target position matches current — nothing to do",
                signal=signal, target=target, current=current,
            )
            return []

        # ── LLM signal gate — only on NEW entries from flat ─────────────
        # Exits (delta moves back towards 0 from an open position) are never
        # gated: we can always reduce/close a position. Entries from flat
        # (current == 0) are evaluated: VETOED suppresses the order;
        # APPROVED_BOOSTED scales the unit_qty.
        llm_scale = 1.0
        if current == 0 and self._gate is not None:
            gate_decision = self._gate.evaluate(pair, float(abs(target)))
            if gate_decision.action == GateDecision.VETOED:
                _log.info(
                    "IchimokuFXMR LLM gate VETOED %s: %s", pair, gate_decision.reason
                )
                self.log_decision(
                    symbol=pair, bar_ts=bar.timestamp,
                    action="skip-llm-vetoed",
                    reason=f"LLM gate vetoed: {gate_decision.reason}",
                    signal=signal, target=target,
                )
                return []
            llm_scale = gate_decision.scale_factor
        # ────────────────────────────────────────────────────────────────

        # Vol-targeted UNIT size (small for FX; "units" here are share-equivalents).
        unit_qty = size_from_vol_target(
            price=bar.close,
            capital=p["capital_usd"] / max(1, len(pairs)),
            target_vol=p["vol_target"],
            realised_vol=None,  # use neutral sizing; per-pair vol is approx via signal cap
            max_leverage=1.5,
        )
        # Apply LLM boost before human overrides.
        unit_qty = int(unit_qty * llm_scale)

        # Size override (applies per-bar, one-shot; beats LLM scale).
        if self._overrides is not None:
            size_ov = self._overrides.get_size_override(self.strategy_id, pair)
            if size_ov is not None and size_ov > 0:
                unit_qty = size_ov
            price_ov = self._overrides.get_price_override(self.strategy_id, pair)
        else:
            price_ov = None

        qty = abs(delta) * unit_qty
        if qty <= 0:
            self.log_decision(
                symbol=pair, bar_ts=bar.timestamp,
                action="skip-zero-qty",
                reason="vol-target sizing rounded to 0 units",
                signal=signal, target=target, current=current,
                unit_qty=unit_qty,
            )
            return []

        side = OrderSide.BUY if delta > 0 else OrderSide.SELL
        tag = (
            f"IchimokuFXMR {pair} signal={signal:+.2f} "
            f"target={target} current={current} delta={delta:+d}"
        )
        action_label = "fire-buy" if side == OrderSide.BUY else "fire-sell"
        self.log_decision(
            symbol=pair, bar_ts=bar.timestamp,
            action=action_label,
            reason=f"signal {signal:+.2f} → target {target:+d} from current {current:+d}",
            signal=signal, target=target, current=current, delta=delta,
            quantity=qty, order_type="LIMIT" if price_ov is not None else "MARKET",
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

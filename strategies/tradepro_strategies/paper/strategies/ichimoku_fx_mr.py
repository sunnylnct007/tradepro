"""IchimokuFXMeanReversionStrategy — hourly G10 FX fade-the-break paper strategy.

Intraday mean-reversion on G10 FX pairs using Ichimoku. Trades the
REVERSION away from cloud breaks ("fade the break").

SIGNAL FIDELITY — the signal is a VERBATIM port of the trader's spec
(docs/main 3.py), kept in `_fx_trader_signal.py` and pinned by
`tests/test_fx_signal_parity.py`. The live strategy calls
`latest_target_position()` on its rolling window each bar and acts on the last
value, so it cannot drift. The trader's logic, exactly:
  - Classic Ichimoku with STANDARD periods (tenkan 9, kijun 26, senkou_b 52,
    26-bar forward shift) — NOT the horizon as the lookback.
  - A break needs THREE conditions: price vs cloud AND tenkan/kijun cross AND
    the chikou (lagging-span) confirmation.
  - Edge-TRIGGERED: a unit is stacked at the bar a break first fires and held
    for the HORIZON (336–624h ≈ 2–3.5 weeks), ensembled across all 8 horizons.
  - Smoothed across SMOOTHS (24/48/72h), then vol-scaled (vol_target / realised
    vol over 480h) and clipped to ±POS_CAP.

(A prior streaming re-implementation drifted from all of the above — see the
DEAD CODE banner on `_ichimoku_lines` below. Fixed 2026-06-03.)

Design:
  - One instance handles ALL FX pairs. Each pair has its own signal state.
  - Signal is recomputed on every hourly bar (rolling window, no lookahead).
  - Position is SIGNED: +1 = long (fade bearish break), -1 = short (fade bullish break).
  - Vol-targeting is carried in the position MAGNITUDE (units 1–3); the per-unit
    notional is one capital-leg per pair.
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
import warnings
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
    FXMeanReversionStrategy as _FXBacktester,
)
# The SIGNAL is a verbatim port of the trader's spec (docs/main 3.py), kept in
# _fx_trader_signal so it cannot drift. HORIZONS/SMOOTHS/POS_CAP come from there
# too, so constants + math are a single source of truth (parity-tested).
from ._fx_trader_signal import (
    HORIZONS,
    SMOOTHS,
    POS_CAP,
    MIN_BARS as _TRADER_MIN_BARS,
    latest_target_position,
)


_log = logging.getLogger("tradepro.paper.ichimoku_fx_mr")


def _fx_market_open(ts) -> bool:
    """Is the spot-FX market open at `ts` (treated as UTC)?

    Spot FX runs 24/5: it opens Sunday ~21:00 UTC (Sydney) and closes
    Friday ~21:00 UTC (NY). It is CLOSED all day Saturday and Sunday
    until the evening. We use 21:00 UTC as the boundary (ignoring the
    1h DST wobble — the strategy trades hourly bars, so an hour either
    side of the weekend boundary is immaterial and erring closed is
    safe). Note: IG offers SEPARATE weekend FX/index CFDs, but this
    strategy trades the weekday MINI epics.
    """
    try:
        wd = ts.weekday()  # Mon=0 … Sat=5, Sun=6
        h = ts.hour
    except AttributeError:
        return True  # unknown timestamp shape — don't block
    if wd == 5:            # Saturday — closed
        return False
    if wd == 6:            # Sunday — opens ~21:00 UTC
        return h >= 21
    if wd == 4:            # Friday — closes ~21:00 UTC
        return h < 21
    return True            # Mon–Thu


# ⚠️ DEAD CODE — DO NOT USE OR RE-WIRE. ⚠️
# _ichimoku_lines / _reversion_signal_latest below are the OLD streaming
# reinterpretation of the FX signal that DRIFTED from the trader's spec: they
# mis-used HORIZONS as Ichimoku lookbacks (not holding periods), dropped the
# chikou condition, and used instantaneous cloud state instead of edge-triggered
# holds. The live signal now comes from `_fx_trader_signal.latest_target_position`
# (a verbatim port of docs/main 3.py, parity-tested). These remain only so old
# references don't NameError; they are no longer called. Delete once confirmed
# unused everywhere.
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
    # Warmup columns (before the rolling windows fill) are all-NaN, so
    # nanmax/nanmin emit a benign "All-NaN slice encountered" RuntimeWarning
    # per call — noisy in the FX logs every bar. The NaN result is intended
    # (those bars are pre-warmup and ignored downstream via nan_to_num in the
    # signal), so suppress just that warning here.
    with warnings.catch_warnings():
        warnings.filterwarnings("ignore", r"All-NaN slice encountered", RuntimeWarning)
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
        "FIDELITY: the signal is a verbatim port of the trader's spec "
        "(docs/main 3.py), pinned by a parity test. A prior version had "
        "drifted (HORIZONS mis-used as Ichimoku lookbacks, chikou dropped, "
        "state instead of edge-triggered holds) — fixed 2026-06-03.",
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
    # Bars-needed is now ~774 of 1h (≈32 days) with the trader-faithful signal
    # (cloud + 624h hold + smooth + 480h vol). 200 lookback-days is ample.
    default_lookback_days = 200

    _closes: dict[str, deque] = field(default_factory=dict)
    _highs: dict[str, deque] = field(default_factory=dict)
    _lows: dict[str, deque] = field(default_factory=dict)
    _times: dict[str, deque] = field(default_factory=dict)  # bar timestamps, for charts
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
            # Phase C-3.2 — injectable CatalystFetcher. None ⇒ no overlay.
            "_catalyst_fetcher": None,
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
        self._catalyst_fetcher = p.get("_catalyst_fetcher") or None

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

    def seed_positions(self, positions: dict[str, int],  # type: ignore[override]
                       avg_prices: dict[str, float] | None = None) -> None:
        """Seed signed unit positions per pair so reruns compute the
        right delta (target - current) instead of re-emitting full
        entries on every run. Wired into paper_session via
        /api/oms/positions. See task #28.

        `avg_prices` forwarded to super() for cost-basis seeding (FX MR
        has no stop-loss exit today, so it's a trading no-op, but keeps
        the seed signature uniform across the call site).

        Calls super() so the base class also populates self.positions
        which the engine's risk gate reads — otherwise sell-to-flat
        on held longs gets rejected as 'short_disallowed' (#86).
        """
        super().seed_positions(positions, avg_prices)
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
        warmup_arg = int(p.get("warmup_bars", 200))
        horizons = tuple(p.get("horizons") or HORIZONS)
        smooths = tuple(p.get("smooths") or SMOOTHS)
        # Warmup = what the trader's signal needs to be representative: the
        # cloud (senkou_b 52 + 26 shift), the longest HOLDING horizon (624),
        # the longest smooth (72) and the 480h vol lookback all filled
        # (_fx_trader_signal.MIN_BARS ≈ 774). NOTE: HORIZONS are HOLDING
        # PERIODS here, NOT Ichimoku lookbacks — the old code mis-used them as
        # lookbacks (×4 for senkou_b ⇒ ~2578 bars), which data-starved 8/10
        # pairs to a silent-zero signal. With ~800 warmup the book trades.
        bars_needed = _TRADER_MIN_BARS
        warmup = max(warmup_arg, bars_needed)
        maxlen = max(800, bars_needed + 10)
        self._closes.setdefault(pair, deque(maxlen=maxlen)).append(bar.close)
        self._highs.setdefault(pair, deque(maxlen=maxlen)).append(bar.high)
        self._lows.setdefault(pair, deque(maxlen=maxlen)).append(bar.low)
        self._times.setdefault(pair, deque(maxlen=maxlen)).append(bar.timestamp)
        self._bar_counts[pair] = self._bar_counts.get(pair, 0) + 1

        # Warmup gate -- no orders until enough bars for the FULL ensemble.
        if self._bar_counts[pair] < warmup:
            # Loudly flag the dangerous middle ground: past the configured
            # warmup_bars gate but still short of what the ensemble needs —
            # the exact silent-zero condition. Fire once (on crossing).
            if self._bar_counts[pair] == warmup_arg and warmup_arg < bars_needed:
                _log.warning(
                    "ichimoku_fx_mr DATA-STARVED for %s: %d bars, but the "
                    "trader's signal needs %d (cloud + 624h hold + smooth + "
                    "480h vol). Signals stay 0 until then — deepen "
                    "--lookback-days or use the bar-cache.",
                    pair, self._bar_counts[pair], bars_needed,
                )
            self.log_decision(
                symbol=pair, bar_ts=bar.timestamp,
                action="skip-warmup",
                reason=(f"warmup {self._bar_counts[pair]}/{warmup} bars "
                        f"(longest horizon {max(horizons)}h needs {bars_needed})"),
                bars_seen=self._bar_counts[pair],
                bars_required=warmup,
            )
            return []

        # Market-hours guard — spot FX is 24/5, CLOSED on weekends. Never
        # emit an order into a closed venue (it just gets rejected
        # MARKET_CLOSED and, pre-fix, spammed duplicates). Force-close
        # above is exempt (operator intent); signal-driven entries/exits
        # are not. "Don't send orders when the market is closed."
        if not _fx_market_open(bar.timestamp):
            self.log_decision(
                symbol=pair, bar_ts=bar.timestamp,
                action="skip-market-closed",
                reason="FX market closed (weekend) — not sending orders",
            )
            return []

        # Trade ONLY on the live (latest) bar. The historical lookback was
        # accumulated into the deques above purely as indicator WARMUP — it is
        # NOT a stream of tradeable moments. Acting on every replayed bar made
        # the strategy fire 357×/run as the mean-reversion signal flipped over
        # history, churn the broker, and reach skip-no-delta by the live bar.
        # The bus marks the final bar per symbol is_live=True.
        if not bar.is_live:
            self.log_decision(
                symbol=pair, bar_ts=bar.timestamp,
                action="skip-warmup-bar",
                reason="historical lookback (indicator warmup); trade fires on the live bar",
            )
            return []

        # Latest target position from the trader's VERBATIM signal pipeline
        # (ichimoku → reversion_signal → vol_scale, in _fx_trader_signal). It
        # returns the vol-scaled position already clipped to [-POS_CAP, POS_CAP];
        # we round it to integer units below. This is the single source of
        # truth — parity-tested against an independent copy of docs/main 3.py.
        closes_arr = np.fromiter(self._closes[pair], dtype=float)
        highs_arr = np.fromiter(self._highs[pair], dtype=float)
        lows_arr = np.fromiter(self._lows[pair], dtype=float)

        signal = latest_target_position(closes_arr, highs_arr, lows_arr)
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
        # Deal-mode IG netting guard. The broker seeds `current` as only the
        # SIGN (+/-1) of the net per pair — IG mini-lots can't be round-tripped
        # to the strategy's 1-3 "units" without the per-pair contract size, and
        # IG opens a NEW deal per order (never nets). So once we already hold a
        # pair in the TARGET DIRECTION we HOLD it, rather than re-adding every
        # run: otherwise a multi-unit target (e.g. -3) against a +/-1 seed yields
        # a non-zero delta forever and stacks duplicate deals. We act only on
        # flat->enter or a direction FLIP. (Full 1-3 unit magnitude fidelity
        # needs mini-lot-native sizing + close-on-reduce — tracked follow-up.)
        if current != 0 and target != 0 and (current > 0) == (target > 0):
            delta = 0
        else:
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
            # Phase C-3.2 — pass catalysts only when a fetcher was
            # injected. No-fetcher path preserves the 2-arg call
            # signature so legacy gate stubs in tests + third-party
            # mocks don't trip on a new kwarg.
            if self._catalyst_fetcher is not None:
                gate_decision = self._gate.evaluate(
                    pair, float(abs(target)),
                    catalysts=self._catalyst_fetcher.fetch(pair),
                )
            else:
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

        # Optimistically advance our tracked position to the target the
        # MOMENT we emit, so the next bar computes delta=0 and we do NOT
        # re-fire the same order while the fill is outstanding. Live
        # brokers (IG/T212) don't fill synchronously — without this the
        # strategy re-emits every bar (current stays stale), producing the
        # duplicate-order flood. The broker stays the golden source:
        # seed_positions re-syncs from it at the next session start, so an
        # optimistic position that didn't actually fill self-corrects.
        self._fx_positions[pair] = target

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
        # Position is tracked in SIGNED TARGET UNITS (±1..pos_cap) and is
        # advanced optimistically when we emit (see on_bar) + reconciled
        # from the broker at session start (seed_positions). We deliberately
        # do NOT add the raw fill quantity here: fill.quantity is in order
        # units (thousands), a different scale, so adding it would corrupt
        # the ±unit book (and, combined with the old re-fire, was part of
        # the runaway). Fills are confirmations; the Ledger tracks P&L.
        return None

    def on_session_end(self, session_date) -> None:  # type: ignore[override]
        return None

    # ------------------------------------------------------------------ #
    # Helpers                                                              #
    # ------------------------------------------------------------------ #

    def _p(self) -> dict[str, Any]:
        return {**self.default_params(), **(self.params or {})}

    def recent_charts(self) -> dict[str, dict[str, Any]]:  # type: ignore[override]
        """Per-pair Ichimoku chart so the trader can SEE, per FX pair: the
        price + trend, the cloud the fade-signal trades against (standard
        9/26/52 Ichimoku — what reversion_signal uses), the Tenkan/Kijun
        lines, and a BUY/SELL triangle at the exact price each fill executed.
        The frontend overlays the avg-entry line on top. Per-pair errors are
        swallowed so one bad pair doesn't strip charts from the rest."""
        from ...viz import build_chart

        out: dict[str, dict[str, Any]] = {}
        for pair in list(self._closes.keys()):
            try:
                closes = list(self._closes.get(pair) or [])
                highs = list(self._highs.get(pair) or [])
                lows = list(self._lows.get(pair) or [])
                times = list(self._times.get(pair) or [])
                # Need enough bars for a meaningful cloud + matching timestamps.
                if len(closes) < 60 or len(times) != len(closes):
                    continue
                df = pd.DataFrame(
                    {"High": highs, "Low": lows, "Close": closes},
                    index=pd.to_datetime(times, utc=True),
                )
                out[f"ichimoku_cloud:{pair}"] = build_chart(
                    "ichimoku_cloud",
                    symbol=pair,
                    df=df,
                    fills=self.recent_fills(symbol=pair),
                    tenkan=9, kijun=26, senkou_b=52, displacement=26,
                )
            except Exception:  # noqa: BLE001
                _log.exception(
                    "ichimoku_fx_mr recent_charts failed for %s — skipping", pair,
                )
        return out


# Process-wide default registry shared with ichimoku_equity (one file).
_DEFAULT_REGISTRY: OverrideRegistry | None = None


def _default_registry() -> OverrideRegistry:
    global _DEFAULT_REGISTRY
    if _DEFAULT_REGISTRY is None:
        _DEFAULT_REGISTRY = OverrideRegistry()
    return _DEFAULT_REGISTRY


__all__ = [
    "IchimokuFXMeanReversionStrategy",
]

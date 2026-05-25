"""COMPASS Momentum — intraday swing-entry paper strategy.

Thesis:
    COMPASS (Continuous Multi-factor Alpha Scoring) identifies stocks
    scoring ≥68/100 that have superior sector-relative momentum, analyst
    tailwind, positive EPS revision, and quality fundamentals. Those names
    are *already* outperforming their peers on a daily basis — this strategy
    fades into intraday pullbacks (RSI dip below `rsi_entry`) while price
    holds above its 20-bar mean, then rides the underlying alpha.

Entry logic (all three gates must pass):
    1. COMPASS score ≥ `compass_min_score` (default 68)
    2. RSI(14) < `rsi_entry` (default 62)  — not extended/overbought
    3. bar.close > SMA-20                  — trend alignment

COMPASS is computed ONCE per symbol per session on the first bar via the
local Parquet cache (fast, no live network calls). Result is memoised in
_state; subsequent bars reuse the cached dict. Any compute failure
silently skips entry for that symbol (score treated as 0).

Exit logic (first condition to fire):
    - RSI(14) ≥ `rsi_exit` (default 72)   — exhaustion exit
    - bar.close falls below stop_price     — hard stop
    - Session-close flatten at `session_close_local`

Sizing:
    Shares = risk_per_trade_usd / (entry_price - stop_price).
    stop_price = entry - `stop_pct` × entry (default 2 %).
    target_price = entry + `rr` × (entry - stop_price) (default 2×).

Params:
    compass_min_score      — minimum COMPASS score to allow entry (default 68)
    rsi_period             — RSI lookback bars (default 14)
    sma_period             — SMA trend filter bars (default 20)
    rsi_entry              — RSI must be BELOW this to trigger (default 62)
    rsi_exit               — RSI exit (exhaustion) threshold (default 72)
    stop_pct               — % of entry price used for stop distance (default 0.02)
    rr                     — minimum reward-to-risk ratio for target (default 2.0)
    risk_per_trade_usd     — dollars risked per trade (default 100)
    session_close_local    — UTC HH:MM for EOD flatten (default "19:50")
    max_trades             — max round-trips per session (default 2)
    provider               — price data provider for COMPASS compute (default "yahoo")
"""
from __future__ import annotations

import logging
from collections import deque
from dataclasses import dataclass
from datetime import time
from typing import Any

from ..registry import register_strategy
from ..strategy import Bar, Fill, Order, OrderSide, OrderType, Strategy

_log = logging.getLogger("tradepro.paper.compass_momentum")


def _compute_rsi(closes: deque, period: int) -> float | None:
    """Wilder RSI from the last `period+1` closes in the deque.
    Returns None when there are too few bars."""
    vals = list(closes)
    if len(vals) < period + 1:
        return None
    # Use only the most-recent period+1 samples
    sample = vals[-(period + 1):]
    gains, losses = [], []
    for i in range(1, len(sample)):
        d = sample[i] - sample[i - 1]
        gains.append(max(d, 0.0))
        losses.append(max(-d, 0.0))
    avg_gain = sum(gains) / period
    avg_loss = sum(losses) / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return round(100.0 - (100.0 / (1.0 + rs)), 2)


@register_strategy("compass_momentum")
@dataclass
class CompassMomentumIntraday(Strategy):
    """Intraday pullback entry on high-COMPASS-score names.

    One long position per symbol, day-only. Enters on RSI dip while
    COMPASS confirms the underlying multi-factor alpha edge is intact.
    """

    source = "alpha-engine"

    @staticmethod
    def default_params() -> dict[str, Any]:
        return {
            "compass_min_score": 68,
            "rsi_period": 14,
            "sma_period": 20,
            "rsi_entry": 62,
            "rsi_exit": 72,
            "stop_pct": 0.02,        # 2 % of entry price
            "rr": 2.0,               # target = entry + rr × risk
            "risk_per_trade_usd": 100.0,
            "session_close_local": "19:50",
            "max_trades": 2,
            "provider": "yahoo",
        }

    # ------------------------------------------------------------------ #
    # Lifecycle                                                            #
    # ------------------------------------------------------------------ #

    def on_session_start(self, session_date) -> None:  # type: ignore[override]
        self._state.clear()
        p = self._p()
        win = max(p["sma_period"], p["rsi_period"] + 1)
        # One rolling window per symbol; initialised lazily in on_bar
        # so we don't need to know the symbol list up front.
        self.remember("closes", {})          # symbol → deque(maxlen=win)
        self.remember("compass_cache", {})   # symbol → score (float) | None
        self.remember("trades_taken", 0)
        self._win = win

    def on_bar(self, bar: Bar) -> list[Order]:
        sym = bar.symbol
        p = self._p()

        # --- Maintain rolling close window per symbol -----------------
        closes_map: dict[str, deque] = self.recall("closes") or {}
        if sym not in closes_map:
            closes_map[sym] = deque(maxlen=self._win)
        closes_map[sym].append(bar.close)
        self.remember("closes", closes_map)
        closes: deque = closes_map[sym]

        # --- EOD flatten guard ----------------------------------------
        if self._is_at_or_after_close(bar):
            return self._flatten_orders(bar)

        # --- COMPASS score — resolved once per symbol per session -----
        compass_cache: dict[str, float | None] = self.recall("compass_cache") or {}
        if sym not in compass_cache:
            compass_cache[sym] = self._resolve_compass(sym, bar, p)
            self.remember("compass_cache", compass_cache)

        compass_score = compass_cache.get(sym)

        # --- Indicators -----------------------------------------------
        rsi = _compute_rsi(closes, p["rsi_period"])
        sma = (sum(closes) / len(closes)) if len(closes) >= p["sma_period"] else None

        # --- Existing position: check exits ---------------------------
        pos = self.position_for(sym)
        if not pos.is_flat:
            return self._check_exit(bar, rsi, p)

        # --- Entry gate -----------------------------------------------
        if self.has_order_in_flight(sym):
            return []
        if self.recall("trades_taken") >= p["max_trades"]:
            return []
        # Need sufficient bar history for both indicators
        if rsi is None or sma is None:
            return []
        # COMPASS gate — treat None / missing as disqualifying
        if compass_score is None or compass_score < p["compass_min_score"]:
            return []
        # RSI must be below the entry threshold (not already extended)
        if rsi >= p["rsi_entry"]:
            return []
        # Price must be above its SMA — trend filter
        if bar.close <= sma:
            return []

        return self._build_entry(bar, rsi, sma, compass_score, p)

    def on_fill(self, fill: Fill) -> None:
        if fill.side == OrderSide.BUY:
            _log.info(
                "CompassMomentum FILL long %s qty=%d @%.2f",
                fill.symbol, fill.quantity, fill.fill_price,
            )
            trades = (self.recall("trades_taken") or 0) + 1
            self.remember("trades_taken", trades)

    def on_session_end(self, session_date) -> None:  # type: ignore[override]
        for pos in self.positions.values():
            if not pos.is_flat:
                _log.warning(
                    "CompassMomentum session_end: %s still holds %d shares — "
                    "EOD flatten may have raced bus shutdown",
                    pos.symbol, pos.quantity,
                )

    # ------------------------------------------------------------------ #
    # Internal helpers                                                    #
    # ------------------------------------------------------------------ #

    def _p(self) -> dict[str, Any]:
        return {**self.default_params(), **(self.params or {})}

    def _is_at_or_after_close(self, bar: Bar) -> bool:
        close_str = self._p()["session_close_local"]
        hh, mm = (int(x) for x in close_str.split(":"))
        return bar.timestamp.time() >= time(hh, mm)

    def _resolve_compass(self, symbol: str, bar: Bar, p: dict) -> float | None:
        """Compute COMPASS score for `symbol` via local cache. Returns None on
        any failure — caller treats None as disqualifying.

        Called at most once per symbol per session (memoised in compass_cache).
        The computation reads Parquet files from disk; no live network calls.
        """
        try:
            from ...compass_scorer import compute_compass_score
            from ...sector_rs import compute_sector_rs
            from ...eps_tracker import get_eps_revision

            # Build a minimal row dict with what we know from the current bar.
            # compare.py passes the full 'best' row; here we substitute a
            # lightweight proxy so the scorer can still run.  Factor scorers
            # that need fields missing here will gracefully return neutral (5).
            proxy_row: dict[str, Any] = {
                "symbol": symbol,
                "close": bar.close,
                "open": bar.open,
                "high": bar.high,
                "low": bar.low,
                "volume": bar.volume,
            }
            sector_rs = compute_sector_rs(symbol, provider=p.get("provider", "yahoo"))
            eps_rev = get_eps_revision(symbol)
            result = compute_compass_score(
                symbol, proxy_row,
                sector_rs_result=sector_rs,
                eps_revision=eps_rev,
            )
            score = result.score
            _log.debug(
                "CompassMomentum: %s COMPASS=%.1f signal=%s conviction=%s",
                symbol, score, result.signal, result.conviction,
            )
            return score
        except Exception as exc:  # noqa: BLE001 — never block on COMPASS failure
            _log.debug("COMPASS compute failed for %s: %s", symbol, exc)
            return None

    def _build_entry(
        self,
        bar: Bar,
        rsi: float,
        sma: float,
        compass_score: float,
        p: dict,
    ) -> list[Order]:
        """Construct the BUY order with stop + target advisory metadata."""
        entry = bar.close
        stop_price = round(entry * (1.0 - p["stop_pct"]), 4)
        risk_per_share = entry - stop_price
        if risk_per_share <= 0:
            return []

        target_price = round(entry + p["rr"] * risk_per_share, 4)
        qty_from_risk = max(1, int(p["risk_per_trade_usd"] / risk_per_share))

        # Cap by risk envelope's position-value limit
        max_pos_value = (
            self.risk.max_position_value_usd
            if self.risk and self.risk.max_position_value_usd
            else 1e9
        )
        qty_from_cap = max(1, int(max_pos_value / max(0.01, entry)))
        qty = min(qty_from_risk, qty_from_cap)

        # Persist stop for exit checking on subsequent bars
        stops: dict[str, float] = self.recall("stops") or {}
        stops[bar.symbol] = stop_price
        self.remember("stops", stops)

        confidence = round(min(compass_score / 100.0, 0.99), 3)

        tag = (
            f"CompassMomentum long · COMPASS={compass_score:.0f} "
            f"RSI={rsi:.1f} SMA={sma:.2f} "
            f"stop={stop_price:.2f} tgt={target_price:.2f} RR={p['rr']:.1f}x"
        )
        return [Order(
            strategy_id=self.strategy_id,
            symbol=bar.symbol,
            side=OrderSide.BUY,
            quantity=qty,
            type=OrderType.MARKET,
            tag=tag,
            risk_stop_price=stop_price,
            risk_target_price=target_price,
            confidence=confidence,
        )]

    def _check_exit(self, bar: Bar, rsi: float | None, p: dict) -> list[Order]:
        """Exit rules while in a long position."""
        pos = self.position_for(bar.symbol)
        if pos.is_flat:
            return []

        stops: dict[str, float] = self.recall("stops") or {}
        stop = stops.get(bar.symbol)

        # Hard stop hit
        if stop is not None and bar.low <= stop:
            return [self._close(bar, f"CompassMomentum stop hit @ {stop:.2f}")]

        # RSI exhaustion exit
        if rsi is not None and rsi >= p["rsi_exit"]:
            return [self._close(bar, f"CompassMomentum RSI exhaustion @ {rsi:.1f}")]

        return []

    def _flatten_orders(self, bar: Bar) -> list[Order]:
        out: list[Order] = []
        for pos in self.positions.values():
            if not pos.is_flat:
                out.append(self._close(bar, "CompassMomentum EOD flatten", pos=pos))
        return out

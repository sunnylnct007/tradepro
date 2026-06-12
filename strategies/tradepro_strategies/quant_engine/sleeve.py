"""Single-sleeve Ichimoku backtest with optional regime gating.

A Sleeve holds a fixed set of tickers and uses Ichimoku signals to
decide long/flat per ticker. The sleeve weight for each ticker is
1/sleeve_size when long and 0 when flat. Total exposure is capped at
1x (no leverage at the sleeve level — leverage happens at the ensemble
level via vol targeting).

Transaction costs are charged on each change in position weight:
  cost = |delta_weight| * cost_bps / 10_000

The sleeve produces a daily returns series by applying the *lagged*
weights to next-day close returns (no look-ahead bias).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

import numpy as np
import pandas as pd

from ..indicators import ichimoku as ichimoku_indicator
from .regime_filter import RegimeFilter


def _rsi(close: pd.Series, n: int = 14) -> pd.Series:
    """Wilder-ish RSI(14) on close (simple-MA variant — enough for an entry gate)."""
    d = close.diff()
    up = d.clip(lower=0).rolling(n).mean()
    dn = (-d.clip(upper=0)).rolling(n).mean()
    return 100 - 100 / (1 + up / dn.replace(0, np.nan))


def _default_compute_position(
    data: dict[str, pd.DataFrame],
    tenkan: int,
    kijun: int,
    senkou_b: int,
    displacement: int,
    entry_max_ext_pct: float | None = None,
    entry_rsi_max: float | None = None,
    stop_loss_pct: float | None = None,
    trail_pct: float | None = None,
) -> pd.DataFrame:
    """Compute per-ticker long/flat signals using Ichimoku.

    Long when: Close > cloud_high AND tenkan > kijun
    Flat when: Close < cloud_low OR tenkan < kijun

    Optional ENTRY-EXTENSION GATE (default OFF): when ``entry_max_ext_pct`` /
    ``entry_rsi_max`` are set, a NEW long is BLOCKED if the name is already
    over-extended (Close > entry_max_ext_pct% above its 200-SMA, or RSI(14) >
    entry_rsi_max). The exit rule is untouched. With both None the function is
    byte-identical to the legacy version (backtest parity).

    Returns a DataFrame of 0/1 values, shape (n_bars, n_tickers).
    """
    gated = entry_max_ext_pct is not None or entry_rsi_max is not None
    positions = {}
    for ticker, df in data.items():
        if "Close" not in df.columns or len(df) < max(tenkan, kijun, senkou_b) + displacement + 1:
            # Not enough bars — stay flat
            positions[ticker] = pd.Series(0, index=df.index, dtype=float)
            continue

        ich = ichimoku_indicator(
            df["High"], df["Low"], df["Close"],
            tenkan=tenkan, kijun=kijun,
            senkou_b=senkou_b, displacement=displacement,
        )

        # Extension series only computed when the gate is active (else None →
        # zero overhead + exact legacy behaviour).
        sma200 = df["Close"].rolling(200).mean() if (gated and entry_max_ext_pct is not None) else None
        rsi14 = _rsi(df["Close"]) if (gated and entry_rsi_max is not None) else None

        pos = pd.Series(0.0, index=df.index)
        # Stateful: start flat, go long when entry conditions met, exit on reversal
        current = 0.0
        entry_px = 0.0  # cost basis of the current long, for the hard stop
        peak_px = 0.0   # highest close since entry, for the trailing stop
        for i in range(len(df)):
            c = df["Close"].iloc[i]
            ch = ich["cloud_high"].iloc[i]
            cl = ich["cloud_low"].iloc[i]
            t = ich["tenkan"].iloc[i]
            k = ich["kijun"].iloc[i]

            if any(pd.isna(x) for x in [ch, cl, t, k]):
                pos.iloc[i] = current
                continue

            if current == 0.0:
                # Entry: price above cloud AND tenkan above kijun
                if c > ch and t > k:
                    blocked = False
                    if entry_max_ext_pct is not None and sma200 is not None:
                        s = sma200.iloc[i]
                        if pd.notna(s) and s > 0 and (c / s - 1) * 100 > entry_max_ext_pct:
                            blocked = True  # too far above the mean — don't chase
                    if not blocked and entry_rsi_max is not None and rsi14 is not None:
                        r = rsi14.iloc[i]
                        if pd.notna(r) and r > entry_rsi_max:
                            blocked = True  # overbought — don't chase
                    if not blocked:
                        current = 1.0
                        entry_px = float(c)  # arm the stops at the entry close
                        peak_px = float(c)
            else:
                peak_px = max(peak_px, float(c))  # high-water mark for the trail
                # HARD STOP (OPT-IN): force-flat if down > stop_loss_pct from entry.
                stopped = (
                    stop_loss_pct is not None and entry_px > 0
                    and (c / entry_px - 1) * 100 <= -stop_loss_pct
                )
                # TRAILING STOP (OPT-IN): force-flat if down > trail_pct from the
                # high-water mark — lets winners run, locks in gains on reversal.
                trailed = (
                    trail_pct is not None and peak_px > 0
                    and (peak_px - c) / peak_px * 100 >= trail_pct
                )
                # Exit: a stop hit, OR price below cloud OR tenkan below kijun.
                if stopped or trailed or c < cl or t < k:
                    current = 0.0
                    entry_px = 0.0
                    peak_px = 0.0

            pos.iloc[i] = current

        positions[ticker] = pos

    if not positions:
        return pd.DataFrame()
    return pd.DataFrame(positions)


@dataclass
class SleeveResult:
    """Output from a Sleeve backtest run."""
    name: str
    returns: pd.Series
    weights: pd.DataFrame
    n_positions: pd.Series  # count of long positions per bar


class Sleeve:
    """Single Ichimoku sleeve backtest.

    Parameters
    ----------
    name : str
        Human-readable label (e.g. "equity_large", "gold").
    data : dict[str, pd.DataFrame]
        Ticker → OHLCV DataFrame with columns High, Low, Close.
        All DataFrames must share the same DatetimeIndex.
    config : QuantEngineConfig or None
        Config dataclass. If None, uses module-level defaults.
    regime : RegimeFilter or None
        Optional bull/bear gate. Signals are zeroed on bear days.
    _compute_position_fn : callable or None
        Injection point for testing. Must accept (data, tenkan, kijun,
        senkou_b, displacement) and return a (n_bars, n_tickers) DataFrame.
    """

    def __init__(
        self,
        name: str,
        data: dict[str, pd.DataFrame],
        config=None,
        regime: RegimeFilter | None = None,
        _compute_position_fn: Callable | None = None,
    ) -> None:
        self.name = name
        self.data = data
        self.regime = regime
        self._compute_position_fn = _compute_position_fn or _default_compute_position

        if config is None:
            from .config import QuantEngineConfig
            config = QuantEngineConfig()
        self.config = config

    def run(self) -> SleeveResult:
        """Run the sleeve backtest and return a SleeveResult."""
        cfg = self.config
        tickers = list(self.data.keys())
        n = cfg.sleeve_large  # used as denominator; actual count = len(tickers)
        sleeve_size = max(len(tickers), 1)

        # Compute raw positions (0/1 per ticker). Pass the entry-extension gate
        # kwargs ONLY when configured — so an injected custom compute_fn (tests)
        # and the default both keep their legacy 5-arg behaviour when the gate
        # is off (backtest parity).
        gate_kw = {}
        if getattr(cfg, "entry_max_ext_pct", None) is not None:
            gate_kw["entry_max_ext_pct"] = cfg.entry_max_ext_pct
        if getattr(cfg, "entry_rsi_max", None) is not None:
            gate_kw["entry_rsi_max"] = cfg.entry_rsi_max
        if getattr(cfg, "stop_loss_pct", None) is not None:
            gate_kw["stop_loss_pct"] = cfg.stop_loss_pct
        if getattr(cfg, "trail_pct", None) is not None:
            gate_kw["trail_pct"] = cfg.trail_pct
        raw_pos = self._compute_position_fn(
            self.data, cfg.tenkan, cfg.kijun, cfg.senkou_b, cfg.displacement,
            **gate_kw,
        )

        if raw_pos.empty:
            idx = next(iter(self.data.values())).index
            empty = pd.Series(0.0, index=idx, name=self.name)
            return SleeveResult(
                name=self.name,
                returns=empty,
                weights=pd.DataFrame(index=idx),
                n_positions=pd.Series(0, index=idx),
            )

        # Convert to weights: 1/sleeve_size each, total exposure capped at 1x
        weights = raw_pos / sleeve_size
        # Cap total row exposure
        row_sum = weights.sum(axis=1)
        mask = row_sum > 1.0
        if mask.any():
            weights.loc[mask] = weights.loc[mask].div(row_sum[mask], axis=0)

        # Apply regime gate
        if self.regime is not None:
            weights = self.regime.gate_signals(weights)

        # Compute close-to-close returns for each ticker
        ticker_returns = {}
        for ticker, df in self.data.items():
            if ticker in weights.columns:
                ticker_returns[ticker] = df["Close"].pct_change().fillna(0.0)

        if not ticker_returns:
            idx = raw_pos.index
            empty = pd.Series(0.0, index=idx, name=self.name)
            return SleeveResult(
                name=self.name,
                returns=empty,
                weights=weights,
                n_positions=raw_pos.sum(axis=1).astype(int),
            )

        returns_df = pd.DataFrame(ticker_returns).reindex(columns=weights.columns).fillna(0.0)

        # Lagged weights (position decided at close of day t, returns realised at day t+1)
        lagged_weights = weights.shift(1).fillna(0.0)

        # Transaction costs: |delta_weight| * cost_bps / 10_000
        delta_weights = weights.diff().abs().fillna(0.0)
        cost_per_bar = delta_weights.sum(axis=1) * (cfg.cost_bps / 10_000.0)

        # Sleeve returns = sum(weight_i * return_i) - cost
        gross = (lagged_weights * returns_df).sum(axis=1)
        sleeve_returns = gross - cost_per_bar
        sleeve_returns.name = self.name

        n_positions = raw_pos.sum(axis=1).astype(int)

        return SleeveResult(
            name=self.name,
            returns=sleeve_returns,
            weights=weights,
            n_positions=n_positions,
        )

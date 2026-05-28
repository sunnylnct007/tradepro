"""Live target portfolio derivation — the slow-loop output.

The equity_pipeline runs the trader's algo over a historical window to
produce a backtest. This module runs the SAME algo logic but extracts
the *single most-recent-bar position vector* — "for tomorrow's open,
the algo recommends holding these names at these weights."

Same code path (Sleeve + Ensemble + RegimeFilter + vol target) so the
live output is consistent with the validated backtest. The only
difference: we keep only the last row of the weights DataFrame and
attach per-symbol context (signal value, regime gate state,
contributing reasons) for the audit log.

Output shape — a LiveTargetPortfolio dataclass:

    run_id              uuid
    as_of_utc           timestamp of the run
    regime_state        'bull' | 'bear' | 'neutral' | 'unknown'
    sleeves             list of SleeveSnapshot:
                          name, n_tickers, n_long, ensemble_weight
                          (= 1/n_sleeves)
    decisions           list of TargetPosition:
                          symbol, sleeve, target_weight,
                          signal, regime_pass, vol, detail{...}

This is what the slow-loop CLI persists into strategy_runs +
strategy_decisions tables (one strategy_runs row, N decision rows).
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

import numpy as np
import pandas as pd

from ..indicators import ichimoku as ichimoku_indicator
from .config import QuantEngineConfig
from .ensemble import Ensemble
from .regime_filter import RegimeFilter
from .sleeve import Sleeve
from .vol_targeting import vol_target_scalar


@dataclass
class TargetPosition:
    """One row in the strategy_decisions table."""
    symbol: str
    sleeve: str
    target_weight: float        # 0..1 portfolio weight
    signal: float               # 1.0 long, 0.0 flat (pre-regime gate)
    regime_pass: bool           # did the SPY-200 gate let this through
    vol: float | None           # per-name realized vol (annualised, %)
    detail: dict[str, Any] = field(default_factory=dict)


@dataclass
class SleeveSnapshot:
    """Per-sleeve summary for the strategy_runs header."""
    name: str
    n_tickers: int
    n_long: int
    ensemble_weight: float      # 1/n_sleeves; how much this sleeve gets
    note: str | None = None


@dataclass
class LiveTargetPortfolio:
    """Slow-loop run result — persisted as strategy_runs row +
    strategy_decisions rows."""
    run_id: uuid.UUID
    strategy: str
    as_of_utc: datetime
    bar_ts: datetime            # timestamp of the most-recent bar used
    regime_state: str           # 'bull' / 'bear' / 'unknown'
    vol_scalar: float           # portfolio-level vol scaling at this bar
    sleeves: list[SleeveSnapshot]
    decisions: list[TargetPosition]
    inputs_hash: str            # opaque hash of config so re-runs that
                                # produced identical inputs are diffable

    def to_summary_json(self) -> dict[str, Any]:
        """JSON payload for the strategy_runs.summary column."""
        return {
            "bar_ts": self.bar_ts.isoformat(),
            "regime_state": self.regime_state,
            "vol_scalar": float(self.vol_scalar),
            "sleeves": [
                {"name": s.name, "n_tickers": s.n_tickers,
                 "n_long": s.n_long, "ensemble_weight": s.ensemble_weight,
                 "note": s.note}
                for s in self.sleeves
            ],
            "inputs_hash": self.inputs_hash,
        }


# ---------------------------------------------------------------------- #
# Live-portfolio derivation                                              #
# ---------------------------------------------------------------------- #


def compute_live_portfolio(
    *,
    strategy: str,
    spy_close: pd.Series,
    sleeve_data: dict[str, dict[str, pd.DataFrame]],
    cfg: QuantEngineConfig | None = None,
    sleeve_regime_gates: dict[str, bool] | None = None,
) -> LiveTargetPortfolio:
    """Produce today's target portfolio.

    Parameters
    ----------
    strategy : str
        Strategy id (e.g. 'ichimoku_equity').
    spy_close : pd.Series
        SPY daily adjusted close — used for the regime filter.
    sleeve_data : dict[str, dict[str, pd.DataFrame]]
        sleeve_name → {ticker → OHLCV DataFrame with Open/High/Low/Close}.
        Caller is responsible for fetching + ensuring history meets
        Ichimoku warmup (>= senkou_b + displacement + 1 bars).
    cfg : QuantEngineConfig | None
        Strategy config. Falls back to defaults.
    sleeve_regime_gates : dict[str, bool] | None
        sleeve_name → whether to apply the SPY 200-SMA gate. Defaults to
        gating only the high-beta-style sleeves (matches the trader's
        spec — equity_large + gold don't get gated, equity_hibeta does).

    Returns
    -------
    LiveTargetPortfolio
    """
    cfg = cfg or QuantEngineConfig()
    sleeve_regime_gates = sleeve_regime_gates or {
        "equity_hibeta": True,
        "equity_large": False,
        "gold": False,
    }
    run_id = uuid.uuid4()
    as_of = datetime.now(timezone.utc)

    # Regime state at the latest bar — same gate the trader uses.
    if cfg.use_regime_filter and len(spy_close) >= cfg.regime_sma:
        sma = spy_close.rolling(cfg.regime_sma).mean()
        is_bull_now = bool(spy_close.iloc[-1] > sma.iloc[-1])
        regime_state = "bull" if is_bull_now else "bear"
    else:
        is_bull_now = True
        regime_state = "unknown"

    regime = RegimeFilter(spy_close, cfg.regime_sma) if cfg.use_regime_filter else None

    # Build sleeves + run each so we have the weights series.
    sleeves: list[Sleeve] = []
    snapshots: list[SleeveSnapshot] = []
    sleeve_results = {}
    bar_ts: datetime | None = None

    for name, data in sleeve_data.items():
        if not data:
            snapshots.append(SleeveSnapshot(
                name=name, n_tickers=0, n_long=0,
                ensemble_weight=0.0,
                note="no usable history",
            ))
            continue
        sleeve = Sleeve(
            name=name, data=data, config=cfg,
            regime=regime if sleeve_regime_gates.get(name, False) else None,
        )
        result = sleeve.run()
        sleeves.append(sleeve)
        sleeve_results[name] = (sleeve, result)
        # Capture the most-recent bar timestamp (consistent across sleeves
        # when they share trading-day index — which they do for equities).
        if not result.weights.empty:
            bar_ts = bar_ts or result.weights.index[-1].to_pydatetime()

    if not sleeves:
        return LiveTargetPortfolio(
            run_id=run_id, strategy=strategy, as_of_utc=as_of,
            bar_ts=as_of, regime_state=regime_state, vol_scalar=1.0,
            sleeves=snapshots, decisions=[],
            inputs_hash=_inputs_hash(strategy, cfg, sleeve_data),
        )

    # Portfolio-level vol target — replicates Ensemble.run() at the last bar.
    ensemble = Ensemble(sleeves, cfg, initial_capital=cfg.initial_capital)
    er = ensemble.run()
    vol_scalar_last = float(er.vol_scalar.iloc[-1]) if not er.vol_scalar.empty else 1.0

    # Per-sleeve final weight = sleeve.weights[last_bar] * (1/n_sleeves)
    # * vol_scalar_last. Per-symbol target_weight follows the same recipe.
    n_sleeves = len(sleeves)
    ensemble_w = 1.0 / n_sleeves if n_sleeves else 0.0

    decisions: list[TargetPosition] = []
    for name, (sleeve, result) in sleeve_results.items():
        w_row = result.weights.iloc[-1] if not result.weights.empty else pd.Series(dtype=float)
        n_long = int((w_row > 0).sum())
        snapshots.append(SleeveSnapshot(
            name=name, n_tickers=len(sleeve.data),
            n_long=n_long, ensemble_weight=ensemble_w,
            note=None if n_long else "no firing signals at latest bar",
        ))

        # Per-symbol target — apply ensemble weight + portfolio vol scalar.
        for symbol, df in sleeve.data.items():
            sleeve_w = float(w_row.get(symbol, 0.0))
            target_w = sleeve_w * ensemble_w * vol_scalar_last

            # Recompute per-symbol indicator values + signal for the
            # audit detail. Cheap — Ichimoku on one symbol is trivial.
            signal_value, gate_pass, vol_pct, detail = _per_symbol_context(
                df, cfg, regime_active=sleeve_regime_gates.get(name, False),
                is_bull_now=is_bull_now,
            )

            decisions.append(TargetPosition(
                symbol=symbol, sleeve=name,
                target_weight=target_w,
                signal=signal_value,
                regime_pass=gate_pass,
                vol=vol_pct,
                detail=detail,
            ))

    return LiveTargetPortfolio(
        run_id=run_id, strategy=strategy,
        as_of_utc=as_of,
        bar_ts=bar_ts or as_of,
        regime_state=regime_state,
        vol_scalar=vol_scalar_last,
        sleeves=snapshots,
        decisions=decisions,
        inputs_hash=_inputs_hash(strategy, cfg, sleeve_data),
    )


# ---------------------------------------------------------------------- #
# Helpers                                                                #
# ---------------------------------------------------------------------- #


def _per_symbol_context(
    df: pd.DataFrame,
    cfg: QuantEngineConfig,
    *,
    regime_active: bool,
    is_bull_now: bool,
) -> tuple[float, bool, float | None, dict[str, Any]]:
    """Compute the latest-bar signal + supporting indicator values
    for one symbol — enough context for the audit log / risk module
    / MCP "why did the algo recommend X?" tool.

    Returns (signal, regime_pass, vol_pct, detail).
    """
    if "Close" not in df.columns or len(df) < max(cfg.tenkan, cfg.kijun, cfg.senkou_b) + cfg.displacement + 1:
        return 0.0, False, None, {"reason": "insufficient history for Ichimoku"}

    ich = ichimoku_indicator(
        df["High"], df["Low"], df["Close"],
        tenkan=cfg.tenkan, kijun=cfg.kijun,
        senkou_b=cfg.senkou_b, displacement=cfg.displacement,
    )

    close = float(df["Close"].iloc[-1])
    cloud_top = float(ich["cloud_high"].iloc[-1])
    cloud_bot = float(ich["cloud_low"].iloc[-1])
    tenkan = float(ich["tenkan"].iloc[-1])
    kijun = float(ich["kijun"].iloc[-1])
    # Long when above the cloud AND tenkan > kijun; the per-bar state
    # machine in Sleeve doesn't expose the live state directly, so we
    # recompute the entry condition for the latest bar here.
    is_long = (close > cloud_top) and (tenkan > kijun)
    signal = 1.0 if is_long else 0.0
    gate_pass = (not regime_active) or is_bull_now

    # Annualised vol — same vol_lookback the strategy uses.
    if len(df) > cfg.vol_lookback:
        rets = df["Close"].pct_change().dropna().tail(cfg.vol_lookback)
        vol_pct = float(rets.std(ddof=1) * np.sqrt(252) * 100)
    else:
        vol_pct = None

    cloud_above = close > cloud_top
    cloud_below = close < cloud_bot

    detail: dict[str, Any] = {
        "close": close,
        "cloud_top": cloud_top,
        "cloud_bottom": cloud_bot,
        "tenkan": tenkan,
        "kijun": kijun,
        "cloud_position": (
            "above" if cloud_above
            else "below" if cloud_below
            else "inside"
        ),
        "tk_cross": "bullish" if tenkan > kijun else "bearish",
        "regime_active": regime_active,
        "regime_bull": is_bull_now,
    }
    if is_long:
        # Distance above the cloud as a fraction — proxy for conviction.
        detail["above_cloud_pct"] = (close - cloud_top) / cloud_top * 100
    elif cloud_below:
        detail["below_cloud_pct"] = (cloud_top - close) / cloud_top * 100
    return signal, gate_pass, vol_pct, detail


def _inputs_hash(
    strategy: str,
    cfg: QuantEngineConfig,
    sleeve_data: dict[str, dict[str, pd.DataFrame]],
) -> str:
    """Opaque short hash of run inputs. Used to detect 'same inputs,
    same outputs' — re-running the slow loop with no new data shouldn't
    produce a different recommendation."""
    import hashlib
    parts = [
        strategy,
        f"{cfg.tenkan}/{cfg.kijun}/{cfg.senkou_b}/{cfg.displacement}",
        f"{cfg.target_vol}/{cfg.max_leverage}/{cfg.vol_lookback}",
        f"{cfg.regime_sma}/{cfg.use_regime_filter}",
    ]
    for sleeve_name in sorted(sleeve_data.keys()):
        tickers = sorted(sleeve_data[sleeve_name].keys())
        parts.append(f"{sleeve_name}:{len(tickers)}:{','.join(tickers[:5])}")
    return hashlib.md5("|".join(parts).encode()).hexdigest()[:12]

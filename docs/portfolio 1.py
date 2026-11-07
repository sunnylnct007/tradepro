# ============================================================
# portfolio.py
# ============================================================
"""Portfolio mechanics: sleeves, ensemble, walk-forward, Monte Carlo."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from config import Config
from strategy import IchimokuStrategy, RegimeFilter


@dataclass
class SleeveResult:
    """Output of a single sleeve backtest."""
    returns: pd.Series
    weights: pd.DataFrame


class Sleeve:
    """A group of tickers traded with the same Ichimoku strategy + sleeve sizing."""

    def __init__(
        self,
        name: str,
        data: dict[str, pd.DataFrame],
        sleeve_size: int,
        config: Config,
        regime: RegimeFilter | None = None,
    ) -> None:
        self.name = name
        self.data = data
        self.sleeve_size = sleeve_size
        self.cfg = config
        self.regime = regime
        self._result: SleeveResult | None = None

    def backtest(self) -> SleeveResult:
        """Run Ichimoku across the sleeve universe → daily returns + weights."""
        if self._result is not None:
            return self._result

        strategy = IchimokuStrategy(self.cfg)
        enriched = {t: strategy.apply(df) for t, df in self.data.items()}
        all_dates = sorted(set().union(*(d.index for d in enriched.values())))

        signals = pd.DataFrame(
            {t: d["position"].reindex(all_dates).fillna(0) for t, d in enriched.items()},
            index=all_dates,
        )
        if self.regime is not None:
            signals = signals.mul(self.regime.align(all_dates).astype(float), axis=0)

        raw_w = signals * (1.0 / self.sleeve_size)
        scale = np.minimum(1.0, 1.0 / raw_w.sum(axis=1).replace(0, np.inf))
        weights = raw_w.mul(scale, axis=0).fillna(0)

        returns = pd.DataFrame(
            {t: d["Close"].reindex(all_dates).pct_change().fillna(0) for t, d in self.data.items()},
            index=all_dates,
        )
        gross = (weights.shift(1).fillna(0) * returns).sum(axis=1)
        cost = weights.diff().abs().fillna(0).sum(axis=1) * (self.cfg.cost_bps / 10_000.0)

        self._result = SleeveResult(returns=gross - cost, weights=weights)
        return self._result


@dataclass
class EnsembleResult:
    """Output of an ensemble backtest."""
    equity: pd.Series
    daily_returns: pd.Series
    sleeve_returns: pd.DataFrame
    vol_scalar: pd.Series
    summary: dict[str, float]


class Ensemble:
    """Combine multiple sleeves equal-weight, then apply vol targeting."""

    def __init__(self, sleeves: list[Sleeve], config: Config) -> None:
        self.sleeves = sleeves
        self.cfg = config

    def _align_sleeves(self) -> pd.DataFrame:
        """Run all sleeve backtests and align to common dates."""
        sleeve_rets = {s.name: s.backtest().returns for s in self.sleeves}
        dates = sorted(set().union(*(r.index for r in sleeve_rets.values())))
        return pd.DataFrame(
            {k: r.reindex(dates).fillna(0) for k, r in sleeve_rets.items()},
            index=dates,
        )

    def _apply_vol_target(self, returns: pd.Series) -> tuple[pd.Series, pd.Series]:
        """Scale daily returns to a constant annualised vol target."""
        realised_vol = returns.rolling(self.cfg.vol_lookback).std() * np.sqrt(252)
        scalar = (self.cfg.target_vol / realised_vol.shift(1)).clip(
            upper=self.cfg.max_leverage,
        ).fillna(1.0)
        return returns * scalar, scalar

    def run(self, use_vol_target: bool = True) -> EnsembleResult:
        """Equal-weight combine + optional vol targeting."""
        sleeve_df = self._align_sleeves()
        ew_returns = sleeve_df.mean(axis=1)
        if use_vol_target:
            daily_returns, scalar = self._apply_vol_target(ew_returns)
        else:
            daily_returns = ew_returns
            scalar = pd.Series(1.0, index=ew_returns.index)
        equity = self.cfg.initial_capital * (1 + daily_returns).cumprod()
        return EnsembleResult(
            equity=equity, daily_returns=daily_returns,
            sleeve_returns=sleeve_df, vol_scalar=scalar,
            summary=Stats.summarise(equity, daily_returns),
        )


class Stats:
    """Static performance metric helpers."""

    @staticmethod
    def summarise(equity: pd.Series, daily_returns: pd.Series) -> dict[str, float]:
        years = len(equity) / 252
        cagr = ((equity.iloc[-1] / equity.iloc[0]) ** (1 / years) - 1) * 100
        sharpe = daily_returns.mean() / daily_returns.std() * np.sqrt(252)
        max_dd = ((equity - equity.cummax()) / equity.cummax()).min() * 100
        return {
            "cagr_pct": round(cagr, 2),
            "sharpe": round(sharpe, 3),
            "max_drawdown_pct": round(max_dd, 2),
            "calmar": round(-cagr / max_dd, 2) if max_dd < 0 else 0.0,
        }

    @staticmethod
    def benchmark(spy_close: pd.Series, dates, initial: float) -> tuple[pd.Series, dict[str, float]]:
        spy_returns = spy_close.reindex(dates).pct_change().fillna(0)
        spy_equity = initial * (1 + spy_returns).cumprod()
        return spy_equity, Stats.summarise(spy_equity, spy_returns)


class WalkForwardValidator:
    """Out-of-sample walk-forward validation with re-fit vol scalar per window."""

    def __init__(self, ensemble_returns: pd.Series, config: Config) -> None:
        self.returns = ensemble_returns
        self.cfg = config

    def run(self) -> tuple[pd.Series, list[dict]]:
        """For each window: fit vol scalar on train, apply to next test year."""
        all_idx = pd.Index(self.returns.index)
        oos_rets, per_window = [], []

        for tr_s, tr_e, te_y in self.cfg.walk_forward_windows:
            train_dates = all_idx[(all_idx >= tr_s) & (all_idx <= tr_e)]
            test_dates = all_idx[all_idx.year == int(te_y)]
            if len(test_dates) == 0 or len(train_dates) == 0:
                continue
            train_vol = self.returns.loc[train_dates].std() * np.sqrt(252)
            scalar = min(self.cfg.max_leverage, self.cfg.target_vol / train_vol) \
                if train_vol > 0 else 1.0
            test_ret = self.returns.loc[test_dates] * scalar
            oos_rets.append(test_ret)
            per_window.append({
                "test_year": te_y,
                "vol_scalar": round(scalar, 3),
                "test_sharpe": round(test_ret.mean() / test_ret.std() * np.sqrt(252), 3),
                "test_cagr": round((((1 + test_ret).prod()) ** (252 / len(test_ret)) - 1) * 100, 2),
            })

        oos = pd.concat(oos_rets).sort_index()
        return oos[~oos.index.duplicated()], per_window


@dataclass
class MonteCarloResult:
    """Output of Monte Carlo simulation."""
    paths: np.ndarray            # shape (n_sims, n_days+1)
    final_values: np.ndarray     # shape (n_sims,)
    max_drawdowns: np.ndarray    # shape (n_sims,)
    initial: float
    years: int
    summary: dict


class MonteCarloSimulator:
    """Block-bootstrap projection of forward returns."""

    def __init__(self, daily_returns: pd.Series, block_size: int = 21, seed: int = 42) -> None:
        self.returns = daily_returns.values
        self.block_size = block_size
        self.rng = np.random.default_rng(seed)

    def run(self, initial: float = 10_000, years: int = 10, n_sims: int = 10_000) -> MonteCarloResult:
        n_days = years * 252
        n_blocks = (n_days + self.block_size - 1) // self.block_size
        max_start = len(self.returns) - self.block_size

        paths = np.empty((n_sims, n_days + 1))
        paths[:, 0] = initial
        for i in range(n_sims):
            starts = self.rng.integers(0, max_start + 1, size=n_blocks)
            rets = np.concatenate([self.returns[s:s + self.block_size] for s in starts])[:n_days]
            paths[i, 1:] = initial * np.cumprod(1 + rets)

        finals = paths[:, -1]
        max_dds = np.array([(p / np.maximum.accumulate(p) - 1).min() for p in paths])

        pcts = [5, 10, 25, 50, 75, 90, 95]
        fv_pct = np.percentile(finals, pcts)
        cagrs = (finals / initial) ** (1 / years) - 1

        summary = {
            "percentiles": {f"p{p}": {
                "final": float(round(fv_pct[i], 0)),
                "multiple": float(round(fv_pct[i] / initial, 2)),
                "cagr_pct": float(round(cagrs[np.argsort(finals)[int(p * n_sims / 100) - 1]] * 100, 2)),
            } for i, p in enumerate(pcts)},
            "mean_final": float(round(finals.mean(), 0)),
            "p_lose_money": float(round((finals < initial).mean() * 100, 1)),
            "p_double": float(round((finals >= 2 * initial).mean() * 100, 1)),
            "p_5x": float(round((finals >= 5 * initial).mean() * 100, 1)),
            "p_10x": float(round((finals >= 10 * initial).mean() * 100, 1)),
            "max_dd_pct": {f"p{p}": float(round(np.percentile(max_dds, p) * 100, 1))
                          for p in [5, 25, 50, 75, 95]},
        }
        return MonteCarloResult(
            paths=paths, final_values=finals, max_drawdowns=max_dds,
            initial=initial, years=years, summary=summary,
        )

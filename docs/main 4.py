# ============================================================
# main.py
# ============================================================
"""Orchestration — wire everything together."""

from __future__ import annotations

import json

from config import Config
from data import DataFetcher
from portfolio import Ensemble, MonteCarloSimulator, Sleeve, Stats, WalkForwardValidator
from strategy import RegimeFilter, UniverseBuilder
from visualization import Plotter


class StrategyRunner:
    """End-to-end pipeline orchestrator."""

    def __init__(self, config: Config) -> None:
        self.cfg = config
        self.fetcher = DataFetcher(config.start_date, config.end_date)

    def run(self) -> dict:
        # 1. Benchmark + regime
        print("Fetching SPY...")
        spy_df = self.fetcher.fetch(self.cfg.benchmark)
        regime = RegimeFilter(spy_df, self.cfg.regime_sma) if self.cfg.use_regime_filter else None

        # 2. Build sleeve universes
        print("\nBuilding sleeve universes...")
        large_data = self.fetcher.fetch_many(list(self.cfg.large_50))
        hibeta_data, _ = UniverseBuilder(self.fetcher, self.cfg).build_high_beta(spy_df)
        gold_data = self.fetcher.fetch_many(list(self.cfg.gold_tickers))

        # 3. Construct sleeves
        print("\nConstructing sleeves...")
        sleeves = [
            Sleeve("equity_large",  large_data,  self.cfg.sleeve_large,  self.cfg),
            Sleeve("equity_hibeta", hibeta_data, self.cfg.sleeve_hibeta, self.cfg, regime=regime),
            Sleeve("gold",          gold_data,   self.cfg.sleeve_gold,   self.cfg),
        ]

        # 4. Ensemble with vol targeting
        print("\nRunning ensemble (equal-weight + vol target)...")
        ensemble = Ensemble(sleeves, self.cfg)
        result = ensemble.run(use_vol_target=True)
        spy_equity, spy_summary = Stats.benchmark(
            spy_df["Close"], result.equity.index, self.cfg.initial_capital,
        )

        # 5. Walk-forward OOS validation
        print("\nRunning walk-forward OOS validation...")
        ew_returns = result.sleeve_returns.mean(axis=1)
        oos_ret, per_window = WalkForwardValidator(ew_returns, self.cfg).run()
        oos_equity = (1 + oos_ret).cumprod() * self.cfg.initial_capital
        oos_summary = Stats.summarise(oos_equity, oos_ret)

        # 6. Monte Carlo projection
        print("\nRunning Monte Carlo projection...")
        mc = MonteCarloSimulator(oos_ret).run(initial=10_000, years=10, n_sims=10_000)

        # 7. Output
        print("\n" + "=" * 60)
        print("RESULTS")
        print("=" * 60)
        print(json.dumps({
            "In-Sample":      result.summary,
            "Walk-Forward OOS": oos_summary,
            "SPY B&H":        spy_summary,
            "Per-window OOS": per_window,
            "Monte Carlo ($10k → 10y)": mc.summary,
        }, indent=2))

        # 8. Plots
        Plotter.backtest(result, spy_equity, spy_summary, oos_equity, oos_summary).show()
        Plotter.monte_carlo(mc).show()

        return {"ensemble": result, "oos": (oos_equity, oos_summary), "monte_carlo": mc}


def main() -> None:
    StrategyRunner(Config()).run()


if __name__ == "__main__":
    main()

using TradePro.Api.Models;

namespace TradePro.Api.Simulation;

/// Static metadata about each strategy — keyed by the same name as the DI
/// registry. Kept here rather than inside the strategy classes so a UI can
/// list strategies (with horizon, description, default params) without
/// instantiating the signal generators.
public static class StrategyCatalog
{
    public static readonly IReadOnlyDictionary<string, StrategyMetadata> All =
        new Dictionary<string, StrategyMetadata>(StringComparer.OrdinalIgnoreCase)
        {
            ["buy_and_hold"] = new(
                Name: "buy_and_hold",
                DisplayName: "Buy & Hold",
                OneLiner: "Buy on day 1, keep the position until the end of the window. The benchmark every other strategy has to beat net of fees.",
                BestIn: "Long bull markets where the underlying keeps compounding up.",
                WorstIn: "Prolonged bear markets — you eat every drawdown in full.",
                Horizon: TimeHorizon.Long,
                HorizonText: "long-term (months to years)",
                DefaultParams: null,
                ParamKeys: null),

            ["sma_crossover"] = new(
                Name: "sma_crossover",
                DisplayName: "SMA crossover",
                OneLiner: "Buy when a fast moving average crosses above a slow one (golden cross); sell when it crosses below (death cross).",
                BestIn: "Stocks in clear sustained trends.",
                WorstIn: "Range-bound or choppy markets — whipsaws eat fees.",
                Horizon: TimeHorizon.Mid,
                HorizonText: "mid-term (weeks to a few months)",
                DefaultParams: new() { ["fast"] = 20, ["slow"] = 50 },
                ParamKeys: new[] { "fast", "slow" }),

            ["rsi_mean_reversion"] = new(
                Name: "rsi_mean_reversion",
                DisplayName: "RSI mean-reversion",
                OneLiner: "Buy when the Relative Strength Index recovers from oversold (<30); sell when it cools off from overbought (>70).",
                BestIn: "Range-bound stocks that oscillate around a fair value.",
                WorstIn: "Strong trends — RSI can stay overbought (or oversold) for weeks while the trend continues.",
                Horizon: TimeHorizon.Short,
                HorizonText: "short-term (days to a couple of weeks per trade)",
                DefaultParams: new() { ["period"] = 14, ["low"] = 30, ["high"] = 70 },
                ParamKeys: new[] { "period", "low", "high" }),

            ["macd_signal_cross"] = new(
                Name: "macd_signal_cross",
                DisplayName: "MACD signal-cross",
                OneLiner: "Buy when the MACD momentum line crosses above its smoothed signal line; sell on the reverse.",
                BestIn: "Early trend changes where momentum is building.",
                WorstIn: "Choppy markets where momentum flips day-to-day.",
                Horizon: TimeHorizon.Mid,
                HorizonText: "short-to-mid-term (days to weeks)",
                DefaultParams: new() { ["fast"] = 12, ["slow"] = 26, ["signal"] = 9 },
                ParamKeys: new[] { "fast", "slow", "signal" }),

            ["donchian_breakout"] = new(
                Name: "donchian_breakout",
                DisplayName: "Donchian breakout",
                OneLiner: "Buy on a close above the prior N-day high; sell on a close below the prior N-day low. Pure breakout / momentum.",
                BestIn: "Strong sustained trends with clear new highs.",
                WorstIn: "Range-bound markets — strategy sits flat or takes small losses on failed breakouts.",
                Horizon: TimeHorizon.Mid,
                HorizonText: "mid-to-long-term (weeks to months)",
                DefaultParams: new() { ["lookback"] = 20 },
                ParamKeys: new[] { "lookback" }),
        };

    public static StrategyMetadata? Get(string name) =>
        All.TryGetValue(name, out var m) ? m : null;
}

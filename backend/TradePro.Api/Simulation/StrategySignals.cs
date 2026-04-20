using TradePro.Api.Models;

namespace TradePro.Api.Simulation;

public enum Signal { Hold, Buy, Sell }

public interface ISignalStrategy
{
    string Name { get; }
    Signal[] Generate(IReadOnlyList<Candle> candles, IReadOnlyDictionary<string, double> @params);
}

public sealed class BuyAndHoldStrategy : ISignalStrategy
{
    public string Name => "buy_and_hold";

    public Signal[] Generate(IReadOnlyList<Candle> candles, IReadOnlyDictionary<string, double> _)
    {
        var signals = new Signal[candles.Count];
        if (candles.Count == 0) return signals;
        signals[0] = Signal.Buy;
        return signals;
    }
}

public sealed class SmaCrossoverStrategy : ISignalStrategy
{
    public string Name => "sma_crossover";

    public Signal[] Generate(IReadOnlyList<Candle> candles, IReadOnlyDictionary<string, double> @params)
    {
        var fast = (int)(@params.TryGetValue("fast", out var f) ? f : 20);
        var slow = (int)(@params.TryGetValue("slow", out var s) ? s : 50);
        var closes = candles.Select(c => c.Close).ToArray();
        var fastSma = Indicators.Sma(closes, fast);
        var slowSma = Indicators.Sma(closes, slow);

        var signals = new Signal[candles.Count];
        bool? prevFastAbove = null;
        for (var i = 0; i < candles.Count; i++)
        {
            if (fastSma[i] is null || slowSma[i] is null) continue;
            var fastAbove = fastSma[i] > slowSma[i];
            if (prevFastAbove.HasValue && fastAbove != prevFastAbove.Value)
            {
                signals[i] = fastAbove ? Signal.Buy : Signal.Sell;
            }
            prevFastAbove = fastAbove;
        }
        return signals;
    }
}

/// Buy when RSI(14) drops below `low` (default 30 — oversold), sell when it
/// climbs above `high` (default 70 — overbought). Fires more often than
/// SMA crossover and tends to do well in range-bound markets.
public sealed class RsiMeanReversionStrategy : ISignalStrategy
{
    public string Name => "rsi_mean_reversion";

    public Signal[] Generate(IReadOnlyList<Candle> candles, IReadOnlyDictionary<string, double> @params)
    {
        var period = (int)(@params.TryGetValue("period", out var p) ? p : 14);
        var low = (decimal)(@params.TryGetValue("low", out var lo) ? lo : 30);
        var high = (decimal)(@params.TryGetValue("high", out var hi) ? hi : 70);

        var closes = candles.Select(c => c.Close).ToArray();
        var rsi = Indicators.Rsi(closes, period);

        var signals = new Signal[candles.Count];
        bool? prevWasOversold = null;
        bool? prevWasOverbought = null;
        for (var i = 0; i < candles.Count; i++)
        {
            if (rsi[i] is not { } v) continue;
            var oversold = v < low;
            var overbought = v > high;
            // Fire on the bar where RSI re-enters the neutral zone, so we don't
            // sit through the whole oversold leg generating duplicate signals.
            if (prevWasOversold == true && !oversold) signals[i] = Signal.Buy;
            else if (prevWasOverbought == true && !overbought) signals[i] = Signal.Sell;
            prevWasOversold = oversold;
            prevWasOverbought = overbought;
        }
        return signals;
    }
}

public interface IStrategyRegistry
{
    IReadOnlyCollection<string> AvailableStrategies { get; }
    ISignalStrategy Resolve(string name);
}

public sealed class StrategyRegistry : IStrategyRegistry
{
    private readonly IReadOnlyDictionary<string, ISignalStrategy> _byName;

    public StrategyRegistry(IEnumerable<ISignalStrategy> strategies)
        => _byName = strategies.ToDictionary(s => s.Name, StringComparer.OrdinalIgnoreCase);

    public IReadOnlyCollection<string> AvailableStrategies => _byName.Keys.ToArray();

    public ISignalStrategy Resolve(string name)
    {
        if (!_byName.TryGetValue(name, out var strat))
            throw new ArgumentException(
                $"Unknown strategy '{name}'. Available: {string.Join(", ", _byName.Keys)}");
        return strat;
    }
}

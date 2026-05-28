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
        var closes = candles.Select(c => c.AdjOrClose).ToArray();
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

        var closes = candles.Select(c => c.AdjOrClose).ToArray();
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

/// Classic MACD signal-line crossover. Buy when MACD crosses above signal,
/// sell when it crosses below. Faster than SMA crossover (uses EMAs) but
/// still trend-following — whipsaws in chop.
public sealed class MacdSignalCrossStrategy : ISignalStrategy
{
    public string Name => "macd_signal_cross";

    public Signal[] Generate(IReadOnlyList<Candle> candles, IReadOnlyDictionary<string, double> @params)
    {
        var fast = (int)(@params.TryGetValue("fast", out var f) ? f : 12);
        var slow = (int)(@params.TryGetValue("slow", out var s) ? s : 26);
        var sigP = (int)(@params.TryGetValue("signal", out var g) ? g : 9);

        var closes = candles.Select(c => c.AdjOrClose).ToArray();
        var (macd, signalLine, _) = Indicators.Macd(closes, fast, slow, sigP);

        var signals = new Signal[candles.Count];
        bool? prevAbove = null;
        for (var i = slow; i < candles.Count; i++)
        {
            if (macd[i] is not { } m || signalLine[i] is not { } sl) continue;
            var above = m > sl;
            if (prevAbove.HasValue && above != prevAbove.Value)
            {
                signals[i] = above ? Signal.Buy : Signal.Sell;
            }
            prevAbove = above;
        }
        return signals;
    }
}

/// Donchian breakout. Buy when today's close exceeds the highest close of
/// the prior `lookback` bars (a true momentum breakout); sell when it
/// drops below the prior `lookback` bars' low. Trend-following with
/// no smoothing — catches strong trends, flat in range-bound markets.
public sealed class DonchianBreakoutStrategy : ISignalStrategy
{
    public string Name => "donchian_breakout";

    public Signal[] Generate(IReadOnlyList<Candle> candles, IReadOnlyDictionary<string, double> @params)
    {
        var lookback = (int)(@params.TryGetValue("lookback", out var lb) ? lb : 20);
        var closes = candles.Select(c => c.AdjOrClose).ToArray();
        var (high, low) = Indicators.Donchian(closes, lookback);

        var signals = new Signal[candles.Count];
        bool? prevAboveHigh = null;
        bool? prevBelowLow = null;
        for (var i = lookback; i < candles.Count; i++)
        {
            if (high[i] is not { } h || low[i] is not { } l) continue;
            var aboveHigh = closes[i] > h;
            var belowLow = closes[i] < l;
            if (prevAboveHigh == false && aboveHigh) signals[i] = Signal.Buy;
            else if (prevBelowLow == false && belowLow) signals[i] = Signal.Sell;
            prevAboveHigh = aboveHigh;
            prevBelowLow = belowLow;
        }
        return signals;
    }
}

/// Ichimoku Cloud. Long entry: close crosses above the cloud (max of
/// senkou A/B) AND Chikou confirms (close > close 26 bars ago). Long
/// exit: close crosses below Kijun. Mirrors the Python
/// strategies.ichimoku_cloud signal logic so backtest math == comparator
/// math.
public sealed class IchimokuCloudStrategy : ISignalStrategy
{
    public string Name => "ichimoku_cloud";

    public Signal[] Generate(IReadOnlyList<Candle> candles, IReadOnlyDictionary<string, double> @params)
    {
        var tenkanP = (int)(@params.TryGetValue("tenkan", out var t) ? t : 9);
        var kijunP = (int)(@params.TryGetValue("kijun", out var k) ? k : 26);
        var senkouBP = (int)(@params.TryGetValue("senkou_b", out var sb) ? sb : 52);
        var displacement = (int)(@params.TryGetValue("displacement", out var d) ? d : 26);

        var n = candles.Count;
        var signals = new Signal[n];
        if (n == 0) return signals;

        var high = candles.Select(c => c.High).ToArray();
        var low = candles.Select(c => c.Low).ToArray();
        var close = candles.Select(c => c.AdjOrClose).ToArray();
        var (_, kijun, cloudHi, _, _) = Indicators.Ichimoku(
            high, low, close, tenkanP, kijunP, senkouBP, displacement);

        bool? prevAboveCloud = null;
        bool? prevBelowKijun = null;
        for (var i = 0; i < n; i++)
        {
            // Entry gate. Need cloud + 26-bar lookback for Chikou.
            if (cloudHi[i] is { } ch && i >= displacement)
            {
                var aboveCloud = close[i] > ch;
                var chikouOk = close[i] > close[i - displacement];
                if (prevAboveCloud == false && aboveCloud && chikouOk)
                    signals[i] = Signal.Buy;
                prevAboveCloud = aboveCloud;
            }
            // Exit gate. Independent of entry — handles the cross-below
            // even when we'd otherwise be evaluating a fresh entry.
            if (kijun[i] is { } kj)
            {
                var belowKijun = close[i] < kj;
                if (prevBelowKijun == false && belowKijun && signals[i] == Signal.Hold)
                    signals[i] = Signal.Sell;
                prevBelowKijun = belowKijun;
            }
        }
        return signals;
    }
}

/// Bollinger Band mean-reversion. Long entry: close below lower band
/// AND RSI < oversold threshold (dual trigger filters the "walking
/// down the band" false positives). Long exit: close back at the
/// middle band OR above the upper band (take-profit).
public sealed class BollingerBounceStrategy : ISignalStrategy
{
    public string Name => "bollinger_bounce";

    public Signal[] Generate(IReadOnlyList<Candle> candles, IReadOnlyDictionary<string, double> @params)
    {
        var window = (int)(@params.TryGetValue("window", out var w) ? w : 20);
        var numStd = @params.TryGetValue("num_std", out var ns) ? ns : 2.0;
        var rsiPeriod = (int)(@params.TryGetValue("rsi_period", out var rp) ? rp : 14);
        var rsiOversold = (decimal)(@params.TryGetValue("rsi_oversold", out var ro) ? ro : 35.0);

        var n = candles.Count;
        var signals = new Signal[n];
        if (n == 0) return signals;
        var closes = candles.Select(c => c.AdjOrClose).ToArray();
        var (mid, upper, lower) = Indicators.Bollinger(closes, window, numStd);
        var rsi = Indicators.Rsi(closes, rsiPeriod);

        bool? prevEntryCond = null;
        bool? prevExitCond = null;
        for (var i = 0; i < n; i++)
        {
            if (mid[i] is not { } m || lower[i] is not { } lo || upper[i] is not { } up
                || rsi[i] is not { } r)
            {
                prevEntryCond = null;
                prevExitCond = null;
                continue;
            }
            var entryCond = closes[i] < lo && r < rsiOversold;
            var exitCond = closes[i] >= m || closes[i] > up;
            if (prevEntryCond == false && entryCond) signals[i] = Signal.Buy;
            else if (prevExitCond == false && exitCond) signals[i] = Signal.Sell;
            prevEntryCond = entryCond;
            prevExitCond = exitCond;
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

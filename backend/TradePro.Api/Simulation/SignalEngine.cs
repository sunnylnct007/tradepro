using TradePro.Api.Models;
using TradePro.Api.Providers;

namespace TradePro.Api.Simulation;

public interface ISignalEngine
{
    Task<SignalDecision> EvaluateAsync(SignalRequest request, CancellationToken ct);
}

/// Evaluates the chosen strategy on the latest candle and returns a BUY / SELL /
/// HOLD recommendation with a short list of reasons. This is deliberately
/// conservative — the purpose is to surface the signal the backtest is acting
/// on, not to pretend at alpha it doesn't have.
public sealed class SignalEngine : ISignalEngine
{
    private readonly IMarketDataRegistry _providers;
    private readonly IStrategyRegistry _strategies;

    public SignalEngine(IMarketDataRegistry providers, IStrategyRegistry strategies)
    {
        _providers = providers;
        _strategies = strategies;
    }

    public async Task<SignalDecision> EvaluateAsync(SignalRequest req, CancellationToken ct)
    {
        var provider = _providers.Resolve(req.Provider);
        var strategy = _strategies.Resolve(req.Strategy);

        var to = DateTime.UtcNow;
        var from = to.AddDays(-Math.Max(req.LookbackDays, 365));
        var series = await provider.GetCandlesAsync(req.Symbol, "1d", from, to, ct);
        var candles = series.Candles;

        if (candles.Count == 0)
        {
            return new SignalDecision(
                req.Symbol, strategy.Name, to, "HOLD", 0.0,
                new[] { "No data returned by the provider." },
                new IndicatorSnapshot(null, null, null, null, null, null, null),
                null, null);
        }

        var closes = candles.Select(c => c.Close).ToList();
        var signals = strategy.Generate(candles, req.Params ?? new Dictionary<string, double>());

        // Find the most recent non-hold signal within the lookback window.
        int lastActionIdx = -1;
        for (var i = signals.Length - 1; i >= 0; i--)
        {
            if (signals[i] != Signal.Hold) { lastActionIdx = i; break; }
        }

        var lastIdx = candles.Count - 1;
        var lastClose = closes[lastIdx];
        var sma20 = Indicators.Sma(closes, 20)[lastIdx];
        var sma50 = Indicators.Sma(closes, 50)[lastIdx];
        var sma200 = closes.Count >= 200 ? Indicators.Sma(closes, 200)[lastIdx] : null;
        var rsi14 = Rsi(closes, 14);

        var high52 = closes.TakeLast(252).Max();
        var low52 = closes.TakeLast(252).Min();
        var vs52wHigh = high52 > 0 ? (lastClose - high52) / high52 * 100m : (decimal?)null;
        var vs52wLow = low52 > 0 ? (lastClose - low52) / low52 * 100m : (decimal?)null;

        var snapshot = new IndicatorSnapshot(sma20, sma50, sma200, rsi14, lastClose, vs52wHigh, vs52wLow);

        var reasons = new List<string>();
        string action;
        double confidence;

        // Fresh signal in the last 3 bars — surface it as the primary recommendation.
        if (lastActionIdx >= 0 && (lastIdx - lastActionIdx) <= 3)
        {
            action = signals[lastActionIdx] == Signal.Buy ? "BUY" : "SELL";
            reasons.Add($"{strategy.Name} triggered {action} on {candles[lastActionIdx].Timestamp:yyyy-MM-dd}.");
            confidence = 0.65;
        }
        else
        {
            action = "HOLD";
            reasons.Add($"{strategy.Name} has no fresh signal — last action was " +
                (lastActionIdx >= 0 ? $"{candles[lastActionIdx].Timestamp:yyyy-MM-dd}." : "never in window."));
            confidence = 0.4;
        }

        // Supporting context — tips the confidence up or down.
        if (sma20.HasValue && sma50.HasValue)
        {
            var trend = sma20 > sma50 ? "up" : "down";
            reasons.Add($"SMA20 vs SMA50: {trend}-trend ({sma20:F2} vs {sma50:F2}).");
            if (action == "BUY" && trend == "up") confidence += 0.1;
            if (action == "SELL" && trend == "down") confidence += 0.1;
        }
        if (rsi14 is { } rsi)
        {
            if (rsi > 70) { reasons.Add($"RSI14 = {rsi:F1} → overbought."); if (action == "BUY") confidence -= 0.1; }
            else if (rsi < 30) { reasons.Add($"RSI14 = {rsi:F1} → oversold."); if (action == "SELL") confidence -= 0.1; }
            else reasons.Add($"RSI14 = {rsi:F1} → neutral.");
        }
        if (vs52wHigh.HasValue) reasons.Add($"{vs52wHigh:F1}% from 52w high, {vs52wLow:F1}% from 52w low.");

        confidence = Math.Clamp(confidence, 0.0, 0.95);

        // Simple risk guidance — never hardcoded positions; user picks.
        decimal? stopPct = action == "BUY" ? 5m : (action == "SELL" ? (decimal?)null : null);
        decimal? targetPct = action == "BUY" ? 10m : null;

        return new SignalDecision(
            req.Symbol, strategy.Name, candles[lastIdx].Timestamp,
            action, confidence, reasons, snapshot, stopPct, targetPct);
    }

    private static decimal? Rsi(IReadOnlyList<decimal> closes, int period)
    {
        if (closes.Count <= period) return null;
        decimal gainAvg = 0, lossAvg = 0;
        for (var i = 1; i <= period; i++)
        {
            var delta = closes[i] - closes[i - 1];
            if (delta > 0) gainAvg += delta; else lossAvg -= delta;
        }
        gainAvg /= period;
        lossAvg /= period;
        for (var i = period + 1; i < closes.Count; i++)
        {
            var delta = closes[i] - closes[i - 1];
            var gain = delta > 0 ? delta : 0m;
            var loss = delta < 0 ? -delta : 0m;
            gainAvg = (gainAvg * (period - 1) + gain) / period;
            lossAvg = (lossAvg * (period - 1) + loss) / period;
        }
        if (lossAvg == 0m) return 100m;
        var rs = gainAvg / lossAvg;
        return 100m - 100m / (1m + rs);
    }
}

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

        // Use split/distribution-adjusted prices throughout — see
        // Candle.AdjOrClose. Without this, a single distribution turns
        // a flat year into a fake 20% drawdown vs 52w high (SWDA.L
        // exhibited exactly this in the Claude Desktop ETF Q&A).
        var closes = candles.Select(c => c.AdjOrClose).ToList();
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

        // Whether the strategy keeps you in the market between transactions.
        // Buy & Hold is always-long after its single BUY; SMA / RSI step in
        // and out, so HOLD means "stay flat" for them but "stay invested"
        // for Buy & Hold.
        var isAlwaysLong = strategy.Name == "buy_and_hold";

        // Position state — was the most recent action a BUY or a SELL?
        var lastSignalKind = lastActionIdx >= 0 ? signals[lastActionIdx] : Signal.Hold;
        var inPosition = isAlwaysLong
            ? lastActionIdx >= 0          // bought on day 1, still holding
            : lastSignalKind == Signal.Buy; // bought-and-not-yet-sold

        // Fresh signal in the last 3 bars — surface it as the primary recommendation.
        if (lastActionIdx >= 0 && (lastIdx - lastActionIdx) <= 3)
        {
            action = lastSignalKind == Signal.Buy ? "BUY" : "SELL";
            reasons.Add($"{HumanStrategy(strategy.Name)} triggered {action} on {candles[lastActionIdx].Timestamp:yyyy-MM-dd}.");
            confidence = 0.65;
        }
        else
        {
            action = "HOLD";
            if (inPosition)
            {
                var entry = candles[lastActionIdx].AdjOrClose;
                var pnlPct = entry > 0 ? (lastClose - entry) / entry * 100m : 0m;
                var pnlSign = pnlPct >= 0 ? "+" : "";
                reasons.Add(
                    $"Stay invested — entered {candles[lastActionIdx].Timestamp:yyyy-MM-dd} at {entry:F2}, " +
                    $"now {lastClose:F2} ({pnlSign}{pnlPct:F1}%).");
            }
            else if (isAlwaysLong)
            {
                // Buy & Hold but no data yet — should be rare.
                reasons.Add("Buy & Hold: no entry recorded in the lookback window.");
            }
            else
            {
                var lastTxt = lastActionIdx >= 0
                    ? $"Last action was {(lastSignalKind == Signal.Buy ? "BUY" : "SELL")} on {candles[lastActionIdx].Timestamp:yyyy-MM-dd}."
                    : "No action in the lookback window yet.";
                var hint = NextCrossoverHint(strategy.Name, sma20, sma50, rsi14);
                reasons.Add($"Stay flat. {lastTxt}{(hint is null ? "" : " " + hint)}");
            }
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

    private static string HumanStrategy(string name) => name switch
    {
        "buy_and_hold" => "Buy & Hold",
        "sma_crossover" => "SMA crossover",
        "rsi_mean_reversion" => "RSI mean-reversion",
        "macd_signal_cross" => "MACD signal-cross",
        "donchian_breakout" => "Donchian breakout",
        _ => name,
    };

    /// Tells the user what would have to happen for the strategy to fire next,
    /// so the HOLD recommendation isn't a dead end.
    private static string? NextCrossoverHint(
        string strategy, decimal? sma20, decimal? sma50, decimal? rsi14)
    {
        switch (strategy)
        {
            case "sma_crossover":
                if (sma20 is not { } f || sma50 is not { } s || s == 0m) return null;
                var gapPct = (s - f) / s * 100m;
                if (gapPct > 0)
                    return $"Next golden cross would need SMA20 to rise +{gapPct:F1}% to cross SMA50.";
                return $"Next death cross would need SMA20 to fall {gapPct:F1}% to cross SMA50.";

            case "rsi_mean_reversion":
                if (rsi14 is not { } r) return null;
                if (r >= 30 && r <= 70)
                    return $"RSI14 is {r:F0} — neutral zone. Buy fires under 30, sell over 70.";
                if (r < 30) return $"RSI14 is {r:F0} (oversold). Buy fires when it climbs back above 30.";
                return $"RSI14 is {r:F0} (overbought). Sell fires when it drops back below 70.";

            case "macd_signal_cross":
                return "Buy fires when MACD line crosses above its signal line; sell on the reverse.";

            case "donchian_breakout":
                return "Buy fires on a close above the prior N-day high; sell on a close below the prior N-day low.";

            default:
                return null;
        }
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

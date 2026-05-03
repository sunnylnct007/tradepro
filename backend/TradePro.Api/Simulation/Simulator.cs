using TradePro.Api.Models;
using TradePro.Api.Providers;

namespace TradePro.Api.Simulation;

public interface ISimulator
{
    Task<SimulationResult> RunAsync(SimulationRequest request, CancellationToken ct);
}

public sealed class Simulator : ISimulator
{
    private readonly IMarketDataRegistry _providers;
    private readonly IStrategyRegistry _strategies;

    public Simulator(IMarketDataRegistry providers, IStrategyRegistry strategies)
    {
        _providers = providers;
        _strategies = strategies;
    }

    public async Task<SimulationResult> RunAsync(SimulationRequest req, CancellationToken ct)
    {
        var provider = _providers.Resolve(req.Provider);
        var strategy = _strategies.Resolve(req.Strategy);
        var fees = req.Fees ?? FeeModels.Uk;

        var series = await provider.GetCandlesAsync(req.Symbol, "1d", req.From, req.To, ct);
        var candles = series.Candles;
        if (candles.Count == 0)
        {
            return Empty(req);
        }

        var signals = strategy.Generate(candles, req.Params ?? new Dictionary<string, double>());

        decimal cash = req.InitialCapital;
        decimal qty = 0m;
        var trades = new List<Trade>();
        var equity = new List<EquityPoint>(candles.Count);

        for (var i = 0; i < candles.Count; i++)
        {
            var price = candles[i].AdjOrClose;

            if (signals[i] == Signal.Buy && qty == 0m && cash > 0m)
            {
                // effective cost per share includes stamp duty on notional + flat commission amortised
                var notional = cash - fees.CommissionPerTrade;
                if (notional <= 0m) continue;
                var effectivePrice = price * (1m + fees.StampDutyRate);
                var boughtQty = Math.Floor(notional / effectivePrice * 10000m) / 10000m;
                if (boughtQty <= 0m) continue;
                var stamp = boughtQty * price * fees.StampDutyRate;
                var totalFees = stamp + fees.CommissionPerTrade;
                cash -= boughtQty * price + totalFees;
                qty += boughtQty;
                trades.Add(new Trade(candles[i].Timestamp, "BUY", price, boughtQty, totalFees, strategy.Name));
            }
            else if (signals[i] == Signal.Sell && qty > 0m)
            {
                var proceeds = qty * price - fees.CommissionPerTrade;
                cash += proceeds;
                trades.Add(new Trade(candles[i].Timestamp, "SELL", price, qty, fees.CommissionPerTrade, strategy.Name));
                qty = 0m;
            }

            var mark = cash + qty * price;
            equity.Add(new EquityPoint(candles[i].Timestamp, mark, cash, qty * price));
        }

        // Close any open position at the last close so the PnL is realised.
        if (qty > 0m && candles.Count > 0)
        {
            var lastPrice = candles[^1].AdjOrClose;
            var proceeds = qty * lastPrice - fees.CommissionPerTrade;
            cash += proceeds;
            trades.Add(new Trade(candles[^1].Timestamp, "SELL", lastPrice, qty, fees.CommissionPerTrade, "close_at_end"));
            qty = 0m;
            if (equity.Count > 0)
                equity[^1] = equity[^1] with { Equity = cash, Cash = cash, Position = 0m };
        }

        var finalEquity = equity.Count > 0 ? equity[^1].Equity : req.InitialCapital;
        var years = Math.Max((candles[^1].Timestamp - candles[0].Timestamp).TotalDays / 365.25, 1.0 / 365.25);
        var totalReturn = (finalEquity - req.InitialCapital) / req.InitialCapital;
        var cagr = (decimal)(Math.Pow((double)(finalEquity / req.InitialCapital), 1.0 / years) - 1.0);

        return new SimulationResult(
            req.Symbol,
            strategy.Name,
            req.Currency,
            req.InitialCapital,
            finalEquity,
            totalReturn * 100m,
            cagr * 100m,
            MaxDrawdownPct(equity),
            Sharpe(equity),
            trades.Count,
            trades,
            equity);
    }

    private static SimulationResult Empty(SimulationRequest req) => new(
        req.Symbol, req.Strategy, req.Currency, req.InitialCapital,
        req.InitialCapital, 0m, 0m, 0m, 0m, 0, Array.Empty<Trade>(), Array.Empty<EquityPoint>());

    private static decimal MaxDrawdownPct(IReadOnlyList<EquityPoint> curve)
    {
        if (curve.Count == 0) return 0m;
        decimal peak = curve[0].Equity;
        decimal worst = 0m;
        foreach (var p in curve)
        {
            if (p.Equity > peak) peak = p.Equity;
            if (peak > 0m)
            {
                var dd = (p.Equity - peak) / peak;
                if (dd < worst) worst = dd;
            }
        }
        return worst * 100m;
    }

    private static decimal Sharpe(IReadOnlyList<EquityPoint> curve)
    {
        if (curve.Count < 3) return 0m;
        var rets = new List<double>(curve.Count);
        for (var i = 1; i < curve.Count; i++)
        {
            var prev = (double)curve[i - 1].Equity;
            var cur = (double)curve[i].Equity;
            if (prev > 0) rets.Add((cur - prev) / prev);
        }
        if (rets.Count == 0) return 0m;
        var mean = rets.Average();
        var variance = rets.Sum(r => (r - mean) * (r - mean)) / rets.Count;
        var stdev = Math.Sqrt(variance);
        if (stdev == 0) return 0m;
        // Annualise assuming ~252 trading days.
        return (decimal)(mean / stdev * Math.Sqrt(252));
    }
}

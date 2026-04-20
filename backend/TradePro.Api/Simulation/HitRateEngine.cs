using TradePro.Api.Models;
using TradePro.Api.Providers;

namespace TradePro.Api.Simulation;

public interface IHitRateEngine
{
    Task<HitRateResult> ComputeAsync(HitRateRequest request, CancellationToken ct);
}

/// Walks the strategy across the full lookback window, pairs BUY→SELL into
/// trades, and returns the aggregate hit statistics. This is the "how often
/// has this signal actually worked on this symbol?" view — the right answer
/// to a 65%-confidence display is "well, over 10 years it was right 11/18
/// times with avg winner +8.2% and avg loser −4.1%".
public sealed class HitRateEngine : IHitRateEngine
{
    private readonly IMarketDataRegistry _providers;
    private readonly IStrategyRegistry _strategies;

    public HitRateEngine(IMarketDataRegistry providers, IStrategyRegistry strategies)
    {
        _providers = providers;
        _strategies = strategies;
    }

    public async Task<HitRateResult> ComputeAsync(HitRateRequest req, CancellationToken ct)
    {
        var provider = _providers.Resolve(req.Provider);
        var strategy = _strategies.Resolve(req.Strategy);

        var to = DateTime.UtcNow;
        var years = Math.Clamp(req.LookbackYears, 1, 30);
        var from = to.AddYears(-years);

        var series = await provider.GetCandlesAsync(req.Symbol, "1d", from, to, ct);
        var candles = series.Candles;
        if (candles.Count == 0)
        {
            return Empty(req, from, to);
        }

        var signals = strategy.Generate(candles, req.Params ?? new Dictionary<string, double>());
        var trades = new List<HitRateTrade>();

        // Track an open position and close it on SELL. If the strategy never
        // fires a SELL after a BUY, the trade is "open" and excluded from
        // win-rate maths but shown in the list.
        DateTime? entryDate = null;
        decimal entryPrice = 0m;

        for (var i = 0; i < candles.Count; i++)
        {
            if (signals[i] == Signal.Buy && entryDate is null)
            {
                entryDate = candles[i].Timestamp;
                entryPrice = candles[i].Close;
            }
            else if (signals[i] == Signal.Sell && entryDate is not null)
            {
                var exit = candles[i].Close;
                var ret = entryPrice > 0m ? (exit - entryPrice) / entryPrice * 100m : 0m;
                var days = (int)(candles[i].Timestamp - entryDate.Value).TotalDays;
                trades.Add(new HitRateTrade(
                    EntryDate: entryDate.Value,
                    ExitDate: candles[i].Timestamp,
                    EntryPrice: entryPrice,
                    ExitPrice: exit,
                    ReturnPct: ret,
                    HoldingDays: days,
                    IsOpen: false));
                entryDate = null;
                entryPrice = 0m;
            }
        }

        if (entryDate is not null)
        {
            trades.Add(new HitRateTrade(
                EntryDate: entryDate.Value,
                ExitDate: null,
                EntryPrice: entryPrice,
                ExitPrice: null,
                ReturnPct: null,
                HoldingDays: null,
                IsOpen: true));
        }

        var closed = trades.Where(t => !t.IsOpen && t.ReturnPct.HasValue).ToArray();
        var winners = closed.Where(t => t.ReturnPct!.Value > 0m).ToArray();
        var losers  = closed.Where(t => t.ReturnPct!.Value <= 0m).ToArray();

        decimal winRatePct = closed.Length == 0 ? 0m
            : (decimal)winners.Length / closed.Length * 100m;

        decimal avgWin  = winners.Length == 0 ? 0m : winners.Average(t => t.ReturnPct!.Value);
        decimal avgLoss = losers.Length == 0  ? 0m : losers.Average(t => t.ReturnPct!.Value);
        decimal expectancy = closed.Length == 0 ? 0m : closed.Average(t => t.ReturnPct!.Value);
        decimal totalRet = closed.Sum(t => t.ReturnPct!.Value);
        decimal best  = closed.Length == 0 ? 0m : closed.Max(t => t.ReturnPct!.Value);
        decimal worst = closed.Length == 0 ? 0m : closed.Min(t => t.ReturnPct!.Value);

        decimal medianHold = 0m;
        if (closed.Length > 0)
        {
            var sorted = closed.Select(t => t.HoldingDays ?? 0).OrderBy(d => d).ToArray();
            medianHold = sorted.Length % 2 == 1
                ? sorted[sorted.Length / 2]
                : (sorted[sorted.Length / 2 - 1] + sorted[sorted.Length / 2]) / 2m;
        }

        return new HitRateResult(
            Symbol: req.Symbol,
            Strategy: strategy.Name,
            From: from,
            To: to,
            TotalTrades: trades.Count,
            Winners: winners.Length,
            Losers: losers.Length,
            WinRatePct: winRatePct,
            AvgWinnerPct: avgWin,
            AvgLoserPct: avgLoss,
            MedianHoldingDays: medianHold,
            BestPct: best,
            WorstPct: worst,
            ExpectancyPct: expectancy,
            TotalReturnPct: totalRet,
            Trades: trades);
    }

    private static HitRateResult Empty(HitRateRequest req, DateTime from, DateTime to) =>
        new(req.Symbol, req.Strategy, from, to,
            0, 0, 0, 0m, 0m, 0m, 0m, 0m, 0m, 0m, 0m,
            Array.Empty<HitRateTrade>());
}

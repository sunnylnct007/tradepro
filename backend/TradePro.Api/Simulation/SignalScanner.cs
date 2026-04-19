using TradePro.Api.Models;
using TradePro.Api.Watchlists;

namespace TradePro.Api.Simulation;

public interface ISignalScanner
{
    Task<ScanResult> ScanAsync(ScanRequest request, CancellationToken ct);
}

/// Fans a signal evaluation across a defined list of symbols and groups the
/// results into BUY / SELL / HOLD buckets, ranked by confidence. This is the
/// "what's worth buying or selling today?" view.
public sealed class SignalScanner : ISignalScanner
{
    private readonly ISignalEngine _engine;
    private readonly IWatchlistStore _watchlists;

    public SignalScanner(ISignalEngine engine, IWatchlistStore watchlists)
    {
        _engine = engine;
        _watchlists = watchlists;
    }

    public async Task<ScanResult> ScanAsync(ScanRequest req, CancellationToken ct)
    {
        IReadOnlyList<(string Symbol, string Label)> universe;

        if (req.Symbols is { Length: > 0 })
        {
            universe = req.Symbols.Select(s => (s, s)).ToArray();
        }
        else
        {
            var name = req.Watchlist ?? "uk";
            var list = _watchlists.Get(name)
                ?? throw new ArgumentException(
                    $"Unknown watchlist '{name}'. Available: {string.Join(", ", _watchlists.Keys)}");
            universe = list.Items.Select(i => (i.Symbol, i.Label)).ToArray();
        }

        var buys = new List<ScanResultItem>();
        var sells = new List<ScanResultItem>();
        var holds = new List<ScanResultItem>();
        var errors = new List<string>();

        // Throttle concurrency — the free providers will rate-limit us otherwise.
        using var sem = new SemaphoreSlim(4);
        var tasks = universe.Select(async entry =>
        {
            await sem.WaitAsync(ct);
            try
            {
                var decision = await _engine.EvaluateAsync(
                    new SignalRequest(entry.Symbol, req.Provider, req.Strategy, 365, req.Params), ct);
                return (entry, decision, error: (string?)null);
            }
            catch (Exception ex)
            {
                return (entry, (SignalDecision?)null, error: $"{entry.Symbol}: {ex.Message}");
            }
            finally { sem.Release(); }
        });

        foreach (var result in await Task.WhenAll(tasks))
        {
            if (result.error is not null) { errors.Add(result.error); continue; }
            if (result.decision is null) continue;

            var item = new ScanResultItem(result.entry.Symbol, result.entry.Label, result.decision);
            switch (result.decision.Action)
            {
                case "BUY": buys.Add(item); break;
                case "SELL": sells.Add(item); break;
                default: holds.Add(item); break;
            }
        }

        return new ScanResult(
            Watchlist: req.Watchlist ?? (req.Symbols is { Length: > 0 } ? "(custom)" : "uk"),
            Strategy: req.Strategy,
            GeneratedAt: DateTime.UtcNow,
            Buys: buys.OrderByDescending(i => i.Decision.Confidence).ToArray(),
            Sells: sells.OrderByDescending(i => i.Decision.Confidence).ToArray(),
            Holds: holds.OrderByDescending(i => i.Decision.Confidence).ToArray(),
            Errors: errors);
    }
}

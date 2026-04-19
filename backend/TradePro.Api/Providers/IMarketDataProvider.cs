using TradePro.Api.Models;

namespace TradePro.Api.Providers;

public interface IMarketDataProvider
{
    string Name { get; }

    Task<CandleSeries> GetCandlesAsync(
        string symbol,
        string interval,
        DateTime from,
        DateTime to,
        CancellationToken ct);
}

public interface IMarketDataRegistry
{
    IReadOnlyCollection<string> AvailableProviders { get; }
    IMarketDataProvider Resolve(string? name);
}

public sealed class MarketDataRegistry : IMarketDataRegistry
{
    private readonly IReadOnlyDictionary<string, IMarketDataProvider> _byName;
    private readonly string _default;

    public MarketDataRegistry(
        IEnumerable<IMarketDataProvider> providers,
        IConfiguration config)
    {
        _byName = providers.ToDictionary(p => p.Name, StringComparer.OrdinalIgnoreCase);
        _default = config["Providers:Default"] ?? _byName.Keys.First();
    }

    public IReadOnlyCollection<string> AvailableProviders => _byName.Keys.ToArray();

    public IMarketDataProvider Resolve(string? name)
    {
        var key = string.IsNullOrWhiteSpace(name) ? _default : name;
        if (!_byName.TryGetValue(key, out var provider))
        {
            throw new ArgumentException(
                $"Unknown provider '{key}'. Available: {string.Join(", ", _byName.Keys)}");
        }
        return provider;
    }
}

using System.Collections.Concurrent;
using Dapper;
using Npgsql;
using TradePro.Api.Oms;

namespace TradePro.Api.Instruments;

/// <summary>
/// Read-side, broker-agnostic translation of a strategy/universe symbol
/// into the broker's order ticker. Consults the DATA in broker_ticker_map
/// first (the authoritative, catalog-derived map maintained by
/// <see cref="BrokerTickerMapBuilder"/>) and falls back to the naive
/// <see cref="SymbolHarmonization.ToBrokerTicker"/> suffix heuristic only
/// when the map has no entry — so a correct override always wins over the
/// guess, but an unmapped symbol still routes somewhere rather than
/// throwing.
///
/// NOTE: deliberately NOT yet wired into PostgresOmsService.ApproveAsync
/// (that has its own ResolveBrokerSymbolAsync today). Swapping the order
/// path onto this resolver is a separate, reviewed step.
/// </summary>
public interface IInstrumentResolver
{
    Task<string> ToBrokerTickerAsync(string symbol, string brokerLabel, CancellationToken ct);
}

public sealed class InstrumentResolver : IInstrumentResolver
{
    private static readonly TimeSpan Ttl = TimeSpan.FromMinutes(5);

    private readonly NpgsqlDataSource _db;
    private readonly ILogger<InstrumentResolver> _log;

    // Per-broker cached map (source_ticker → broker_ticker) with a TTL.
    private readonly ConcurrentDictionary<string, CachedMap> _cache =
        new(StringComparer.OrdinalIgnoreCase);

    public InstrumentResolver(NpgsqlDataSource db, ILogger<InstrumentResolver> log)
    {
        _db = db;
        _log = log;
    }

    public async Task<string> ToBrokerTickerAsync(
        string symbol, string brokerLabel, CancellationToken ct)
    {
        var bare = SymbolHarmonization.BareSourceTicker(symbol);
        var map = await GetMapAsync(brokerLabel, ct);
        if (map.TryGetValue(bare, out var brokerTicker)
            && !string.IsNullOrWhiteSpace(brokerTicker))
        {
            return brokerTicker;
        }
        return SymbolHarmonization.ToBrokerTicker(symbol, brokerLabel);
    }

    private async Task<IReadOnlyDictionary<string, string>> GetMapAsync(
        string brokerLabel, CancellationToken ct)
    {
        if (_cache.TryGetValue(brokerLabel, out var cached)
            && DateTime.UtcNow - cached.LoadedAtUtc < Ttl)
        {
            return cached.Map;
        }

        var map = new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase);
        try
        {
            await using var conn = await _db.OpenConnectionAsync(ct);
            var rows = await conn.QueryAsync<(string source_ticker, string broker_ticker)>(
                new CommandDefinition(@"
                    SELECT source_ticker, broker_ticker
                    FROM broker_ticker_map
                    WHERE broker = @brokerLabel",
                    new { brokerLabel }, cancellationToken: ct));
            foreach (var (src, bt) in rows)
            {
                if (!string.IsNullOrWhiteSpace(src) && !string.IsNullOrWhiteSpace(bt))
                    map[src] = bt;
            }
        }
        catch (Exception ex)
        {
            // Fail open: an empty map just means every symbol falls back
            // to the heuristic — better than blocking order routing on a
            // transient DB hiccup. Serve stale cache if we have one.
            _log.LogWarning(ex,
                "broker_ticker_map load failed for {Broker}; using heuristic fallback",
                brokerLabel);
            if (_cache.TryGetValue(brokerLabel, out var stale)) return stale.Map;
        }

        _cache[brokerLabel] = new CachedMap(map, DateTime.UtcNow);
        return map;
    }

    private sealed record CachedMap(IReadOnlyDictionary<string, string> Map, DateTime LoadedAtUtc);
}

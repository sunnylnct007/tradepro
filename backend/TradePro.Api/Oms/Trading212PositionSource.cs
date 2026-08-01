using TradePro.Api.Providers.Trading212;

namespace TradePro.Api.Oms;

/// <summary>T212 golden-source position accessor for the unified reconciler.
/// Wraps the demo client's positions + order-status (for the real fill price).</summary>
public sealed class Trading212PositionSource : IBrokerPositionSource
{
    private readonly Trading212DemoClient _client;
    private readonly Trading212DemoPositionsCache _positionsCache;

    public Trading212PositionSource(
        Trading212DemoClient client, Trading212DemoPositionsCache positionsCache)
    {
        _client = client;
        _positionsCache = positionsCache;
    }

    public string BrokerLabel => "T212_DEMO";

    public async Task<BrokerPositionsRead> ReadPositionsAsync(CancellationToken ct)
    {
        if (!_client.IsEnabled) return BrokerPositionsRead.Fail("T212 demo disabled");
        try
        {
            // Route through the shared 30s-TTL cache (serves stale on 429) instead
            // of hitting T212 raw on every reconciler tick (60s default) — T212
            // enforces 1 req/1s, and the UI's Portfolio + OMS drift endpoint also
            // read the same demo positions, so an uncached reconciler tick was one
            // more caller tripping "TooManyRequests" (see Trading212DemoPositionsCache
            // docstring — same problem it was built to solve, just not wired here).
            var r = await _positionsCache.GetAsync(ct);
            if (r.Error is not null) return BrokerPositionsRead.Fail(r.Error);
            // T212 nests the ticker in `instrument` on /equity/portfolio; the
            // top-level Ticker is NULL in the wild. Reading p.Ticker alone made
            // every held position look tickerless → the reconciler saw an EMPTY
            // book and (a) falsely settled every in-flight sell and (b) flagged
            // every real holding as broker-flat drift. Same fallback the
            // sync-from-broker path uses (ReconcileMath). Drop any still-null.
            return BrokerPositionsRead.From(
                r.Positions
                 .Select(p => (Ticker: p.Instrument?.Ticker ?? p.Ticker, p.Quantity))
                 .Where(x => !string.IsNullOrWhiteSpace(x.Ticker))
                 .Select(x => (x.Ticker!, x.Quantity))
                 .ToList());
        }
        catch (Exception ex) when (ex is not OperationCanceledException)
        {
            return BrokerPositionsRead.Fail(ex.Message);
        }
    }

    public async Task<decimal?> TryGetFillPriceAsync(string brokerOrderId, CancellationToken ct)
    {
        if (!long.TryParse(brokerOrderId, out var id)) return null;
        var s = await _client.GetOrderStatusAsync(id, ct);
        if (s is null) return null;
        if (s.FillPrice is decimal fp && fp > 0m) return fp;
        if (s.FilledValue is decimal v && s.FilledQuantity is decimal q && q > 0) return v / q;
        return null;
    }
}

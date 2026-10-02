using System.Net;

namespace TradePro.Api.Providers.Trading212;

/// <summary>
/// Mirror of <see cref="Trading212PositionsCache"/> but for the demo
/// client. Both Portfolio (defaults to demo) AND the OMS positions
/// drift endpoint hit the demo /equity/portfolio every page load,
/// and T212 enforces 1 req/1s — without a cache the second call
/// always trips 429 ("BusinessException / TooManyRequests").
///
/// Same TTL contract (default 30s), same fall-back-on-429 behaviour
/// as the live cache so the UI never sees a broken positions panel
/// because of rate-limiting.
/// </summary>
public sealed class Trading212DemoPositionsCache
{
    private readonly IServiceScopeFactory _scopeFactory;
    private readonly ILogger<Trading212DemoPositionsCache> _log;
    private readonly SemaphoreSlim _lock = new(1, 1);
    private readonly TimeSpan _ttl;

    private Trading212PositionsResult? _cached;
    private DateTime _cachedAtUtc;

    public Trading212DemoPositionsCache(
        IServiceScopeFactory scopeFactory,
        IConfiguration config,
        ILogger<Trading212DemoPositionsCache> log)
    {
        _scopeFactory = scopeFactory;
        _log = log;
        var seconds = config.GetValue<int?>("Trading212Demo:PositionsCacheSeconds")
            ?? config.GetValue<int?>("Trading212:PositionsCacheSeconds")
            ?? 30;
        _ttl = TimeSpan.FromSeconds(Math.Clamp(seconds, 5, 600));
    }

    /// <summary>
    /// Cached position read. Pass forceFresh when the answer DECIDES A TRADE.
    ///
    /// The cache exists for a real reason — T212 allows 1 req/sec and the drift
    /// panel racing the Portfolio fetch produced 429s on the trader's screen —
    /// so it stays. But a TTL of up to 600s means a lane can sell, then read a
    /// pre-sale book minutes later and sell the same position again.
    ///
    /// That is not hypothetical. On 1 Oct 2026 the IBKR equivalent served a
    /// stale book to the position seed all night: every read returned
    /// "ESNT 118" while sells filled against it, and a long-only sleeve sold
    /// itself to -2,596 SHORT. T212 had the same shape with no way out at all —
    /// its endpoint exposed no bypass, so a lane could not get an uncached book
    /// even if it knew to ask.
    ///
    /// forceFresh still takes the lock, so concurrent callers coalesce into one
    /// upstream request and the rate limit is respected.
    /// </summary>
    public async Task<Trading212PositionsResult> GetAsync(
        CancellationToken ct, bool forceFresh = false)
    {
        if (!forceFresh
            && _cached is { Error: null } fresh
            && DateTime.UtcNow - _cachedAtUtc < _ttl)
        {
            return WithCacheMeta(fresh, fromCache: true);
        }

        await _lock.WaitAsync(ct);
        try
        {
            if (!forceFresh
                && _cached is { Error: null } recent
                && DateTime.UtcNow - _cachedAtUtc < _ttl)
            {
                return WithCacheMeta(recent, fromCache: true);
            }

            using var scope = _scopeFactory.CreateScope();
            var client = scope.ServiceProvider.GetRequiredService<Trading212DemoClient>();
            var result = await client.GetPositionsAsync(ct);

            // 429 → serve stale; the operator gets stale data + an
            // "as of N seconds ago" indicator instead of a banner.
            if (result.HttpStatus == (int)HttpStatusCode.TooManyRequests
                && _cached is { Error: null } stale)
            {
                _log.LogInformation(
                    "T212 demo returned 429; serving cached positions from {When:o}",
                    _cachedAtUtc);
                return WithCacheMeta(stale, fromCache: true);
            }

            if (result.Error is null)
            {
                _cached = result with { FetchedAtUtc = DateTime.UtcNow };
                _cachedAtUtc = DateTime.UtcNow;
                return WithCacheMeta(_cached, fromCache: false);
            }

            return result;
        }
        finally
        {
            _lock.Release();
        }
    }

    private Trading212PositionsResult WithCacheMeta(
        Trading212PositionsResult r, bool fromCache)
    {
        return r with
        {
            FromCache = fromCache,
            FetchedAtUtc = _cachedAtUtc,
            AgeSeconds = (DateTime.UtcNow - _cachedAtUtc).TotalSeconds,
        };
    }
}

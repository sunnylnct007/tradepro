using System.Net;

namespace TradePro.Api.Providers.Trading212;

/// <summary>
/// In-memory cache for the T212 /equity/positions response.
///
/// Why: T212 enforces 1 req/1s on this endpoint and the dashboard
/// has multiple consumers (HoldingsHealthCard + Portfolio page +
/// future MCP) that each fire on mount. Without a cache the second
/// consumer always trips a 429.
///
/// TTL is short (default 30s) — your portfolio doesn't change in
/// half a minute and the user isn't doing real-time tick monitoring.
/// On a 429 we serve the last successful response (even if older
/// than TTL) with FromCache=true so the UI can render an "as of"
/// timestamp instead of a banner-of-doom.
///
/// Singleton-scoped service. Thread-safe via SemaphoreSlim so
/// concurrent requests collapse to one upstream T212 call.
/// </summary>
public sealed class Trading212PositionsCache
{
    // Trading212Client is a transient typed HttpClient — a singleton
    // can't depend on a transient, so resolve per-call via the scope
    // factory (mirrors the Trading212InstrumentsService pattern).
    private readonly IServiceScopeFactory _scopeFactory;
    private readonly ILogger<Trading212PositionsCache> _log;
    private readonly SemaphoreSlim _lock = new(1, 1);
    private readonly TimeSpan _ttl;

    private Trading212PositionsResult? _cached;
    private DateTime _cachedAtUtc;

    public Trading212PositionsCache(
        IServiceScopeFactory scopeFactory,
        IConfiguration config,
        ILogger<Trading212PositionsCache> log)
    {
        _scopeFactory = scopeFactory;
        _log = log;
        var seconds = config.GetValue<int?>("Trading212:PositionsCacheSeconds") ?? 30;
        _ttl = TimeSpan.FromSeconds(Math.Clamp(seconds, 5, 600));
    }

    public async Task<Trading212PositionsResult> GetAsync(CancellationToken ct)
    {
        // Fresh cache → return without acquiring the upstream lock.
        if (_cached is { Error: null } fresh
            && DateTime.UtcNow - _cachedAtUtc < _ttl)
        {
            return WithCacheMeta(fresh, fromCache: true);
        }

        await _lock.WaitAsync(ct);
        try
        {
            // Re-check after acquiring lock — another caller may have
            // refreshed while we were waiting.
            if (_cached is { Error: null } recent
                && DateTime.UtcNow - _cachedAtUtc < _ttl)
            {
                return WithCacheMeta(recent, fromCache: true);
            }

            using var scope = _scopeFactory.CreateScope();
            var client = scope.ServiceProvider.GetRequiredService<Trading212Client>();
            var result = await client.GetPositionsAsync(ct);

            // 429: serve last good response if we have one. Otherwise
            // pass the 429 up so the UI can render "rate limited".
            if (result.HttpStatus == (int)HttpStatusCode.TooManyRequests
                && _cached is { Error: null } stale)
            {
                _log.LogInformation(
                    "Trading212 returned 429; serving cached positions from {When:o}",
                    _cachedAtUtc);
                return WithCacheMeta(stale, fromCache: true);
            }

            // Successful refresh — replace cache.
            if (result.Error is null)
            {
                _cached = result with { FetchedAtUtc = DateTime.UtcNow };
                _cachedAtUtc = DateTime.UtcNow;
                return WithCacheMeta(_cached, fromCache: false);
            }

            // Failed and no cached fallback — pass the error through.
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

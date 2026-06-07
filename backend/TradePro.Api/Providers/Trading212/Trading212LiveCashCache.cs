using System.Net;

namespace TradePro.Api.Providers.Trading212;

/// <summary>
/// In-memory TTL cache + stale-on-429 fallback for the T212 LIVE
/// /equity/account/cash response. Mirrors <see cref="Trading212DemoCashCache"/>
/// — same shape, same TTL contract, same "serve last good on 429"
/// behaviour. Separate cache instance because the live and demo cash
/// accounts have independent rate-limit buckets.
///
/// Why: the cockpit /cash-summary endpoint is polled on mount and on
/// interval; without a cache every concurrent consumer hits T212's
/// /account/cash bucket, tripping 429 ("BusinessException /
/// TooManyRequests"). With this cache the first caller within the TTL
/// window hits T212; the rest return the in-memory snapshot.
///
/// TTL default 30s (configurable via Trading212:CashCacheSeconds, 5–600).
/// Singleton-scoped. Thread-safe via SemaphoreSlim so concurrent
/// requests collapse to one upstream T212 call.
/// </summary>
public sealed class Trading212LiveCashCache
{
    // Trading212Client is a transient typed HttpClient — a singleton
    // can't depend on a transient, so resolve per-call via the scope
    // factory (mirrors Trading212DemoCashCache pattern).
    private readonly IServiceScopeFactory _scopeFactory;
    private readonly ILogger<Trading212LiveCashCache> _log;
    private readonly SemaphoreSlim _lock = new(1, 1);
    private readonly TimeSpan _ttl;

    private Trading212CashResult? _cached;
    private DateTime _cachedAtUtc;

    public Trading212LiveCashCache(
        IServiceScopeFactory scopeFactory,
        IConfiguration config,
        ILogger<Trading212LiveCashCache> log)
    {
        _scopeFactory = scopeFactory;
        _log = log;
        var seconds = config.GetValue<int?>("Trading212:CashCacheSeconds") ?? 30;
        _ttl = TimeSpan.FromSeconds(Math.Clamp(seconds, 5, 600));
    }

    public TimeSpan Ttl => _ttl;

    public DateTime? CachedAtUtc => _cached is null ? null : _cachedAtUtc;

    public async Task<Trading212CashResult> GetAsync(CancellationToken ct)
    {
        // Fast path — fresh cache, no lock needed.
        if (_cached is { Error: null } fresh
            && DateTime.UtcNow - _cachedAtUtc < _ttl)
        {
            return fresh;
        }

        await _lock.WaitAsync(ct);
        try
        {
            // Double-check after acquiring the lock — another caller may
            // have just refreshed while we were waiting.
            if (_cached is { Error: null } recent
                && DateTime.UtcNow - _cachedAtUtc < _ttl)
            {
                return recent;
            }

            using var scope = _scopeFactory.CreateScope();
            var client = scope.ServiceProvider.GetRequiredService<Trading212Client>();
            var result = await client.GetCashAsync(ct);

            // 429 → serve stale; the operator sees the last good
            // snapshot with the "fetched at" footer instead of an
            // angry red error message on the Portfolio page.
            if (result.HttpStatus == (int)HttpStatusCode.TooManyRequests
                && _cached is { Error: null } stale)
            {
                _log.LogInformation(
                    "T212 live /cash returned 429; serving cached snapshot from {When:o}",
                    _cachedAtUtc);
                return stale;
            }

            if (result.Error is null)
            {
                _cached = result;
                _cachedAtUtc = DateTime.UtcNow;
                return result;
            }

            // Other errors (auth fail, network) — surface up. We don't
            // cache failures; next call retries upstream.
            return result;
        }
        finally
        {
            _lock.Release();
        }
    }
}

using System.Net;

namespace TradePro.Api.Providers.Trading212;

/// <summary>
/// In-memory TTL cache + stale-on-429 fallback for the T212 demo
/// /equity/account/cash response. Mirrors <see cref="Trading212DemoPositionsCache"/>
/// — same shape, same TTL contract, same "serve last good on 429"
/// behaviour. Different upstream endpoint, different rate-limit
/// bucket, so they need separate cache instances.
///
/// Why: the Portfolio page polls cash every render + every interval,
/// and the cockpit pulls it on mount. T212's /account/cash bucket is
/// (1 req/2s in the demo SLA we observed), so the second hit always
/// trips a 429 ("BusinessException / TooManyRequests"). Without
/// caching the user sees "T212 cash error: HTTP 429" in red the
/// moment they open the page. With this cache they see the last
/// good snapshot + an "(as of 14s ago)" footnote.
/// </summary>
public sealed class Trading212DemoCashCache
{
    private readonly IServiceScopeFactory _scopeFactory;
    private readonly ILogger<Trading212DemoCashCache> _log;
    private readonly SemaphoreSlim _lock = new(1, 1);
    private readonly TimeSpan _ttl;

    private Trading212CashResult? _cached;
    private DateTime _cachedAtUtc;

    public Trading212DemoCashCache(
        IServiceScopeFactory scopeFactory,
        IConfiguration config,
        ILogger<Trading212DemoCashCache> log)
    {
        _scopeFactory = scopeFactory;
        _log = log;
        var seconds = config.GetValue<int?>("Trading212Demo:CashCacheSeconds")
            ?? config.GetValue<int?>("Trading212:CashCacheSeconds")
            ?? 30;
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
            // have just refreshed.
            if (_cached is { Error: null } recent
                && DateTime.UtcNow - _cachedAtUtc < _ttl)
            {
                return recent;
            }

            using var scope = _scopeFactory.CreateScope();
            var client = scope.ServiceProvider.GetRequiredService<Trading212DemoClient>();
            var result = await client.GetCashAsync(ct);

            // 429 → serve stale; the operator sees the last good
            // snapshot with the "fetched at" footer instead of an
            // angry red error message on the Portfolio page.
            if (result.HttpStatus == (int)HttpStatusCode.TooManyRequests
                && _cached is { Error: null } stale)
            {
                _log.LogInformation(
                    "T212 demo /cash returned 429; serving cached snapshot from {When:o}",
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

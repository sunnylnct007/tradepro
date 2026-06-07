using Microsoft.Extensions.Configuration;
using Microsoft.Extensions.DependencyInjection;
using Microsoft.Extensions.Logging.Abstractions;
using TradePro.Api.Providers.Trading212;
using Xunit;

namespace TradePro.Api.Tests.Providers;

/// <summary>
/// Unit tests for <see cref="Trading212LiveCashCache"/> — no network, no
/// secrets. Verifies the TTL-caching contract:
///   • First call within TTL reaches the upstream client.
///   • Subsequent calls within TTL return the cached result (no second
///     upstream hit).
///   • Errors are NOT cached; the next call retries upstream.
///   • On HTTP 429 the last successful snapshot is served as fallback.
///
/// Uses a stub IServiceScopeFactory so the cache's scope-factory pattern
/// is exercised without needing a real DI container.
/// </summary>
public class Trading212LiveCashCacheTest
{
    // ── helpers ────────────────────────────────────────────────────────

    private static IConfiguration MakeConfig(int ttlSeconds = 30)
    {
        return new ConfigurationBuilder()
            .AddInMemoryCollection(new Dictionary<string, string?>
            {
                ["Trading212:CashCacheSeconds"] = ttlSeconds.ToString(),
            })
            .Build();
    }

    // A stub that lets the test control what GetCashAsync returns on each call.
    private sealed class FakeT212LiveCashCache
    {
        private readonly Queue<Trading212CashResult> _responses;
        public int CallCount { get; private set; }

        public FakeT212LiveCashCache(IEnumerable<Trading212CashResult> responses)
            => _responses = new Queue<Trading212CashResult>(responses);

        public Trading212CashResult Next()
        {
            CallCount++;
            return _responses.Count > 0
                ? _responses.Dequeue()
                : new Trading212CashResult(null, null, null, null, null, null, "no more responses", 500);
        }
    }

    // Wire a Trading212LiveCashCache with a short TTL and a stub scope
    // factory that drives calls to a FakeT212LiveCashCache.
    private static (Trading212LiveCashCache Cache, FakeT212LiveCashCache Fake)
        Build(IEnumerable<Trading212CashResult> responses, int ttlSeconds = 60)
    {
        var fake = new FakeT212LiveCashCache(responses);
        var scopeFactory = new StubScopeFactory(() => fake.Next());
        var cache = new Trading212LiveCashCache(
            scopeFactory, MakeConfig(ttlSeconds),
            NullLogger<Trading212LiveCashCache>.Instance);
        return (cache, fake);
    }

    private static Trading212CashResult OkResult(decimal free = 1000m) =>
        new(Free: free, Invested: 500m, Total: 1500m, Blocked: null,
            Ppl: 20m, Currency: "GBP", Error: null, HttpStatus: 200);

    // ── tests ──────────────────────────────────────────────────────────

    [Fact]
    public async Task FirstCall_hitsUpstream()
    {
        var (cache, fake) = Build([OkResult()]);
        await cache.GetAsync(CancellationToken.None);
        Assert.Equal(1, fake.CallCount);
    }

    [Fact]
    public async Task SecondCallWithinTtl_returnsCachedResult_withoutUpstreamHit()
    {
        var (cache, fake) = Build([OkResult(free: 999m), OkResult(free: 111m)]);

        var first = await cache.GetAsync(CancellationToken.None);
        var second = await cache.GetAsync(CancellationToken.None);

        Assert.Equal(1, fake.CallCount);   // only ONE upstream hit
        Assert.Equal(999m, first.Free);    // first result served both times
        Assert.Equal(999m, second.Free);
    }

    [Fact]
    public async Task ErrorResult_isNotCached_nextCallRetries()
    {
        var errorResult = new Trading212CashResult(
            null, null, null, null, null, null, "auth failed", 401);
        var (cache, fake) = Build([errorResult, OkResult(free: 777m)]);

        var first = await cache.GetAsync(CancellationToken.None);
        var second = await cache.GetAsync(CancellationToken.None);

        Assert.Equal(2, fake.CallCount);   // error not cached → two upstream hits
        Assert.NotNull(first.Error);
        Assert.Null(second.Error);
        Assert.Equal(777m, second.Free);
    }

    [Fact]
    public async Task Http429_withNoStaleCache_surfacesErrorNotCached()
    {
        // When no prior successful snapshot exists and T212 returns 429,
        // the error is passed through (no stale to fall back to). The
        // error is NOT cached — a subsequent call retries upstream.
        var tooMany = new Trading212CashResult(
            null, null, null, null, null, null, "HTTP 429: too many requests", 429);
        var ok = OkResult(free: 250m);
        var (cache, fake) = Build([tooMany, ok]);

        var first = await cache.GetAsync(CancellationToken.None);
        // 429 with no stale → error surfaced.
        Assert.NotNull(first.Error);

        // Second call: error was NOT cached, so retries upstream → gets OK.
        var second = await cache.GetAsync(CancellationToken.None);
        Assert.Equal(2, fake.CallCount);
        Assert.Null(second.Error);
        Assert.Equal(250m, second.Free);
    }

    [Fact]
    public void CachedAtUtc_isNullBeforeFirstCall()
    {
        var (cache, _) = Build([OkResult()]);
        Assert.Null(cache.CachedAtUtc);
    }

    [Fact]
    public async Task CachedAtUtc_isSetAfterSuccessfulFetch()
    {
        var (cache, _) = Build([OkResult()]);
        var before = DateTime.UtcNow;
        await cache.GetAsync(CancellationToken.None);
        var after = DateTime.UtcNow;
        Assert.NotNull(cache.CachedAtUtc);
        Assert.InRange(cache.CachedAtUtc!.Value, before, after);
    }

    [Fact]
    public void Ttl_respectsConfigValue()
    {
        var (cache, _) = Build([], ttlSeconds: 25);
        Assert.Equal(TimeSpan.FromSeconds(25), cache.Ttl);
    }

    [Fact]
    public void Ttl_clampsToMinimum5s()
    {
        var (cache, _) = Build([], ttlSeconds: 1);
        Assert.Equal(TimeSpan.FromSeconds(5), cache.Ttl);
    }

    // ── stub scope factory ─────────────────────────────────────────────

    /// <summary>
    /// Stub IServiceScopeFactory that returns a scope whose service
    /// provider resolves Trading212Client via a provided delegate.
    /// Keeps the test free of reflection / Moq so it compiles without
    /// extra test packages.
    /// </summary>
    private sealed class StubScopeFactory : IServiceScopeFactory
    {
        private readonly Func<Trading212CashResult> _nextResult;

        public StubScopeFactory(Func<Trading212CashResult> nextResult)
            => _nextResult = nextResult;

        public IServiceScope CreateScope() => new StubScope(_nextResult);

        private sealed class StubScope : IServiceScope
        {
            public IServiceProvider ServiceProvider { get; }
            public StubScope(Func<Trading212CashResult> next)
                => ServiceProvider = new StubProvider(next);
            public void Dispose() { }
        }

        private sealed class StubProvider : IServiceProvider
        {
            private readonly Func<Trading212CashResult> _next;
            public StubProvider(Func<Trading212CashResult> next) => _next = next;

            public object? GetService(Type serviceType)
            {
                if (serviceType == typeof(Trading212Client))
                    return new FakeTrading212Client(_next);
                return null;
            }
        }
    }

    /// <summary>
    /// Minimal stand-in for Trading212Client — only GetCashAsync is used
    /// by the cache; everything else is left unimplemented.
    /// </summary>
    private sealed class FakeTrading212Client : Trading212Client
    {
        private readonly Func<Trading212CashResult> _next;

        public FakeTrading212Client(Func<Trading212CashResult> next)
            : base(
                new System.Net.Http.HttpClient(),
                Microsoft.Extensions.Options.Options.Create(new Trading212Options
                {
                    Mode = "live",
                    ApiKey = "fake-key",
                }),
                NullLogger<Trading212Client>.Instance)
        {
            _next = next;
        }

        // Override GetCashAsync — called by the cache. Trading212Client.GetCashAsync
        // is virtual so the cache (which holds a Trading212Client reference) dispatches
        // here rather than the real HTTP implementation.
        public override Task<Trading212CashResult> GetCashAsync(CancellationToken ct)
            => Task.FromResult(_next());
    }
}

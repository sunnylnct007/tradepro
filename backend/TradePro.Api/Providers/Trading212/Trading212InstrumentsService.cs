using System.Net.Http.Headers;
using System.Net.Http.Json;
using System.Text;
using System.Text.Json;
using Microsoft.Extensions.Options;

namespace TradePro.Api.Providers.Trading212;

/// <summary>
/// Caches the T212 instruments registry. Singleton-scoped — the
/// registry changes infrequently (T212 says 'data refreshed every
/// 10 minutes' but rate-limits the endpoint at 1 req / 50s) so we
/// hold it in memory and write a copy to disk for cold-start.
///
/// Refresh strategy: lazy on access, gated by a 24h TTL. A failed
/// refresh leaves the existing cache in place — callers always get
/// *something* if a previous load succeeded.
/// </summary>
public sealed class Trading212InstrumentsService
{
    private const int TtlHours = 24;

    // The Trading212Client is registered as a typed HttpClient (transient
    // by IHttpClientFactory convention). Capturing it here would freeze
    // the underlying message handler for the life of the singleton; the
    // ASP.NET Core docs flag that as a captive-dependency hazard. So we
    // hold the scope factory and resolve a fresh client per refresh.
    private readonly IServiceScopeFactory _scopeFactory;
    private readonly IOptionsMonitor<Trading212Options> _options;
    private readonly IOptionsMonitor<Trading212DemoOptions> _demoOptions;
    private readonly IHttpClientFactory _httpFactory;
    private readonly ILogger<Trading212InstrumentsService> _log;
    private readonly IConfiguration _config;
    private readonly SemaphoreSlim _refreshLock = new(1, 1);

    private IReadOnlyList<Trading212Instrument> _cache = Array.Empty<Trading212Instrument>();
    private DateTime _loadedAtUtc = DateTime.MinValue;

    public Trading212InstrumentsService(
        IServiceScopeFactory scopeFactory,
        IOptionsMonitor<Trading212Options> options,
        IOptionsMonitor<Trading212DemoOptions> demoOptions,
        IHttpClientFactory httpFactory,
        ILogger<Trading212InstrumentsService> log,
        IConfiguration config)
    {
        _scopeFactory = scopeFactory;
        _options = options;
        _demoOptions = demoOptions;
        _httpFactory = httpFactory;
        _log = log;
        _config = config;
        TryLoadFromDisk();
    }

    // The instruments REGISTRY is read-only reference data — the same US-equity
    // catalog regardless of demo/live. It powers the OMS pre-flight tradeability
    // gate + broker_ticker_map harmonization, so it must be available whenever
    // EITHER T212 credential set is configured. The live section's SM binding is
    // known-flaky in prod (see docker-compose.aws.yaml), while the demo creds
    // load reliably and ARE the account the strategies trade — so we fall back
    // to demo when live is off. Demo is read-only here (catalog only; orders
    // still go through the dedicated Trading212DemoClient).
    public bool IsEnabled => _options.CurrentValue.IsEnabled || _demoOptions.CurrentValue.IsEnabled;
    public int CachedCount => _cache.Count;
    public DateTime LoadedAtUtc => _loadedAtUtc;

    public async Task<IReadOnlyList<Trading212Instrument>> GetAllAsync(CancellationToken ct)
    {
        await EnsureFreshAsync(ct);
        return _cache;
    }

    /// <summary>Case-insensitive substring search across ticker,
    /// shortName and name. Caps the result at <paramref name="limit"/>.
    /// Returns an empty list (never throws) so callers can merge with
    /// other sources cleanly.</summary>
    public async Task<IReadOnlyList<Trading212Instrument>> SearchAsync(
        string query, int limit, CancellationToken ct)
    {
        if (string.IsNullOrWhiteSpace(query)) return Array.Empty<Trading212Instrument>();
        await EnsureFreshAsync(ct);
        if (_cache.Count == 0) return Array.Empty<Trading212Instrument>();

        var q = query.Trim();
        // Hits in priority order: exact ticker prefix > shortName prefix > any
        // substring. Stop early once we have `limit`.
        var seen = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
        var hits = new List<Trading212Instrument>(limit);

        void TryAdd(Trading212Instrument inst)
        {
            if (hits.Count >= limit) return;
            if (!seen.Add(inst.Ticker)) return;
            hits.Add(inst);
        }

        foreach (var i in _cache)
        {
            if (hits.Count >= limit) break;
            if (i.Ticker.StartsWith(q, StringComparison.OrdinalIgnoreCase)) TryAdd(i);
        }
        foreach (var i in _cache)
        {
            if (hits.Count >= limit) break;
            if ((i.ShortName ?? "").StartsWith(q, StringComparison.OrdinalIgnoreCase)) TryAdd(i);
        }
        foreach (var i in _cache)
        {
            if (hits.Count >= limit) break;
            if ((i.Ticker.Contains(q, StringComparison.OrdinalIgnoreCase))
                || (i.ShortName ?? "").Contains(q, StringComparison.OrdinalIgnoreCase)
                || (i.Name ?? "").Contains(q, StringComparison.OrdinalIgnoreCase))
            {
                TryAdd(i);
            }
        }
        return hits;
    }

    private async Task EnsureFreshAsync(CancellationToken ct)
    {
        if (!IsEnabled) return;
        var stale = (DateTime.UtcNow - _loadedAtUtc) > TimeSpan.FromHours(TtlHours);
        if (!stale) return;
        await _refreshLock.WaitAsync(ct);
        try
        {
            // Re-check inside the lock — another caller may have just refreshed.
            if ((DateTime.UtcNow - _loadedAtUtc) <= TimeSpan.FromHours(TtlHours)) return;

            IReadOnlyList<Trading212Instrument> fresh;
            if (_options.CurrentValue.IsEnabled)
            {
                // Live section configured — use the standard typed client.
                using var scope = _scopeFactory.CreateScope();
                var client = scope.ServiceProvider.GetRequiredService<Trading212Client>();
                fresh = await client.GetInstrumentsAsync(ct);
            }
            else
            {
                // Live off but demo configured — fetch the catalog with the
                // demo creds against demo.trading212.com (same registry).
                fresh = await FetchViaDemoAsync(ct);
            }
            if (fresh.Count > 0)
            {
                _cache = fresh;
                _loadedAtUtc = DateTime.UtcNow;
                TrySaveToDisk();
                _log.LogInformation(
                    "Trading212 instruments cache refreshed: {Count} rows", fresh.Count);
            }
            else if (_cache.Count == 0)
            {
                _log.LogWarning(
                    "Trading212 instruments fetch returned 0 rows and no prior cache exists");
            }
        }
        finally
        {
            _refreshLock.Release();
        }
    }

    /// <summary>
    /// Fetch the instruments catalog using the DEMO credentials against
    /// demo.trading212.com. Used when the live section is disabled — the
    /// catalog is the same reference data either way. Mirrors
    /// Trading212Client's auth (Basic when a secret is present, raw key
    /// otherwise) and parse. Returns empty on any failure so the caller
    /// keeps any prior cache and never throws.
    /// </summary>
    private async Task<IReadOnlyList<Trading212Instrument>> FetchViaDemoAsync(CancellationToken ct)
    {
        var demo = _demoOptions.CurrentValue;
        if (!demo.IsEnabled) return Array.Empty<Trading212Instrument>();
        try
        {
            using var http = _httpFactory.CreateClient();
            http.BaseAddress = new Uri(demo.BaseUrl);
            http.Timeout = TimeSpan.FromSeconds(demo.TimeoutSeconds);
            if (!string.IsNullOrWhiteSpace(demo.ApiSecret))
            {
                var token = Convert.ToBase64String(
                    Encoding.UTF8.GetBytes($"{demo.ApiKey}:{demo.ApiSecret}"));
                http.DefaultRequestHeaders.Authorization =
                    new AuthenticationHeaderValue("Basic", token);
            }
            else
            {
                http.DefaultRequestHeaders.TryAddWithoutValidation(
                    "Authorization", demo.ApiKey);
            }
            using var resp = await http.GetAsync("equity/metadata/instruments", ct);
            if (!resp.IsSuccessStatusCode)
            {
                _log.LogWarning(
                    "Trading212 DEMO instruments fetch returned HTTP {Status}",
                    (int)resp.StatusCode);
                return Array.Empty<Trading212Instrument>();
            }
            var items = await resp.Content
                .ReadFromJsonAsync<List<Trading212Instrument>>(cancellationToken: ct);
            return (IReadOnlyList<Trading212Instrument>?)items
                ?? Array.Empty<Trading212Instrument>();
        }
        catch (Exception ex)
        {
            _log.LogWarning(ex, "Trading212 DEMO instruments fetch failed");
            return Array.Empty<Trading212Instrument>();
        }
    }

    private string CachePath()
    {
        var root = _config["Trading212:CachePath"];
        if (string.IsNullOrWhiteSpace(root))
        {
            root = Path.Combine(
                Environment.GetEnvironmentVariable("HOME") ?? Path.GetTempPath(),
                ".tradepro", "server-cache");
        }
        return Path.Combine(root, "t212_instruments.json");
    }

    private void TryLoadFromDisk()
    {
        var path = CachePath();
        if (!File.Exists(path)) return;
        try
        {
            var json = File.ReadAllText(path);
            var snap = JsonSerializer.Deserialize<DiskSnapshot>(json);
            if (snap?.Instruments is { Count: > 0 } items)
            {
                _cache = items;
                _loadedAtUtc = snap.LoadedAtUtc;
                _log.LogInformation(
                    "Trading212 instruments cache loaded from disk: {Count} rows from {When:o}",
                    items.Count, snap.LoadedAtUtc);
            }
        }
        catch (Exception ex)
        {
            _log.LogWarning(ex, "Trading212 instruments disk load failed; will refetch");
        }
    }

    private void TrySaveToDisk()
    {
        var path = CachePath();
        try
        {
            Directory.CreateDirectory(Path.GetDirectoryName(path)!);
            var json = JsonSerializer.Serialize(new DiskSnapshot
            {
                LoadedAtUtc = _loadedAtUtc,
                Instruments = _cache.ToList(),
            });
            File.WriteAllText(path, json);
        }
        catch (Exception ex)
        {
            _log.LogWarning(ex, "Trading212 instruments disk save failed");
        }
    }

    private sealed class DiskSnapshot
    {
        public DateTime LoadedAtUtc { get; set; }
        public List<Trading212Instrument> Instruments { get; set; } = [];
    }
}

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
    private readonly ILogger<Trading212InstrumentsService> _log;
    private readonly IConfiguration _config;
    private readonly SemaphoreSlim _refreshLock = new(1, 1);

    private IReadOnlyList<Trading212Instrument> _cache = Array.Empty<Trading212Instrument>();
    private DateTime _loadedAtUtc = DateTime.MinValue;

    public Trading212InstrumentsService(
        IServiceScopeFactory scopeFactory,
        IOptionsMonitor<Trading212Options> options,
        ILogger<Trading212InstrumentsService> log,
        IConfiguration config)
    {
        _scopeFactory = scopeFactory;
        _options = options;
        _log = log;
        _config = config;
        TryLoadFromDisk();
    }

    public bool IsEnabled => _options.CurrentValue.IsEnabled;
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
            using var scope = _scopeFactory.CreateScope();
            var client = scope.ServiceProvider.GetRequiredService<Trading212Client>();
            var fresh = await client.GetInstrumentsAsync(ct);
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

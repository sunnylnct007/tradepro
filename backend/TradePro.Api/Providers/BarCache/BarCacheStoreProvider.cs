using Microsoft.Extensions.Options;
using Parquet;
using Parquet.Schema;
using TradePro.Api.Models;

namespace TradePro.Api.Providers.BarCache;

/// <summary>
/// The ONE read path for bars in the API: candles served from the golden bar
/// store (docs/DATA_ARCHITECTURE.md). Registered as <c>bar_cache</c> in the
/// market-data registry; the Postgres bar table and the live-Yahoo default it
/// replaces are on the retire list.
///
/// Storage contract (written by the Python <c>BarStore</c>, never by this code):
/// <c>{assetClass}/{SYMBOL}/{resolution}/{yyyy-MM}.parquet</c>, pandas-written,
/// columns <c>open high low close volume adj_factor source timestamp</c>, the
/// timestamp a UTC instant that is the bar's OPEN. Stored resolutions are
/// <c>1m</c>, <c>5m</c>, <c>1d</c>. 15m/30m/1h are DERIVED here from 5m (a 15m
/// bar IS three 5m bars) and never stored — one source of truth.
///
/// Provenance travels with the data: each row's <c>source</c> is counted into
/// <see cref="CandleSeries.Sources"/> and the newest bar's timestamp into
/// <see cref="CandleSeries.NewestBar"/>, so a screen can show "complete, newest
/// 19:35Z, 57 ibkr_web / 59 yfinance" instead of a bare grade. The 5m lane
/// said "complete" three times on 11 Nov 2026 while holding no bar for the day;
/// a timestamp beside the word is the fix.
/// </summary>
public sealed class BarCacheStoreProvider : IMarketDataProvider
{
    public const string ProviderName = "bar_cache";
    public string Name => ProviderName;

    private readonly IBarCacheBlobs _blobs;
    private readonly BarCacheOptions _opts;
    private readonly ILogger<BarCacheStoreProvider> _log;

    public BarCacheStoreProvider(IBarCacheBlobs blobs, IOptions<BarCacheOptions> opts, ILogger<BarCacheStoreProvider> log)
    {
        _blobs = blobs;
        _opts = opts.Value;
        _log = log;
    }

    public async Task<CandleSeries> GetCandlesAsync(
        string symbol, string interval, DateTime from, DateTime to, CancellationToken ct)
    {
        var sym = symbol.Trim().ToUpperInvariant();
        var (stored, groupMinutes) = ResolveStorage(interval);
        var fromUtc = AsUtc(from);
        var toUtc = AsUtc(to);
        if (toUtc <= fromUtc)
            return Empty(sym, interval);

        // Daily partitions are keyed by the bar's date; intraday by the bar's
        // open instant. Both are "timestamp < to" half-open, like the store.
        var rows = new List<Row>();
        string? note = null;
        foreach (var month in MonthsCovering(fromUtc, toUtc))
        {
            var rel = $"{_opts.DefaultAssetClass}/{sym}/{stored}/{month:yyyy-MM}.parquet";
            byte[]? bytes;
            try
            {
                bytes = await _blobs.ReadAsync(rel, ct);
            }
            catch (Exception ex) when (ex is not OperationCanceledException)
            {
                // The store could not be READ (denied, unreachable). That is an
                // operations fault, not a data fault: say so on the series and
                // stop asking — every further month will fail the same way. Never
                // a 500 (the chart dies) and never a quiet hop to a live feed.
                // 12 Nov 2026: the first deploy of the switch answered 500
                // "not authorized to perform s3:GetObject" because the role policy
                // had only been PLANNED, not applied.
                note = $"bar store at {_blobs.Location} could not be read: {ex.GetType().Name}: {Truncate(ex.Message, 240)}";
                _log.LogError(ex, "bar_cache: cannot read {Path} from {Location}", rel, _blobs.Location);
                break;
            }
            if (bytes is null) continue;
            try
            {
                rows.AddRange(await ReadPartitionAsync(bytes, ct));
            }
            catch (Exception ex)
            {
                // A corrupt partition is a data fault, not a reason to answer
                // from a different store. Log it, skip it, and let the hole show.
                _log.LogError(ex, "bar_cache: partition {Path} unreadable — skipped", rel);
                note = (note is null ? "" : note + "; ") + $"partition {month:yyyy-MM} unreadable: {ex.GetType().Name}";
            }
        }

        var inWindow = rows
            .Where(r => r.Ts >= fromUtc && r.Ts < toUtc)
            .OrderBy(r => r.Ts)
            .ToList();
        // The store de-duplicates on write; a merged read across months cannot
        // overlap, but a defensive distinct on timestamp costs nothing.
        inWindow = inWindow.GroupBy(r => r.Ts).Select(g => g.Last()).OrderBy(r => r.Ts).ToList();

        if (groupMinutes > 0)
            inWindow = Aggregate(inWindow, groupMinutes);

        var candles = inWindow
            .Select(r => new Candle(r.Ts, (decimal)r.Open, (decimal)r.High, (decimal)r.Low, (decimal)r.Close,
                                    AdjustedClose: r.AdjFactor is > 0 && Math.Abs(r.AdjFactor - 1.0) > 1e-12
                                        ? (decimal)(r.Close * r.AdjFactor) : null,
                                    r.Volume))
            .ToList();

        var sources = inWindow
            .GroupBy(r => r.Source ?? "unknown")
            .ToDictionary(g => g.Key, g => g.Count());

        return new CandleSeries(sym, interval, Name, candles,
            Sources: sources,
            NewestBar: candles.Count == 0 ? null : candles[^1].Timestamp,
            Location: _blobs.Location,
            Note: note);
    }

    private static string Truncate(string s, int n) => s.Length <= n ? s : s[..n] + "…";

    private CandleSeries Empty(string sym, string interval) =>
        new(sym, interval, Name, Array.Empty<Candle>(), Sources: new Dictionary<string, int>(), NewestBar: null, Location: _blobs.Location);

    /// <summary>Which stored resolution serves an interval, and how many stored bars make one requested bar (0 = none).</summary>
    internal static (string Stored, int GroupMinutes) ResolveStorage(string interval) =>
        interval.Trim().ToLowerInvariant() switch
        {
            "1m" => ("1m", 0),
            "5m" => ("5m", 0),
            "15m" => ("5m", 15),
            "30m" => ("5m", 30),
            "1h" or "60m" => ("5m", 60),
            "1d" or "d" or "day" or "daily" => ("1d", 0),
            var other => throw new ArgumentException(
                $"interval '{other}' is not served by the bar store (1m, 5m, 15m, 30m, 1h, 1d)"),
        };

    internal static IEnumerable<DateTime> MonthsCovering(DateTime fromUtc, DateTime toUtc)
    {
        var m = new DateTime(fromUtc.Year, fromUtc.Month, 1, 0, 0, 0, DateTimeKind.Utc);
        // `to` is exclusive: a window ending exactly at a month boundary does
        // not need that month.
        var lastInstant = toUtc.AddTicks(-1);
        var end = new DateTime(lastInstant.Year, lastInstant.Month, 1, 0, 0, 0, DateTimeKind.Utc);
        for (; m <= end; m = m.AddMonths(1)) yield return m;
    }

    private static DateTime AsUtc(DateTime d) => d.Kind switch
    {
        DateTimeKind.Utc => d,
        DateTimeKind.Local => d.ToUniversalTime(),
        _ => DateTime.SpecifyKind(d, DateTimeKind.Utc),   // the endpoint parses dates as unspecified; treat as UTC
    };

    /// <summary>Group intraday rows into fixed buckets aligned to the UTC day. Sessions never straddle a UTC day for US bars.</summary>
    internal static List<Row> Aggregate(List<Row> rows, int minutes)
    {
        var size = TimeSpan.FromMinutes(minutes).Ticks;
        return rows
            .GroupBy(r => new DateTime(r.Ts.Ticks - (r.Ts.Ticks % size), DateTimeKind.Utc))
            .OrderBy(g => g.Key)
            .Select(g =>
            {
                var ordered = g.OrderBy(r => r.Ts).ToList();
                return new Row(
                    g.Key,
                    ordered[0].Open,
                    ordered.Max(r => r.High),
                    ordered.Min(r => r.Low),
                    ordered[^1].Close,
                    ordered.Sum(r => r.Volume),
                    ordered[^1].AdjFactor,
                    // A derived bar's provenance is the WORST of its parts: if any
                    // 5m bar came from a fallback, the 15m bar is a fallback bar.
                    ordered.Select(r => r.Source).Distinct().Count() == 1
                        ? ordered[0].Source
                        : string.Join("+", ordered.Select(r => r.Source ?? "unknown").Distinct().OrderBy(s => s)));
            })
            .ToList();
    }

    internal sealed record Row(DateTime Ts, double Open, double High, double Low, double Close, long Volume, double AdjFactor, string? Source);

    internal static async Task<List<Row>> ReadPartitionAsync(byte[] bytes, CancellationToken ct)
    {
        using var ms = new MemoryStream(bytes, writable: false);
        using var reader = await ParquetReader.CreateAsync(ms, cancellationToken: ct);
        var fields = reader.Schema.GetDataFields().ToDictionary(f => f.Name, StringComparer.OrdinalIgnoreCase);
        DataField F(string name) => fields.TryGetValue(name, out var f) ? f
            : throw new InvalidDataException($"bar partition lacks column '{name}' (has: {string.Join(",", fields.Keys)})");

        var rows = new List<Row>();
        for (var g = 0; g < reader.RowGroupCount; g++)
        {
            using var rg = reader.OpenRowGroupReader(g);
            var ts = (await rg.ReadColumnAsync(F("timestamp"), ct)).Data;
            var open = (await rg.ReadColumnAsync(F("open"), ct)).Data;
            var high = (await rg.ReadColumnAsync(F("high"), ct)).Data;
            var low = (await rg.ReadColumnAsync(F("low"), ct)).Data;
            var close = (await rg.ReadColumnAsync(F("close"), ct)).Data;
            var vol = (await rg.ReadColumnAsync(F("volume"), ct)).Data;
            var adj = fields.ContainsKey("adj_factor") ? (await rg.ReadColumnAsync(F("adj_factor"), ct)).Data : null;
            var src = fields.ContainsKey("source") ? (await rg.ReadColumnAsync(F("source"), ct)).Data : null;

            for (var i = 0; i < ts.Length; i++)
            {
                var t = ToUtc(ts.GetValue(i));
                if (t is null) continue;
                rows.Add(new Row(
                    t.Value,
                    ToDouble(open.GetValue(i)), ToDouble(high.GetValue(i)), ToDouble(low.GetValue(i)), ToDouble(close.GetValue(i)),
                    ToLong(vol.GetValue(i)),
                    adj is null ? 1.0 : ToDouble(adj.GetValue(i), 1.0),
                    src?.GetValue(i)?.ToString()));
            }
        }
        return rows;
    }

    // pandas writes timestamp[ms, UTC]; Parquet.Net surfaces that as DateTime
    // (Kind Utc or Unspecified), DateTimeOffset on some versions, or a raw
    // epoch when the logical type is lost. Accept all three; refuse to guess
    // beyond that.
    private static DateTime? ToUtc(object? v) => v switch
    {
        null => null,
        DateTime dt => dt.Kind == DateTimeKind.Utc ? dt : DateTime.SpecifyKind(dt.ToUniversalTime(), DateTimeKind.Utc),
        DateTimeOffset dto => dto.UtcDateTime,
        long epochMs => DateTimeOffset.FromUnixTimeMilliseconds(epochMs).UtcDateTime,
        _ => throw new InvalidDataException($"unexpected timestamp type {v.GetType().Name}"),
    };

    private static double ToDouble(object? v, double @default = double.NaN) => v switch
    {
        null => @default,
        double d => d,
        float f => f,
        decimal m => (double)m,
        long l => l,
        int i => i,
        _ => Convert.ToDouble(v),
    };

    private static long ToLong(object? v) => v switch
    {
        null => 0,
        long l => l,
        int i => i,
        double d => double.IsNaN(d) ? 0 : (long)d,
        float f => (long)f,
        decimal m => (long)m,
        _ => Convert.ToInt64(v),
    };
}

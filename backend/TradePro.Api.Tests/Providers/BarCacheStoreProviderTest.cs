using Microsoft.Extensions.Logging.Abstractions;
using Microsoft.Extensions.Options;
using Parquet;
using Parquet.Data;
using Parquet.Schema;
using TradePro.Api.Providers.BarCache;
using Xunit;

namespace TradePro.Api.Tests.Providers;

/// <summary>
/// The API's one read path for bars (docs/DATA_ARCHITECTURE.md), driven over a
/// real parquet tree written in the harvester's layout. No network, no
/// Postgres: a temp directory stands in for the S3 mirror.
/// </summary>
public sealed class BarCacheStoreProviderTest : IDisposable
{
    private static readonly DateTime Utc = new(2026, 11, 1, 0, 0, 0, DateTimeKind.Utc);
    private readonly string _root = Path.Combine(Path.GetTempPath(), "tp-barcache-" + Guid.NewGuid().ToString("N"));

    public void Dispose() { try { Directory.Delete(_root, recursive: true); } catch { /* best effort */ } }

    private BarCacheStoreProvider Provider(int currentTtl = 60) =>
        new(new CachingBarCacheBlobs(new LocalDirectoryBarCacheBlobs(_root),
                Options.Create(new BarCacheOptions { LocalDir = _root, CurrentMonthTtlSeconds = currentTtl })),
            Options.Create(new BarCacheOptions { LocalDir = _root }),
            NullLogger<BarCacheStoreProvider>.Instance);

    /// <summary>Write one partition in the store's layout and column set.</summary>
    private async Task WritePartition(string symbol, string resolution, DateTime month, IReadOnlyList<(DateTime ts, double close, string source)> bars)
    {
        var dir = Path.Combine(_root, "us_etf", symbol, resolution);
        Directory.CreateDirectory(dir);
        var path = Path.Combine(dir, $"{month:yyyy-MM}.parquet");

        var fTs = new DateTimeDataField("timestamp", DateTimeFormat.DateAndTime, isAdjustedToUTC: true);
        var fO = new DataField<double>("open");
        var fH = new DataField<double>("high");
        var fL = new DataField<double>("low");
        var fC = new DataField<double>("close");
        var fV = new DataField<long>("volume");
        var fA = new DataField<double>("adj_factor");
        var fS = new DataField<string>("source");
        var schema = new ParquetSchema(fO, fH, fL, fC, fV, fA, fS, fTs);

        using var fs = File.Create(path);
        using var w = await ParquetWriter.CreateAsync(schema, fs);
        using var rg = w.CreateRowGroup();
        var n = bars.Count;
        await rg.WriteColumnAsync(new DataColumn(fO, bars.Select(b => b.close - 0.5).ToArray()));
        await rg.WriteColumnAsync(new DataColumn(fH, bars.Select(b => b.close + 1.0).ToArray()));
        await rg.WriteColumnAsync(new DataColumn(fL, bars.Select(b => b.close - 1.0).ToArray()));
        await rg.WriteColumnAsync(new DataColumn(fC, bars.Select(b => b.close).ToArray()));
        await rg.WriteColumnAsync(new DataColumn(fV, Enumerable.Repeat(1000L, n).ToArray()));
        await rg.WriteColumnAsync(new DataColumn(fA, Enumerable.Repeat(1.0, n).ToArray()));
        await rg.WriteColumnAsync(new DataColumn(fS, bars.Select(b => b.source).ToArray()));
        await rg.WriteColumnAsync(new DataColumn(fTs, bars.Select(b => b.ts).ToArray()));
    }

    private static IReadOnlyList<(DateTime, double, string)> Session5m(DateTime day, int bars, string source, double start = 100)
        => Enumerable.Range(0, bars)
            .Select(k => (day.AddHours(14).AddMinutes(30 + 5 * k), start + k, source))
            .ToList();

    [Fact]
    public async Task Serves_5m_bars_in_a_half_open_window_with_provenance()
    {
        var day = Utc.AddDays(10);                                        // Wed 11 Nov 2026
        await WritePartition("SPY", "5m", Utc, Session5m(day, 62, "ibkr_web"));

        var s = await Provider().GetCandlesAsync("spy", "5m", day, day.AddDays(1), CancellationToken.None);

        Assert.Equal("bar_cache", s.Provider);
        Assert.Equal("SPY", s.Symbol);
        Assert.Equal(62, s.Candles.Count);
        Assert.Equal(day.AddHours(14).AddMinutes(30), s.Candles[0].Timestamp);
        Assert.Equal(day.AddHours(19).AddMinutes(35), s.Candles[^1].Timestamp);
        Assert.Equal(DateTimeKind.Utc, s.Candles[0].Timestamp.Kind);
        Assert.Equal(s.Candles[^1].Timestamp, s.NewestBar);
        Assert.Equal(62, s.Sources!["ibkr_web"]);
        Assert.Equal(_root, s.Location);
    }

    [Fact]
    public async Task A_window_across_a_month_boundary_reads_both_partitions()
    {
        var oct = new DateTime(2026, 10, 1, 0, 0, 0, DateTimeKind.Utc);
        await WritePartition("SPY", "1d", oct, new[] { (oct.AddDays(29), 90.0, "ibkr_web"), (oct.AddDays(30), 91.0, "ibkr_web") });
        await WritePartition("SPY", "1d", Utc, new[] { (Utc.AddDays(1), 92.0, "ibkr_web"), (Utc.AddDays(2), 93.0, "yfinance") });

        var s = await Provider().GetCandlesAsync("SPY", "1d", oct.AddDays(29), Utc.AddDays(3), CancellationToken.None);

        Assert.Equal(new[] { 90.0m, 91.0m, 92.0m, 93.0m }, s.Candles.Select(c => c.Close).ToArray());
        Assert.Equal(3, s.Sources!["ibkr_web"]);
        Assert.Equal(1, s.Sources["yfinance"]);
    }

    [Fact]
    public async Task The_end_of_the_window_is_exclusive_like_the_store()
    {
        var day = Utc.AddDays(10);
        await WritePartition("SPY", "5m", Utc, Session5m(day, 4, "ibkr_web"));
        var cut = day.AddHours(14).AddMinutes(40);                        // third bar opens here

        var s = await Provider().GetCandlesAsync("SPY", "5m", day, cut, CancellationToken.None);

        Assert.Equal(2, s.Candles.Count);
        Assert.All(s.Candles, c => Assert.True(c.Timestamp < cut));
    }

    [Fact]
    public async Task Fifteen_minute_bars_are_derived_from_5m_and_carry_the_worst_provenance()
    {
        var day = Utc.AddDays(10);
        var bars = Session5m(day, 6, "ibkr_web").ToList();
        bars[4] = (bars[4].Item1, bars[4].Item2, "yfinance");             // one fallback bar in the second bucket
        await WritePartition("SPY", "5m", Utc, bars);

        var s = await Provider().GetCandlesAsync("SPY", "15m", day, day.AddDays(1), CancellationToken.None);

        Assert.Equal(2, s.Candles.Count);
        var first = s.Candles[0];
        Assert.Equal(day.AddHours(14).AddMinutes(30), first.Timestamp);
        Assert.Equal(99.5m, first.Open);                                  // open of bar 0 (close 100 - 0.5)
        Assert.Equal(102.0m, first.Close);                                // close of bar 2
        Assert.Equal(103.0m, first.High);                                 // max high = 102 + 1
        Assert.Equal(3000, first.Volume);
        Assert.Equal(1, s.Sources!["ibkr_web"]);
        Assert.Equal(1, s.Sources["ibkr_web+yfinance"]);
    }

    [Fact]
    public async Task A_symbol_with_no_partition_is_an_empty_labelled_series_not_an_error()
    {
        var s = await Provider().GetCandlesAsync("NOPE", "1d", Utc, Utc.AddDays(5), CancellationToken.None);
        Assert.Empty(s.Candles);
        Assert.Equal("bar_cache", s.Provider);
        Assert.Null(s.NewestBar);
    }

    [Fact]
    public async Task An_interval_the_store_does_not_serve_is_refused_not_guessed()
    {
        await Assert.ThrowsAsync<ArgumentException>(() =>
            Provider().GetCandlesAsync("SPY", "2h", Utc, Utc.AddDays(1), CancellationToken.None));
    }

    [Fact]
    public void Months_covering_a_window_treat_the_end_as_exclusive()
    {
        var months = BarCacheStoreProvider.MonthsCovering(
            new DateTime(2026, 10, 15, 0, 0, 0, DateTimeKind.Utc),
            new DateTime(2026, 12, 1, 0, 0, 0, DateTimeKind.Utc)).ToArray();
        Assert.Equal(new[] { "2026-10", "2026-11" }, months.Select(m => m.ToString("yyyy-MM")).ToArray());
    }

    [Fact]
    public async Task Unconfigured_store_answers_empty_and_says_so()
    {
        var p = new BarCacheStoreProvider(new UnconfiguredBarCacheBlobs(),
            Options.Create(new BarCacheOptions()), NullLogger<BarCacheStoreProvider>.Instance);
        var s = await p.GetCandlesAsync("SPY", "1d", Utc, Utc.AddDays(5), CancellationToken.None);
        Assert.Empty(s.Candles);
        Assert.Contains("UNCONFIGURED", s.Location);
    }

    private sealed class DeniedBlobs : IBarCacheBlobs
    {
        public string Location => "s3://bucket/bar_cache";
        public Task<byte[]?> ReadAsync(string relativePath, CancellationToken ct)
            => throw new Amazon.S3.AmazonS3Exception("User is not authorized to perform: s3:GetObject on resource");
    }

    [Fact]
    public async Task A_denied_store_read_is_an_empty_series_with_the_reason_not_a_500()
    {
        var p = new BarCacheStoreProvider(new DeniedBlobs(),
            Options.Create(new BarCacheOptions()), NullLogger<BarCacheStoreProvider>.Instance);

        var s = await p.GetCandlesAsync("SPY", "5m", Utc, Utc.AddMonths(2), CancellationToken.None);

        Assert.Empty(s.Candles);
        Assert.Equal("bar_cache", s.Provider);                       // never silently another provider
        Assert.Equal("s3://bucket/bar_cache", s.Location);
        Assert.Contains("could not be read", s.Note);
        Assert.Contains("AmazonS3Exception", s.Note);
        Assert.Contains("not authorized", s.Note);
    }

    /// <summary>
    /// The harvester's own output, when this machine has it. Proves the pandas
    /// timestamp[ms, UTC] encoding reads back as UTC instants — the one thing a
    /// self-written fixture cannot prove. Skipped where the store is absent (CI).
    /// </summary>
    [Fact]
    public async Task Reads_a_partition_the_python_harvester_wrote()
    {
        var home = Environment.GetFolderPath(Environment.SpecialFolder.UserProfile);
        var real = Path.Combine(home, ".tradepro", "bar_cache", "us_etf", "SPY", "5m");
        if (!Directory.Exists(real)) return;                               // no local store here
        var newest = Directory.GetFiles(real, "*.parquet").OrderBy(f => f).LastOrDefault();
        if (newest is null) return;

        var rows = await BarCacheStoreProvider.ReadPartitionAsync(await File.ReadAllBytesAsync(newest), CancellationToken.None);

        Assert.NotEmpty(rows);
        Assert.All(rows, r => Assert.Equal(DateTimeKind.Utc, r.Ts.Kind));
        // US regular session opens 13:30Z (EDT) or 14:30Z (EST); every bar's
        // open sits inside [13:30, 21:00) UTC. A timezone slip would move this.
        Assert.All(rows, r => Assert.InRange(r.Ts.TimeOfDay, TimeSpan.FromHours(13.5), TimeSpan.FromHours(21)));
        Assert.All(rows, r => Assert.False(string.IsNullOrEmpty(r.Source)));
    }
}

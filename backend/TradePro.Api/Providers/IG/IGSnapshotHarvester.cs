using Dapper;
using Microsoft.Extensions.Hosting;
using Microsoft.Extensions.Logging;
using Microsoft.Extensions.Options;
using Npgsql;

namespace TradePro.Api.Providers.IG;

/// <summary>
/// Phase P2 — IG L1 snapshot harvester. Continuously polls
/// /markets/{epic} for a configured universe and writes one row per
/// epic per poll to ig_l1_snapshots. This is the data-lake
/// foundation: the lake the trader OWNS so backtests + replay don't
/// depend on yfinance bars that can revise / disappear.
///
/// Design points:
///
///   * **Off by default.** IsEnabled=false means a deploy doesn't
///     start hammering /markets/{epic}. Operator flips
///     IG:Harvester:Enabled=true via config / cockpit when ready.
///
///   * **Singleton-session-correct.** Uses the same IGClient (which
///     uses the shared IGSessionCache) so 50 epics polled every 5
///     min add ZERO extra session traffic. The 403 anti-abuse block
///     IG triggered on early IGClient versions doesn't re-emerge.
///
///   * **Sequential within a tick.** A 50-epic universe at 5-min
///     cadence has 6s per epic — easily within the IGClient's auth
///     overhead budget. Parallelism would risk IG's per-account rate
///     limit; we trade throughput for safety.
///
///   * **Failures are rows.** When a poll returns HTTP-ok but bid/ask
///     are null (market closed, instrument halted) we STILL insert
///     a row with `error="market_status: OFFLINE"` so the time series
///     has no gaps. Network / auth errors are logged but skip the
///     insert (they tell us nothing about the market).
///
///   * **Configurable universe.** IG:Harvester:Epics is a comma-
///     separated list. Empty = scan the ig_epic_map.json file. The
///     production list lives in config so a deploy can change it
///     without recompiling.
/// </summary>
public sealed class IGSnapshotHarvester : BackgroundService
{
    private readonly IServiceScopeFactory _scopes;
    private readonly IGHarvesterOptions _options;
    private readonly ILogger<IGSnapshotHarvester> _log;
    private readonly TimeSpan _interval;
    private readonly TimeSpan _perEpicDelay;
    private readonly bool _disabled;

    public IGSnapshotHarvester(
        IServiceScopeFactory scopes,
        IOptions<IGHarvesterOptions> options,
        ILogger<IGSnapshotHarvester> log)
    {
        _scopes = scopes;
        _options = options.Value;
        _log = log;
        // Clamp so a misconfig can't either spam IG (too low) or
        // produce an effectively-dead harvester (too high).
        var sec = Math.Clamp(_options.IntervalSeconds, 60, 3600);
        _interval = TimeSpan.FromSeconds(sec);
        _perEpicDelay = TimeSpan.FromMilliseconds(
            Math.Clamp(_options.PerEpicDelayMs, 0, 10_000));
        _disabled = !_options.Enabled;
    }

    protected override async Task ExecuteAsync(CancellationToken ct)
    {
        if (_disabled)
        {
            _log.LogInformation(
                "IGSnapshotHarvester is DISABLED via config (IG:Harvester:Enabled=false). "
                + "Flip the flag + restart the API to start capturing.");
            return;
        }
        _log.LogInformation(
            "IGSnapshotHarvester started: tick={Tick}s perEpicDelay={Delay}ms epics={Epics}",
            _interval.TotalSeconds,
            _perEpicDelay.TotalMilliseconds,
            _options.Epics is { Count: > 0 } eps
                ? string.Join(",", eps)
                : "(none)");

        // Settle delay so the API has time to warm + IG to auth.
        try { await Task.Delay(TimeSpan.FromSeconds(15), ct); }
        catch (OperationCanceledException) { return; }

        while (!ct.IsCancellationRequested)
        {
            try { await OneTickAsync(ct); }
            catch (OperationCanceledException) { break; }
            catch (Exception ex)
            {
                _log.LogError(ex, "IGSnapshotHarvester tick threw — continuing");
            }
            try { await Task.Delay(_interval, ct); }
            catch (OperationCanceledException) { break; }
        }
    }

    /// <summary>One full sweep across the configured epic list.
    /// Sequential by design (see class-doc).</summary>
    private async Task OneTickAsync(CancellationToken ct)
    {
        if (_options.Epics is null || _options.Epics.Count == 0)
        {
            _log.LogDebug("IGSnapshotHarvester: empty epic list — nothing to do.");
            return;
        }

        using var scope = _scopes.CreateScope();
        var sp = scope.ServiceProvider;
        var db = sp.GetRequiredService<NpgsqlDataSource>();
        var ig = sp.GetService<IGClient>();
        if (ig is null || !ig.IsEnabled)
        {
            _log.LogDebug(
                "IGSnapshotHarvester: IGClient unavailable / disabled — skipping tick.");
            return;
        }

        int captured = 0;
        int failed = 0;
        await using var conn = await db.OpenConnectionAsync(ct);

        foreach (var entry in _options.Epics)
        {
            if (ct.IsCancellationRequested) return;
            // Each entry is "symbol:epic" — symbol = canonical
            // identity we use everywhere (matches oms_fills.symbol);
            // epic = IG's instrument code.
            var (symbol, epic) = ParseEntry(entry);
            if (symbol is null || epic is null)
            {
                _log.LogWarning(
                    "IGSnapshotHarvester: malformed epic entry {Entry} — expected 'SYMBOL:EPIC'", entry);
                continue;
            }

            try
            {
                var snap = await ig.GetMarketSnapshotAsync(epic, ct);
                if (snap.Bid is null && snap.Ask is null
                    && string.IsNullOrEmpty(snap.Error))
                {
                    // IG returned 2xx but no quote — likely market
                    // closed. Persist with a status so the time series
                    // has no false gap.
                    await InsertAsync(conn, symbol, epic,
                        bid: null, ask: null,
                        marketStatus: snap.MarketStatus, updateTime: snap.UpdateTime,
                        error: string.IsNullOrEmpty(snap.MarketStatus)
                            ? "no_quote_in_response"
                            : $"market_status:{snap.MarketStatus}");
                    failed++;
                }
                else if (snap.Bid is not null && snap.Ask is not null)
                {
                    await InsertAsync(conn, symbol, epic,
                        bid: snap.Bid.Value, ask: snap.Ask.Value,
                        marketStatus: snap.MarketStatus, updateTime: snap.UpdateTime,
                        error: null);
                    captured++;
                }
                else
                {
                    // Partial response — record what we have.
                    await InsertAsync(conn, symbol, epic,
                        bid: snap.Bid, ask: snap.Ask,
                        marketStatus: snap.MarketStatus, updateTime: snap.UpdateTime,
                        error: snap.Error ?? "partial_quote");
                    failed++;
                }
            }
            catch (Exception ex)
            {
                // Don't insert on network/auth errors — those tell us
                // nothing about the market itself.
                _log.LogWarning(ex,
                    "IGSnapshotHarvester: snapshot fetch threw for {Symbol}/{Epic}",
                    symbol, epic);
                failed++;
            }

            try { await Task.Delay(_perEpicDelay, ct); }
            catch (OperationCanceledException) { return; }
        }

        _log.LogInformation(
            "IGSnapshotHarvester tick complete: captured={Captured} failed={Failed} of {Total}",
            captured, failed, _options.Epics.Count);
    }

    /// <summary>Parse a "SYMBOL:EPIC" entry. Tolerant of whitespace
    /// around either side. Returns (null, null) on malformed input
    /// so the caller can warn + skip.</summary>
    public static (string? Symbol, string? Epic) ParseEntry(string entry)
    {
        if (string.IsNullOrWhiteSpace(entry)) return (null, null);
        var parts = entry.Split(':', 2);
        if (parts.Length != 2) return (null, null);
        var sym = parts[0].Trim();
        var epic = parts[1].Trim();
        if (sym.Length == 0 || epic.Length == 0) return (null, null);
        return (sym, epic);
    }

    private static async Task InsertAsync(
        NpgsqlConnection conn,
        string symbol, string epic,
        decimal? bid, decimal? ask,
        string? marketStatus, string? updateTime,
        string? error)
    {
        await conn.ExecuteAsync(@"
            INSERT INTO ig_l1_snapshots
              (symbol, epic, bid, ask, market_status, update_time, source, error)
            VALUES
              (@symbol, @epic, @bid, @ask, @marketStatus, @updateTime, 'ig_markets', @error);",
            new { symbol, epic, bid, ask, marketStatus, updateTime, error });
    }
}

/// <summary>Bound from the IG:Harvester config section. Off by
/// default — flip Enabled=true after the universe + cadence are
/// reviewed.</summary>
public sealed class IGHarvesterOptions
{
    public const string SectionName = "IG:Harvester";

    /// <summary>Master switch. False = the BackgroundService logs
    /// "disabled" + returns. Cheap to ship; safe to leave deployed.</summary>
    public bool Enabled { get; set; } = false;

    /// <summary>Seconds between sweeps. Clamped to [60, 3600].
    /// Default 300 = 5 min, friendly to IG's rate limits at any
    /// sane universe size.</summary>
    public int IntervalSeconds { get; set; } = 300;

    /// <summary>Milliseconds to wait between epic polls within a
    /// sweep. Spreads load against IG's per-account rate cap.
    /// 250 ms = 4 epics/sec = 240 epics/min — well within limits.</summary>
    public int PerEpicDelayMs { get; set; } = 250;

    /// <summary>List of "SYMBOL:EPIC" strings to poll each sweep.
    /// Bind from config as `IG:Harvester:Epics:0`, `:1`, etc.
    /// Empty list = harvester idles (logged at tick).</summary>
    public List<string>? Epics { get; set; }
}

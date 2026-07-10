using Dapper;
using Microsoft.Extensions.Options;
using Npgsql;

namespace TradePro.Api.Providers.IBKR;

/// <summary>
/// IBKRDailyBackfillService — the DEEP DAILY history filler for the ONE central
/// store (<c>ibkr_price_bars</c>, resolution='1d'). Separate from IBKRBarHarvester
/// (which keeps a shallow 1m intraday window warm): daily history is what the
/// ICH-Equity SWING strategy + backtests need, and IBKR serves YEARS of it.
///
/// Design:
///   * **Deep pass, once per symbol.** Any configured symbol whose 1d history
///     doesn't reach back far enough gets one deep pull (BackfillPeriod, e.g. 15y),
///     UPSERTed into ibkr_price_bars. Runs straight through the universe, PACED
///     (PerSymbolDelayMs) so it never starves live order placement on the shared
///     IBKR session (see project_ibkr_harvest_session_isolation).
///   * **Runs 24/7 until caught up, then tops up.** After the deep pass completes,
///     a light top-up loop refreshes the recent daily window on an interval so the
///     latest completed daily bar always lands — the store stays current with no
///     manual re-run.
///   * **IBKR primary, Yahoo fallback (loud).** Same provenance model as the 1m
///     harvester: source='ibkr' (GOOD) or 'yahoo' (BRONZE) per bar, never a
///     silent gap. Idempotent UPSERT: a later IBKR fetch overwrites a Yahoo bar.
///   * **Observable.** IBKRDailyBackfillStatus surfaces progress (deep-done count,
///     in-progress symbol, bars written, last error) for the Data Health board.
/// </summary>
public sealed class IBKRDailyBackfillService : BackgroundService
{
    private const string Resolution = "1d";

    private readonly IServiceProvider _sp;
    private readonly NpgsqlDataSource _db;
    private readonly IBKRDailyBackfillOptions _options;
    private readonly IReadOnlyList<string> _symbols;
    private readonly IBKRDailyBackfillStatus _status;
    private readonly ILogger<IBKRDailyBackfillService> _log;
    private readonly TimeSpan _perSymbolDelay;
    private readonly TimeSpan _topUpInterval;
    private readonly bool _disabled;

    // Symbols whose deep history has been confirmed pulled this process lifetime.
    private readonly HashSet<string> _deepDone = new(StringComparer.OrdinalIgnoreCase);

    public IBKRDailyBackfillService(
        IServiceProvider sp,
        NpgsqlDataSource db,
        IOptions<IBKRDailyBackfillOptions> options,
        IOptions<IBKRHarvesterOptions> harvesterOptions,
        IBKRDailyBackfillStatus status,
        ILogger<IBKRDailyBackfillService> log)
    {
        _sp = sp;
        _db = db;
        _options = options.Value;
        _status = status;
        _log = log;
        // One universe, config-driven: use the backfill's own Symbols if set, else
        // reuse the harvester's list so daily + intraday cover the SAME names.
        var syms = (_options.Symbols is { Count: > 0 } ? _options.Symbols : harvesterOptions.Value.Symbols)
                   ?? new List<string>();
        _symbols = syms.Select(s => s.Trim().ToUpperInvariant())
                       .Where(s => s.Length > 0).Distinct().ToList();
        _perSymbolDelay = TimeSpan.FromMilliseconds(Math.Clamp(_options.PerSymbolDelayMs, 0, 10_000));
        _topUpInterval = TimeSpan.FromHours(Math.Clamp(_options.TopUpIntervalHours, 1, 168));
        _disabled = !_options.Enabled;

        _status.Enabled = _options.Enabled;
        _status.TotalSymbols = _symbols.Count;
        _status.Period = _options.Period;
    }

    protected override async Task ExecuteAsync(CancellationToken ct)
    {
        if (_disabled)
        {
            _log.LogInformation(
                "IBKRDailyBackfillService DISABLED (IBKR:DailyBackfill:Enabled=false). "
                + "Flip the flag + restart to backfill deep daily history.");
            return;
        }

        _log.LogInformation(
            "IBKRDailyBackfillService started: period={Period} symbols={N} perSymbolDelay={Delay}ms topUp={TopUp}h",
            _options.Period, _symbols.Count, _perSymbolDelay.TotalMilliseconds, _topUpInterval.TotalHours);

        // Let the API + IBKR session warm before hammering history requests.
        try { await Task.Delay(TimeSpan.FromSeconds(45), ct); }
        catch (OperationCanceledException) { return; }

        // ── Deep pass: fill each symbol's history once, paced. ──────────────
        await DeepPassAsync(ct);
        _status.DeepDoneUtc = DateTime.UtcNow;
        _log.LogInformation(
            "IBKRDailyBackfillService deep pass complete: {Done}/{Total} symbols have deep daily history",
            _deepDone.Count, _symbols.Count);

        // ── Top-up loop: keep the recent daily window current, forever. ─────
        while (!ct.IsCancellationRequested)
        {
            try { await Task.Delay(_topUpInterval, ct); }
            catch (OperationCanceledException) { return; }

            // Re-run the deep pass for anything STILL not deep (a failed pull
            // retries), then top up recent bars for everyone.
            await DeepPassAsync(ct);
            await TopUpAsync(ct);
        }
    }

    /// <summary>Deep-history pull for symbols that don't yet reach back far enough.</summary>
    private async Task DeepPassAsync(CancellationToken ct)
    {
        // "Deep enough" = earliest 1d bar is older than this many days. A symbol
        // with only the shallow recent window (from a top-up) still gets its deep pull.
        var deepThreshold = DateTime.UtcNow.AddDays(-_options.DeepMinCoverageDays);

        await using var conn = await _db.OpenConnectionAsync(ct);
        int barsWritten = 0;
        foreach (var sym in _symbols)
        {
            if (ct.IsCancellationRequested) return;
            if (_deepDone.Contains(sym)) continue;

            var earliest = await conn.ExecuteScalarAsync<DateTime?>(
                "SELECT min(ts) FROM ibkr_price_bars WHERE symbol=@sym AND resolution=@res;",
                new { sym, res = Resolution });
            if (earliest is { } e && e <= deepThreshold)
            {
                _deepDone.Add(sym);            // already deep from a prior run
                _status.DeepBackfilled = _deepDone.Count;
                continue;
            }

            _status.InProgressSymbol = sym;
            var n = await FetchAndUpsertAsync(conn, sym, _options.Period, ct);
            if (n > 0)
            {
                barsWritten += n;
                _deepDone.Add(sym);
                _status.DeepBackfilled = _deepDone.Count;
            }
            _status.InProgressSymbol = null;

            try { await Task.Delay(_perSymbolDelay, ct); }
            catch (OperationCanceledException) { return; }
        }
        _status.LastRunBarsWritten = barsWritten;
        _status.LastRunUtc = DateTime.UtcNow;
    }

    /// <summary>Light refresh of the recent daily window so today's bar lands.</summary>
    private async Task TopUpAsync(CancellationToken ct)
    {
        await using var conn = await _db.OpenConnectionAsync(ct);
        int barsWritten = 0;
        foreach (var sym in _symbols)
        {
            if (ct.IsCancellationRequested) return;
            barsWritten += await FetchAndUpsertAsync(conn, sym, _options.TopUpPeriod, ct);
            try { await Task.Delay(_perSymbolDelay, ct); }
            catch (OperationCanceledException) { return; }
        }
        _status.LastRunBarsWritten = barsWritten;
        _status.LastRunUtc = DateTime.UtcNow;
    }

    /// <summary>IBKR-primary (keeps session warm) daily fetch → UPSERT; Yahoo
    /// fallback only if IBKR returns nothing. Returns rows written.</summary>
    private async Task<int> FetchAndUpsertAsync(
        NpgsqlConnection conn, string sym, string period, CancellationToken ct)
    {
        // Fresh scope per call — this is a long-lived singleton; don't hold a
        // scoped IBKRClient/YahooProvider for the process lifetime.
        using var scope = _sp.CreateScope();
        var ibkr = scope.ServiceProvider.GetService<IBKRClient>();
        var yahoo = scope.ServiceProvider.GetService<YahooFinanceProvider>();
        try
        {
            var rows = new List<(DateTime Ts, decimal O, decimal H, decimal L, decimal C, long V)>();
            string source = "ibkr";
            if (ibkr is not null)
            {
                var hist = await ibkr.GetPriceHistoryAsync(sym, period, "1d", ct);
                if (hist.Bars.Count > 0)
                {
                    foreach (var b in hist.Bars)
                        rows.Add((DateTimeOffset.FromUnixTimeMilliseconds(b.TimeMs).UtcDateTime,
                                  b.Open, b.High, b.Low, b.Close, b.Volume));
                }
                else
                {
                    _log.LogWarning(
                        "IBKRDailyBackfill: IBKR no daily bars for {Sym} (period {Period}) — {Err} — trying Yahoo",
                        sym, period, hist.Error ?? $"HTTP {hist.HttpStatus}");
                }
            }
            if (rows.Count == 0 && yahoo is not null)
            {
                var to = DateTime.UtcNow;
                var from = to.AddYears(-20);   // Yahoo caps to what it has
                var series = await yahoo.GetCandlesAsync(sym, "1d", from, to, ct);
                foreach (var c in series.Candles)
                    rows.Add((DateTime.SpecifyKind(c.Timestamp, DateTimeKind.Utc),
                              c.Open, c.High, c.Low, c.Close, c.Volume));
                source = "yahoo";
            }
            if (rows.Count == 0)
            {
                _status.LastError = $"{sym}: no daily bars from IBKR or Yahoo";
                _log.LogWarning("IBKRDailyBackfill: NO daily bars for {Sym} from IBKR or Yahoo", sym);
                return 0;
            }

            const string sql = @"
                INSERT INTO ibkr_price_bars
                  (symbol, resolution, ts, open, high, low, close, volume, source, captured_at_utc)
                VALUES
                  (@symbol, @resolution, @ts, @open, @high, @low, @close, @volume, @source, now())
                ON CONFLICT (symbol, resolution, ts) DO UPDATE SET
                  open=EXCLUDED.open, high=EXCLUDED.high, low=EXCLUDED.low,
                  close=EXCLUDED.close, volume=EXCLUDED.volume,
                  source=EXCLUDED.source, captured_at_utc=now();";
            var n = await conn.ExecuteAsync(sql, rows.Select(r => new
            {
                symbol = sym, resolution = Resolution, ts = r.Ts,
                open = r.O, high = r.H, low = r.L, close = r.C, volume = r.V, source,
            }));
            _log.LogInformation("IBKRDailyBackfill: {Sym} {Source} {N} daily bars (period {Period})",
                sym, source, n, period);
            return n;
        }
        catch (Exception ex)
        {
            _status.LastError = $"{sym}: {ex.Message}";
            _log.LogWarning(ex, "IBKRDailyBackfill: threw for {Sym}", sym);
            return 0;
        }
    }
}

/// <summary>Bound from IBKR:DailyBackfill config. Off by default.</summary>
public sealed class IBKRDailyBackfillOptions
{
    public const string SectionName = "IBKR:DailyBackfill";

    public bool Enabled { get; set; }
    /// <summary>Deep-history window per symbol (IBKR bar period, e.g. "15y").</summary>
    public string Period { get; set; } = "15y";
    /// <summary>Recent window pulled on each top-up so today's bar lands.</summary>
    public string TopUpPeriod { get; set; } = "1m";
    /// <summary>A symbol counts as "deep" once its earliest 1d bar is at least
    /// this many days old — else it gets a deep pull.</summary>
    public int DeepMinCoverageDays { get; set; } = 1000;   // ~4 years
    public int PerSymbolDelayMs { get; set; } = 750;
    public int TopUpIntervalHours { get; set; } = 12;
    /// <summary>Symbols to backfill. Null/empty → reuse IBKR:Harvester:Symbols.</summary>
    public List<string>? Symbols { get; set; }
}

/// <summary>Live progress of the deep daily backfill, for the Data Health board.</summary>
public sealed class IBKRDailyBackfillStatus
{
    public bool Enabled { get; set; }
    public int TotalSymbols { get; set; }
    public int DeepBackfilled { get; set; }
    public string? InProgressSymbol { get; set; }
    public string Period { get; set; } = "";
    public int LastRunBarsWritten { get; set; }
    public DateTime? LastRunUtc { get; set; }
    public DateTime? DeepDoneUtc { get; set; }
    public string? LastError { get; set; }
}

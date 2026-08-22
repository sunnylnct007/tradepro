using Dapper;
using Microsoft.Extensions.Hosting;
using Microsoft.Extensions.Logging;
using Microsoft.Extensions.Options;
using Npgsql;
using TradePro.Api.Providers; // YahooFinanceProvider

namespace TradePro.Api.Providers.IBKR;

/// <summary>
/// Standalone intraday BAR harvester — the platform's continuous price feed.
/// Runs ON the API (AWS), OFF the local Gateway, writing OHLCV bars to
/// <c>ibkr_price_bars</c> for charts + intraday analysis.
///
/// WHY this lives here (not a Mac cron):
///
///   * **Session warmth is the fix.** IBKR intraday history returns
///     "Chart data unavailable" when the brokerage session goes cold from
///     idling (daily is unaffected). Every <see cref="IBKRClient.GetPriceHistoryAsync"/>
///     call auto-triggers the session bring-up + tickle, so a BackgroundService
///     polling on a timer inherently HOLDS THE SESSION WARM — which a fire-and-
///     forget cron cannot. When warm, 1m/5m/1h all return cleanly.
///
///   * **IBKR primary, Yahoo fallback, LOUD.** IBKR is the paid, good source.
///     On any IBKR miss (cold session, pacing, halt) we fall back to Yahoo and
///     record <c>source='yahoo'</c> on those bars so the UI shows BRONZE, never
///     a silent gap. project_data_foundation_findings / central_observability_fail_loud.
///
///   * **Off by default.** Enabled=false means a deploy ships this dormant —
///     it can NEVER disturb the trading path. Operator flips
///     IBKR:Harvester:Enabled=true once the universe + cadence are reviewed.
///
///   * **Decoupled from trading.** Swing/ICH-Equity uses DAILY bars from its own
///     cache; this 1-min feed exists purely for the platform's charts/analysis
///     and can degrade to Yahoo without ever touching the strategy.
/// </summary>
public sealed class IBKRBarHarvester : BackgroundService
{
    private readonly IServiceScopeFactory _scopes;
    private readonly IBKRHarvesterOptions _options;
    private readonly IBKRHarvesterStatus _status;
    private readonly ILogger<IBKRBarHarvester> _log;
    private readonly TimeSpan _interval;
    private readonly TimeSpan _perSymbolDelay;
    private readonly bool _disabled;
    // Symbols whose deep history has been pulled once. Backfill runs once per
    // symbol (spread across sweeps), then steady-state forward harvesting.
    private readonly HashSet<string> _backfilled = new();

    private readonly IBKRPauseState _pause;

    public IBKRBarHarvester(
        IServiceScopeFactory scopes,
        IOptions<IBKRHarvesterOptions> options,
        IBKRHarvesterStatus status,
        IBKRPauseState pause,
        ILogger<IBKRBarHarvester> log)
    {
        _scopes = scopes;
        _options = options.Value;
        _status = status;
        _pause = pause;
        _log = log;
        // Clamp so a misconfig can't spam IBKR (too low) or go dead (too high).
        var sec = Math.Clamp(_options.IntervalSeconds, 60, 3600);
        _interval = TimeSpan.FromSeconds(sec);
        _perSymbolDelay = TimeSpan.FromMilliseconds(
            Math.Clamp(_options.PerSymbolDelayMs, 0, 10_000));
        _disabled = !_options.Enabled;

        _status.Enabled = _options.Enabled;
        _status.IntervalSeconds = sec;
        _status.Resolution = _options.Resolution;
        _status.ConfiguredSymbolCount = _options.Symbols?.Count ?? 0;
    }

    protected override async Task ExecuteAsync(CancellationToken ct)
    {
        if (_disabled)
        {
            _log.LogInformation(
                "IBKRBarHarvester is DISABLED via config (IBKR:Harvester:Enabled=false). "
                + "Flip the flag + restart the API to start harvesting bars.");
            return;
        }
        _log.LogInformation(
            "IBKRBarHarvester started: tick={Tick}s res={Res} period={Period} symbols={N}",
            _interval.TotalSeconds, _options.Resolution, _options.Period,
            _options.Symbols?.Count ?? 0);

        // Settle delay so the API + IBKR session have time to warm.
        try { await Task.Delay(TimeSpan.FromSeconds(20), ct); }
        catch (OperationCanceledException) { return; }

        while (!ct.IsCancellationRequested)
        {
            // Paused: the operator has taken the IBKR session back (portal login).
            // Skip the tick entirely so we don't re-auth behind them.
            if (_pause.Paused)
            {
                _status.LastError = "paused — IBKR session released for portal use";
                try { await Task.Delay(_interval, ct); } catch (OperationCanceledException) { break; }
                continue;
            }
            // Closed market: the bars this sweep would fetch cannot have
            // changed. Idle quietly (status says why) instead of burning the
            // shared session's pacing budget on a re-download of Friday.
            if (_options.RthOnly && !IsUsMarketHours(DateTime.UtcNow))
            {
                _status.LastError = "idle — outside US market hours (RthOnly)";
                try { await Task.Delay(_interval, ct); } catch (OperationCanceledException) { break; }
                continue;
            }
            try
            {
                await OneTickAsync(ct);
                _status.LastError = null;
            }
            catch (OperationCanceledException) { break; }
            catch (Exception ex)
            {
                _log.LogError(ex, "IBKRBarHarvester tick threw — continuing");
                _status.LastError = ex.Message;
            }
            _status.LastTickAtUtc = DateTime.UtcNow;
            _status.NextTickEtaUtc = DateTime.UtcNow + _interval;
            try { await Task.Delay(_interval, ct); }
            catch (OperationCanceledException) { break; }
        }
    }

    /// <summary>US regular trading hours with a small pad: 09:25–16:05 ET,
    /// Mon–Fri. DST-correct via the IANA zone; if tzdata is missing in the
    /// container the fixed-UTC fallback window covers RTH in BOTH DST states
    /// (13:20–21:10 UTC) — erring on the side of fetching.</summary>
    internal static bool IsUsMarketHours(DateTime utcNow)
    {
        try
        {
            var tz = TimeZoneInfo.FindSystemTimeZoneById("America/New_York");
            var et = TimeZoneInfo.ConvertTimeFromUtc(utcNow, tz);
            if (et.DayOfWeek is DayOfWeek.Saturday or DayOfWeek.Sunday) return false;
            var t = et.TimeOfDay;
            return t >= new TimeSpan(9, 25, 0) && t <= new TimeSpan(16, 5, 0);
        }
        catch (TimeZoneNotFoundException)
        {
            if (utcNow.DayOfWeek is DayOfWeek.Saturday or DayOfWeek.Sunday) return false;
            var t = utcNow.TimeOfDay;
            return t >= new TimeSpan(13, 20, 0) && t <= new TimeSpan(21, 10, 0);
        }
    }

    private async Task OneTickAsync(CancellationToken ct)
    {
        if (_options.Symbols is null || _options.Symbols.Count == 0)
        {
            _log.LogDebug("IBKRBarHarvester: empty symbol list — nothing to do.");
            return;
        }

        using var scope = _scopes.CreateScope();
        var sp = scope.ServiceProvider;
        var db = sp.GetRequiredService<NpgsqlDataSource>();
        // CENTRAL CONNECTION (critical): resolve the SAME IBKRClient the OMS order
        // path uses. It shares the singleton IBKRSessionCache -> ONE IBKR Web API
        // session for BOTH harvest and trading. No separate connection = no
        // per-account session contention that would break order placement. Requests
        // serialize on the one session; the per-symbol delay + CAPPED backfill keep
        // harvest load light so history-filling never starves an order. See
        // project_ibkr_harvest_session_isolation.
        var ibkr = sp.GetService<IBKRClient>();
        var yahoo = sp.GetService<YahooFinanceProvider>();

        var res = _options.Resolution;
        var ibkrBar = IbkrBar(res);
        var yahooInterval = YahooInterval(res);

        int ibkrOk = 0, yahooOk = 0, failed = 0, barsWritten = 0, backfilled = 0;
        // Backfill is spread across sweeps (MaxBackfillPerSweep) so the first run
        // doesn't burst a heavy 30-day pull for the whole universe onto the shared
        // trading session at once.
        int backfillBudget = Math.Max(0, _options.MaxBackfillPerSweep);
        await using var conn = await db.OpenConnectionAsync(ct);

        foreach (var symbol in _options.Symbols)
        {
            if (ct.IsCancellationRequested) return;
            var sym = symbol?.Trim();
            if (string.IsNullOrEmpty(sym)) continue;

            // A symbol not yet backfilled gets ONE deep history pull (BackfillPeriod),
            // capped per sweep; everyone else pulls the small recent window (Period).
            bool doBackfill = !_backfilled.Contains(sym) && backfillBudget > 0;
            var period = doBackfill ? _options.BackfillPeriod : _options.Period;

            try
            {
                // 1) PRIMARY: IBKR. This call also keeps the session warm.
                var rows = new List<BarRow>();
                string source = "ibkr";
                if (ibkr is not null)
                {
                    var hist = await ibkr.GetPriceHistoryAsync(sym, period, ibkrBar, ct);
                    if (hist.Bars.Count > 0)
                    {
                        foreach (var b in hist.Bars)
                            rows.Add(new BarRow(
                                DateTimeOffset.FromUnixTimeMilliseconds(b.TimeMs).UtcDateTime,
                                b.Open, b.High, b.Low, b.Close, b.Volume));
                    }
                    else
                    {
                        // Fail-loud: surface WHY IBKR gave nothing (cold session etc.).
                        _log.LogWarning(
                            "IBKRBarHarvester: IBKR no bars for {Sym} ({Res}) — {Err} — falling back to Yahoo",
                            sym, res, hist.Error ?? $"HTTP {hist.HttpStatus}");
                    }
                }

                // 2) FALLBACK: Yahoo (BRONZE), only if IBKR gave nothing.
                if (rows.Count == 0 && yahoo is not null)
                {
                    var (from, to) = LookbackWindow(res);
                    var series = await yahoo.GetCandlesAsync(sym, yahooInterval, from, to, ct);
                    foreach (var c in series.Candles)
                        rows.Add(new BarRow(
                            DateTime.SpecifyKind(c.Timestamp, DateTimeKind.Utc),
                            c.Open, c.High, c.Low, c.Close, c.Volume));
                    source = "yahoo";
                }

                if (rows.Count == 0)
                {
                    failed++;
                    _log.LogWarning("IBKRBarHarvester: NO bars for {Sym} from IBKR or Yahoo", sym);
                }
                else
                {
                    var n = await UpsertAsync(conn, sym, res, source, rows);
                    barsWritten += n;
                    if (source == "ibkr") ibkrOk++; else yahooOk++;
                    // Mark backfilled only on a real write — a failed deep pull retries
                    // next sweep rather than silently dropping the symbol's history.
                    if (doBackfill) { _backfilled.Add(sym); backfillBudget--; backfilled++; }
                }
            }
            catch (Exception ex)
            {
                failed++;
                _log.LogWarning(ex, "IBKRBarHarvester: harvest threw for {Sym}", sym);
            }

            try { await Task.Delay(_perSymbolDelay, ct); }
            catch (OperationCanceledException) { return; }
        }

        _log.LogInformation(
            "IBKRBarHarvester tick complete: ibkr={Ibkr} yahoo={Yahoo} failed={Failed} bars={Bars} backfilled={BF} ({Done}/{Total} symbols have history)",
            ibkrOk, yahooOk, failed, barsWritten, backfilled, _backfilled.Count, _options.Symbols.Count);
        _status.LastTickIbkr = ibkrOk;
        _status.LastTickYahoo = yahooOk;
        _status.LastTickFailed = failed;
        _status.LastTickBarsWritten = barsWritten;
        _status.BackfilledSymbols = _backfilled.Count;
    }

    private readonly record struct BarRow(
        DateTime Ts, decimal Open, decimal High, decimal Low, decimal Close, long Volume);

    /// <summary>UPSERT so re-harvest overwrites (a later IBKR fetch replaces an
    /// earlier Yahoo fallback for the same bar — source flips bronze->good).</summary>
    private static async Task<int> UpsertAsync(
        NpgsqlConnection conn, string symbol, string resolution, string source,
        IReadOnlyList<BarRow> rows)
    {
        const string sql = @"
            INSERT INTO ibkr_price_bars
              (symbol, resolution, ts, open, high, low, close, volume, source, captured_at_utc)
            VALUES
              (@symbol, @resolution, @ts, @open, @high, @low, @close, @volume, @source, now())
            ON CONFLICT (symbol, resolution, ts) DO UPDATE SET
              open=EXCLUDED.open, high=EXCLUDED.high, low=EXCLUDED.low,
              close=EXCLUDED.close, volume=EXCLUDED.volume,
              source=EXCLUDED.source, captured_at_utc=now();";
        var paramList = rows.Select(r => new
        {
            symbol, resolution, ts = r.Ts,
            open = r.Open, high = r.High, low = r.Low, close = r.Close,
            volume = r.Volume, source,
        });
        return await conn.ExecuteAsync(sql, paramList);
    }

    // resolution -> IBKR bar size. IBKR uses "1min"/"5min" for MINUTES
    // ("1m" = a MONTH in IBKR — never send that as a bar).
    private static string IbkrBar(string res) => res switch
    {
        "1m" => "1min", "5m" => "5min", "15m" => "15min", "30m" => "30min",
        "1h" => "1h", "1d" => "1d", _ => "1min",
    };

    private static string YahooInterval(string res) => res switch
    {
        "1m" => "1m", "5m" => "5m", "15m" => "15m", "30m" => "30m",
        "1h" => "1h", "1d" => "1d", _ => "1m",
    };

    // Yahoo needs an explicit [from,to]. Bound intraday windows (Yahoo caps 1m to
    // ~7d); daily gets a longer lookback.
    private static (DateTime From, DateTime To) LookbackWindow(string res)
    {
        var to = DateTime.UtcNow;
        var from = res switch
        {
            "1m" => to.AddDays(-1),
            "5m" or "15m" or "30m" => to.AddDays(-5),
            "1h" => to.AddDays(-30),
            _ => to.AddDays(-365),
        };
        return (from, to);
    }
}

/// <summary>Bound from IBKR:Harvester config. Off by default — flip Enabled=true
/// after the universe + cadence are reviewed.</summary>
public sealed class IBKRHarvesterOptions
{
    public const string SectionName = "IBKR:Harvester";

    /// <summary>Master switch. False = the service logs "disabled" + returns.</summary>
    public bool Enabled { get; set; } = false;

    /// <summary>Seconds between sweeps. Clamped to [60, 3600]. 300 = 5 min.</summary>
    public int IntervalSeconds { get; set; } = 300;

    /// <summary>Sweep only during US regular trading hours (09:25–16:05 ET,
    /// Mon–Fri). Outside RTH the bars can't change, yet before this gate the
    /// harvester re-fetched the same closed-session window 24/7 — measured
    /// 21 Aug 2026 at ~96k IBKR requests/day, ~65% of ALL traffic on the one
    /// shared session, weekends included. Set false only for a deliberate
    /// overnight catch-up run. US holidays are not special-cased: ~10
    /// idle-market days/year of cheap no-change fetches, not worth a
    /// calendar dependency.</summary>
    public bool RthOnly { get; set; } = true;

    /// <summary>Bar resolution: '1m' | '5m' | '15m' | '30m' | '1h' | '1d'.</summary>
    public string Resolution { get; set; } = "1m";

    /// <summary>IBKR history window for STEADY-STATE forward harvesting (e.g. '1d').
    /// Small = keeps recent bars current cheaply.</summary>
    public string Period { get; set; } = "1d";

    /// <summary>IBKR history window for the ONE-TIME per-symbol BACKFILL — the deep
    /// history pull. '1m' = ~30d (IBKR's 1-min ceiling); use '1y'/'2y' for 1h/1d.</summary>
    public string BackfillPeriod { get; set; } = "1m";

    /// <summary>Max symbols to backfill per sweep, so the deep pull is spread over
    /// several ticks instead of bursting the whole universe onto the shared IBKR
    /// session at once (protects order placement). 0 = disable backfill.</summary>
    public int MaxBackfillPerSweep { get; set; } = 5;

    /// <summary>Milliseconds between symbol fetches within a sweep (pacing).</summary>
    public int PerSymbolDelayMs { get; set; } = 250;

    /// <summary>Symbols to harvest each sweep. Bind as IBKR:Harvester:Symbols:0, :1, ...</summary>
    public List<string>? Symbols { get; set; }
}

/// <summary>Live status for the cockpit observability panel — mirrors the
/// IGHarvesterStatus pattern so a silent-dead harvester shows up loud.</summary>
public sealed class IBKRHarvesterStatus
{
    public bool Enabled { get; set; }
    public int IntervalSeconds { get; set; }
    public string Resolution { get; set; } = "1m";
    public int ConfiguredSymbolCount { get; set; }
    public int BackfilledSymbols { get; set; }
    public DateTime? LastTickAtUtc { get; set; }
    public DateTime? NextTickEtaUtc { get; set; }
    public int LastTickIbkr { get; set; }
    public int LastTickYahoo { get; set; }
    public int LastTickFailed { get; set; }
    public int LastTickBarsWritten { get; set; }
    public string? LastError { get; set; }
}

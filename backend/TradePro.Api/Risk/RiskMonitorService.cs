using System.Text.Json;
using Dapper;
using Npgsql;
using TradePro.Api.Providers.Trading212;

namespace TradePro.Api.Risk;

/// <summary>
/// Step 8 — continuous risk monitor.
///
/// A hosted BackgroundService that periodically (default 60s) scans
/// the live portfolio state against defensive thresholds. When a
/// rule trips, it can:
///   1. Log a risk_event with decision='KILL_SWITCH'
///   2. Auto-freeze the system (set system_state.mode='frozen')
///   3. (Later) cancel pending BUY orders that haven't dispatched
///
/// Per the systematic-trading discussion — this loop ONLY DEFENDS.
/// It never opens new positions; that's the slow loop's exclusive
/// privilege. Multiple ways to say no, one way to say yes.
///
/// Rules wired Day 1 (cheap, broker-data driven):
///   • portfolio_drawdown — portfolio_value drops > threshold% from
///     the rolling high-water mark within a window. Auto-freeze.
///   • position_drift_critical — any unresolved critical drift event
///     → auto-freeze (broker disagrees with us on what we hold).
///   • cash_negative — T212 cash goes negative or absurdly small,
///     suggests something happened we don't know about → auto-freeze.
///
/// Rules NOT wired Day 1 (need indicator/sentiment service):
///   • VIX > N (needs market data feed for ^VIX tick)
///   • LLM sentiment shift on held positions (needs news_sentiment
///     pipeline productisation)
///   • Per-position drawdown vs entry (needs per-position cost basis
///     tracking — derive from oms_fills next iteration)
/// </summary>
public sealed class RiskMonitorService : BackgroundService
{
    private readonly IServiceScopeFactory _scopes;
    private readonly ILogger<RiskMonitorService> _log;
    private readonly TimeSpan _interval;

    public RiskMonitorService(
        IServiceScopeFactory scopes,
        IConfiguration cfg,
        ILogger<RiskMonitorService> log)
    {
        _scopes = scopes;
        _log = log;
        var seconds = cfg.GetValue<int?>("Risk:MonitorIntervalSeconds") ?? 60;
        _interval = TimeSpan.FromSeconds(Math.Clamp(seconds, 10, 3600));
    }

    protected override async Task ExecuteAsync(CancellationToken ct)
    {
        _log.LogInformation("RiskMonitorService starting (interval {Sec}s)", _interval.TotalSeconds);
        // Initial small delay so the API has time to come up before
        // we start hammering the cache.
        try { await Task.Delay(TimeSpan.FromSeconds(15), ct); }
        catch (OperationCanceledException) { return; }

        while (!ct.IsCancellationRequested)
        {
            try { await OneCycleAsync(ct); }
            catch (OperationCanceledException) { break; }
            catch (Exception ex)
            {
                // A failure in one cycle must NOT bring the loop down —
                // an unmonitored portfolio is the worst state.
                _log.LogError(ex, "risk monitor cycle errored — continuing");
            }
            try { await Task.Delay(_interval, ct); }
            catch (OperationCanceledException) { break; }
        }
        _log.LogInformation("RiskMonitorService stopping");
    }

    private async Task OneCycleAsync(CancellationToken ct)
    {
        using var scope = _scopes.CreateScope();
        var sp = scope.ServiceProvider;
        var db = sp.GetRequiredService<NpgsqlDataSource>();
        var cashCache = sp.GetRequiredService<Trading212DemoCashCache>();

        // If we're already frozen / panic, no point evaluating: just
        // keep the loop alive so when the operator resumes we pick up
        // again. The operator may have frozen for a reason we'd
        // duplicate-trigger on.
        var currentMode = await CurrentModeAsync(db);
        if (currentMode != "normal") return;

        var settings = await ReadMonitorSettingsAsync(db);

        // Rule 1: portfolio drawdown. Use T212 total as the proxy for
        // portfolio value; high-water-mark is stored in
        // risk_monitor_state table (created lazily below). DD calc is
        // (now - hwm) / hwm; trip when < -threshold.
        var cash = await cashCache.GetAsync(ct);
        if (cash.Error is null && cash.Total is > 0m)
        {
            var portValue = cash.Total.Value;
            var hwm = await UpsertHighWaterMarkAsync(db, "t212_demo", portValue);
            var ddFrac = (double)((portValue - hwm) / hwm);
            if (ddFrac < -settings.MaxPortfolioDrawdown && settings.MaxPortfolioDrawdown > 0)
            {
                await TripAndFreezeAsync(db, sp,
                    gate: "portfolio_drawdown",
                    reason: $"portfolio drawdown {ddFrac:P2} exceeds threshold "
                          + $"{-settings.MaxPortfolioDrawdown:P2} (hwm={hwm:C0}, now={portValue:C0})");
            }
        }

        // Rule 2: critical position drift. If any unresolved critical
        // drift, freeze — broker disagrees with our records by >5%
        // on at least one symbol; we shouldn't dispatch anything
        // until the operator reviews.
        var criticalDrift = await CountUnresolvedCriticalDriftAsync(db);
        if (criticalDrift > 0)
        {
            await TripAndFreezeAsync(db, sp,
                gate: "position_drift_critical",
                reason: $"{criticalDrift} unresolved critical position drift event(s) — "
                      + $"broker disagrees with our records by >5% on at least one symbol; "
                      + $"resolve drift before resuming");
        }

        // Rule 3: cash_negative — T212 free cash dropped below zero
        // or below an unusually small threshold. Could mean an unknown
        // commission, FX conversion fee, dividend reversal, etc.
        if (cash.Error is null && cash.Free is decimal free && free < settings.MinFreeCashUsd)
        {
            await TripAndFreezeAsync(db, sp,
                gate: "cash_negative",
                reason: $"T212 free cash {free:C0} dropped below threshold "
                      + $"{settings.MinFreeCashUsd:C0} — investigate unaccounted activity");
        }
    }

    // ─────────────────────────────────────────────────────────────────

    private static async Task<string> CurrentModeAsync(NpgsqlDataSource db)
    {
        await using var conn = await db.OpenConnectionAsync();
        var m = await conn.QueryFirstOrDefaultAsync<string>(
            "SELECT mode FROM system_state WHERE id = 1;");
        return string.IsNullOrEmpty(m) ? "normal" : m;
    }

    private async Task<int> CountUnresolvedCriticalDriftAsync(NpgsqlDataSource db)
    {
        await using var conn = await db.OpenConnectionAsync();
        return await conn.ExecuteScalarAsync<int>(@"
            SELECT COUNT(*) FROM position_drift_events
            WHERE severity = 'critical' AND resolved_at_utc IS NULL;");
    }

    private async Task<decimal> UpsertHighWaterMarkAsync(
        NpgsqlDataSource db, string scope, decimal portValue)
    {
        await using var conn = await db.OpenConnectionAsync();
        // Lazy table create — keeps the monitor self-contained without
        // a dedicated migration (which we'd otherwise need for one
        // tiny key-value row).
        await conn.ExecuteAsync(@"
            CREATE TABLE IF NOT EXISTS risk_monitor_state (
                scope TEXT PRIMARY KEY,
                high_water_mark NUMERIC NOT NULL,
                updated_at_utc TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );");
        // Upsert that promotes the high-water mark if the current
        // value exceeds it. Returns the (possibly newly promoted)
        // hwm.
        var hwm = await conn.QueryFirstAsync<decimal>(@"
            INSERT INTO risk_monitor_state (scope, high_water_mark)
            VALUES (@scope, @portValue)
            ON CONFLICT (scope) DO UPDATE
            SET high_water_mark = GREATEST(risk_monitor_state.high_water_mark, EXCLUDED.high_water_mark),
                updated_at_utc = NOW()
            RETURNING high_water_mark;",
            new { scope, portValue });
        return hwm;
    }

    private async Task TripAndFreezeAsync(
        NpgsqlDataSource db, IServiceProvider sp,
        string gate, string reason)
    {
        await using var conn = await db.OpenConnectionAsync();
        // Idempotency — if we already wrote a KILL_SWITCH event for
        // this exact gate today, don't spam. The operator's already
        // got the alert; the auto-freeze sticks until they resume.
        var alreadyTripped = await conn.ExecuteScalarAsync<int>(@"
            SELECT COUNT(*) FROM risk_events
            WHERE gate = @gate AND decision = 'KILL_SWITCH'
              AND occurred_at_utc >= NOW() - INTERVAL '1 hour';",
            new { gate });
        if (alreadyTripped > 0) return;

        var detail = JsonSerializer.Serialize(new
        {
            triggered_by = "RiskMonitorService",
            timestamp = DateTime.UtcNow,
        });

        await using var tx = await conn.BeginTransactionAsync();
        try
        {
            // 1) Log the kill-switch event
            await conn.ExecuteAsync(@"
                INSERT INTO risk_events
                    (strategy_id, symbol, side, qty, broker,
                     decision, gate, reason, detail_json)
                VALUES ('_monitor', '*', 'N/A', 0, '*',
                        'KILL_SWITCH', @gate, @reason, @detail::jsonb);",
                new { gate, reason, detail }, transaction: tx);
            // 2) Flip system_state to frozen + audit
            var prior = await conn.QueryFirstOrDefaultAsync<string>(
                "SELECT mode FROM system_state WHERE id = 1 FOR UPDATE;",
                transaction: tx) ?? "normal";
            if (prior == "normal")
            {
                await conn.ExecuteAsync(@"
                    UPDATE system_state
                    SET mode = 'frozen',
                        reason = @reason,
                        set_at_utc = NOW(),
                        set_by = 'risk_monitor'
                    WHERE id = 1;",
                    new { reason }, transaction: tx);
                await conn.ExecuteAsync(@"
                    INSERT INTO system_state_events
                        (prior_mode, new_mode, reason, changed_by)
                    VALUES (@prior, 'frozen', @reason, 'risk_monitor');",
                    new { prior, reason }, transaction: tx);
            }
            await tx.CommitAsync();
        }
        catch
        {
            await tx.RollbackAsync();
            throw;
        }

        _log.LogWarning(
            "RISK MONITOR auto-freeze: gate={Gate} reason={Reason}", gate, reason);
    }

    private async Task<MonitorSettings> ReadMonitorSettingsAsync(NpgsqlDataSource db)
    {
        await using var conn = await db.OpenConnectionAsync();
        var rows = (await conn.QueryAsync<(string key, string value_text)>(@"
            SELECT key, value::text AS value_text
            FROM app_settings_kv
            WHERE key IN (
                'risk_monitor_max_drawdown',
                'risk_monitor_min_free_cash_usd'
            );")).ToDictionary(r => r.key, r => r.value_text);

        T Parse<T>(string key, T fallback)
        {
            if (!rows.TryGetValue(key, out var raw) || string.IsNullOrEmpty(raw)) return fallback;
            try { return JsonSerializer.Deserialize<T>(raw)!; }
            catch { return fallback; }
        }
        return new MonitorSettings(
            MaxPortfolioDrawdown: Parse("risk_monitor_max_drawdown", 0.10),  // 10% default
            MinFreeCashUsd: Parse("risk_monitor_min_free_cash_usd", -100m)); // -100 default
    }

    private sealed record MonitorSettings(
        double MaxPortfolioDrawdown,
        decimal MinFreeCashUsd);
}

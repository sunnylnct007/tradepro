using System.Net;
using System.Security.Cryptography;
using System.Text;
using System.Text.Json;
using Dapper;
using Npgsql;
using TradePro.Api.Oms;
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

        // Rule 4 + 5: per-position stop-loss + take-profit (defensive
        // overlay per project_overnight_risk_options).
        //
        // For each currently-held T212 position, compute its return
        // since entry (broker's average_price_paid vs current_price).
        // If the position is down past stop_loss_pct, flag it (and
        // eventually trigger an exit order). If it's up past
        // take_profit_pct, flag for partial trim. NEVER OPEN —
        // monitor only defends.
        //
        // Day 1: log a risk_event per flagged position. Auto-exit
        // wiring (push CLOSE order to OMS) comes in the next
        // iteration — for now the operator sees the alert in the
        // banner / digest and acts manually.
        await CheckPositionLevelDefenseAsync(sp, settings, ct);
    }

    private async Task CheckPositionLevelDefenseAsync(
        IServiceProvider sp, MonitorSettings settings, CancellationToken ct)
    {
        if (settings.StopLossPct <= 0 && settings.TakeProfitPct <= 0) return;
        var posCache = sp.GetService<Trading212DemoPositionsCache>();
        if (posCache is null) return;
        var posResult = await posCache.GetAsync(ct);
        if (posResult.HttpStatus == (int)HttpStatusCode.TooManyRequests
            && (posResult.Positions is null || posResult.Positions.Count == 0)) return;
        var positions = posResult.Positions ?? new List<Trading212Position>();
        var db = sp.GetRequiredService<NpgsqlDataSource>();
        foreach (var p in positions)
        {
            // T212 has been seen returning positions with a null/empty
            // ticker (race between symbol metadata and snapshot). Skip
            // them — risk_events.symbol is NOT NULL and inserting one
            // crashes the whole monitor cycle.
            if (string.IsNullOrWhiteSpace(p.Ticker)) continue;
            if (p.Quantity <= 0m) continue;
            if (p.AveragePricePaid is null or <= 0m) continue;
            if (p.CurrentPrice is null or <= 0m) continue;
            var avg = p.AveragePricePaid.Value;
            var cur = p.CurrentPrice.Value;
            var retPct = (double)((cur - avg) / avg);
            string? gate = null;
            string? reason = null;
            if (settings.StopLossPct > 0 && retPct <= -settings.StopLossPct)
            {
                gate = "position_stop_loss";
                reason = $"position {p.Ticker} down {retPct:P2} since entry "
                       + $"({avg:F2} → {cur:F2}); stop-loss threshold {-settings.StopLossPct:P2}";
            }
            else if (settings.TakeProfitPct > 0 && retPct >= settings.TakeProfitPct)
            {
                gate = "position_take_profit";
                reason = $"position {p.Ticker} up {retPct:P2} since entry "
                       + $"({avg:F2} → {cur:F2}); take-profit threshold {settings.TakeProfitPct:P2}";
            }
            if (gate is null) continue;

            // Per-symbol idempotency — only log once per gate per
            // 4 hours. The operator should see the alert; spamming
            // every minute helps no one. Auto-exit (when wired) will
            // be the actual mitigation; this log is the audit trail.
            await using var conn = await db.OpenConnectionAsync();
            var recent = await conn.ExecuteScalarAsync<int>(@"
                SELECT COUNT(*) FROM risk_events
                WHERE symbol = @sym AND gate = @gate
                  AND occurred_at_utc >= NOW() - INTERVAL '4 hours';",
                new { sym = p.Ticker, gate });
            if (recent > 0) continue;

            await conn.ExecuteAsync(@"
                INSERT INTO risk_events
                    (strategy_id, symbol, side, qty, broker,
                     decision, gate, reason, detail_json)
                VALUES ('_monitor', @symbol, 'N/A', @qty, 'T212_DEMO',
                        'KILL_SWITCH', @gate, @reason, @detail::jsonb);",
                new
                {
                    symbol = p.Ticker,
                    qty = p.Quantity,
                    gate,
                    reason,
                    detail = JsonSerializer.Serialize(new
                    {
                        avg_entry_price = avg,
                        current_price = cur,
                        return_pct = retPct,
                        threshold_pct = gate == "position_stop_loss"
                            ? -settings.StopLossPct : settings.TakeProfitPct,
                    }),
                });
            _log.LogWarning(
                "RISK MONITOR position-level alert: {Gate} on {Symbol} — {Reason}",
                gate, p.Ticker, reason);

            // Auto-exit if enabled. For stop_loss: close the full
            // position. For take_profit: trim half (lock in gains,
            // let the rest run with trend). System_state=frozen
            // still allows SELLs by design — defensive exits are
            // the whole point of staying alive while frozen.
            if (settings.AutoExitOnStopLoss && gate == "position_stop_loss")
            {
                await PushExitOrderAsync(sp, p, qty: p.Quantity,
                    reason: $"auto-exit stop_loss: {reason}");
            }
            else if (settings.AutoExitOnTakeProfit && gate == "position_take_profit")
            {
                // Round HALF down to 4 dp — T212 accepts fractional
                // shares but only to 4 dp on US equities.
                var half = Math.Round(p.Quantity / 2m, 4, MidpointRounding.ToZero);
                if (half > 0m)
                {
                    await PushExitOrderAsync(sp, p, qty: half,
                        reason: $"auto-trim take_profit (half): {reason}");
                }
            }
        }
    }

    private async Task PushExitOrderAsync(
        IServiceProvider sp, Trading212Position pos, decimal qty, string reason)
    {
        try
        {
            var oms = sp.GetService<IOmsService>();
            if (oms is null)
            {
                _log.LogWarning("auto-exit skipped: IOmsService unavailable");
                return;
            }
            // Deterministic ClientOrderId so a flapping signal doesn't
            // queue 20 duplicate exits — same (symbol, qty, day) =
            // same UUID = OMS returns the existing row.
            var seed = $"auto-exit:{pos.Ticker}:{qty:0.0000}:"
                     + $"{DateTime.UtcNow:yyyyMMdd}";
            var clientId = DeterministicGuid(seed);
            var intent = new OrderIntent(
                ClientOrderId: clientId,
                Broker: "T212_DEMO",
                Symbol: pos.Ticker,           // already in T212 form (AAPL_US_EQ)
                Side: "SELL",
                Qty: qty,
                OrderType: "MKT",
                StrategyId: "ichimoku_equity");
            var order = await oms.EnqueueAsync(intent, "risk_monitor");
            try
            {
                var done = await oms.ApproveAsync(order.Id, "risk_monitor");
                _log.LogInformation(
                    "AUTO-EXIT placed: SELL {Qty} {Sym} order={Id} state={State} — {Reason}",
                    qty, pos.Ticker, done.Id, done.State, reason);
            }
            catch (InvalidOperationException gateRefusal)
            {
                _log.LogWarning(
                    "AUTO-EXIT enqueued but refused by gate: {Sym} — {Msg}",
                    pos.Ticker, gateRefusal.Message);
            }
        }
        catch (Exception ex)
        {
            _log.LogError(ex, "auto-exit failed for {Sym}", pos.Ticker);
        }
    }

    private static Guid DeterministicGuid(string input)
    {
        using var md5 = MD5.Create();
        var bytes = md5.ComputeHash(Encoding.UTF8.GetBytes(input));
        return new Guid(bytes);
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
                'risk_monitor_min_free_cash_usd',
                'risk_monitor_stop_loss_pct',
                'risk_monitor_take_profit_pct',
                'risk_monitor_auto_exit_stop_loss',
                'risk_monitor_auto_exit_take_profit'
            );")).ToDictionary(r => r.key, r => r.value_text);

        T Parse<T>(string key, T fallback)
        {
            if (!rows.TryGetValue(key, out var raw) || string.IsNullOrEmpty(raw)) return fallback;
            try { return JsonSerializer.Deserialize<T>(raw)!; }
            catch { return fallback; }
        }
        return new MonitorSettings(
            MaxPortfolioDrawdown: Parse("risk_monitor_max_drawdown", 0.10),  // 10% default
            MinFreeCashUsd: Parse("risk_monitor_min_free_cash_usd", -100m),  // -100 default
            // Per-position thresholds — 0 disables. Defaults conservative:
            // -3% stop, +8% take-profit. Operator tunes via /settings.
            StopLossPct: Parse("risk_monitor_stop_loss_pct", 0.03),
            TakeProfitPct: Parse("risk_monitor_take_profit_pct", 0.08),
            // Auto-exit defaults TRUE for stop-loss (defensive), FALSE
            // for take-profit (operator chooses whether to mechanically
            // trim or let winners run). Tunable in /settings.
            AutoExitOnStopLoss: Parse("risk_monitor_auto_exit_stop_loss", true),
            AutoExitOnTakeProfit: Parse("risk_monitor_auto_exit_take_profit", false));
    }

    private sealed record MonitorSettings(
        double MaxPortfolioDrawdown,
        decimal MinFreeCashUsd,
        double StopLossPct,
        double TakeProfitPct,
        bool AutoExitOnStopLoss,
        bool AutoExitOnTakeProfit);
}

using System.Net;
using System.Text.Json;
using Dapper;
using Npgsql;
using TradePro.Api.Oms;
using TradePro.Api.Providers.Trading212;

namespace TradePro.Api.Risk;

/// <summary>
/// Risk module Phase 2 — pre-trade gate. Sits between order approval
/// and broker dispatch. Each order goes through every gate in
/// sequence; ANY blocker → refused; all-pass → allowed (with optional
/// warnings).
///
/// Gates wired here (cheap — no external indicator data yet):
///   1. symbol_blacklist  — operator-curated do-not-trade list
///   2. order_size_cap    — qty + notional caps from settings
///   3. velocity_gate     — too many orders per strategy per minute
///   4. cash_check        — order cost vs T212 free × safety margin
///
/// Multi-indicator vetoes (RSI extreme, Bollinger touch, volume
/// divergence, LLM sentiment) come in a follow-up commit — they need
/// the indicator snapshot service to read latest values per symbol.
///
/// Every decision (allowed OR blocked) writes one row to risk_events
/// so the audit trail is complete. Fail-closed: if a gate's data
/// source is unreachable (DB blip, T212 down) and risk_fail_closed
/// is TRUE, the order is BLOCKED — better to refuse than to dispatch
/// blind.
/// </summary>
public sealed class RiskGate
{
    private readonly NpgsqlDataSource _db;
    private readonly Trading212DemoCashCache _cashCache;
    private readonly ILogger<RiskGate> _log;

    public RiskGate(
        NpgsqlDataSource db,
        Trading212DemoCashCache cashCache,
        ILogger<RiskGate> log)
    {
        _db = db;
        _cashCache = cashCache;
        _log = log;
    }

    public async Task<RiskCheckResult> EvaluateAsync(
        OmsOrder order, CancellationToken ct)
    {
        var failures = new List<RiskFailure>();
        var warnings = new List<RiskFailure>();
        var context = new Dictionary<string, object>();

        // Load settings once — single round-trip.
        var settings = await ReadSettingsAsync();
        context["settings_snapshot"] = settings;

        try
        {
            // Gate 1: blacklist
            var blacklisted = await IsBlacklistedAsync(order.Symbol);
            if (blacklisted is not null)
            {
                failures.Add(new RiskFailure(
                    "blacklist",
                    $"symbol {order.Symbol} is blacklisted: {blacklisted}"));
            }

            // Gate 2: order size cap (qty + notional)
            if (settings.MaxOrderQty > 0 && order.Qty > settings.MaxOrderQty)
            {
                failures.Add(new RiskFailure(
                    "order_size_cap",
                    $"qty {order.Qty} > cap {settings.MaxOrderQty}"));
            }
            // Notional needs a price. ApproveAsync ran BEFORE this with
            // an approval row that has the broker price — for now we
            // estimate as qty × avg_fill_price if filled, else skip.
            // (We can wire current_price from T212 positions cache later
            // if we want pre-fill notional caps.)
            if (settings.MaxOrderNotionalUsd > 0
                && order.AvgFillPrice is > 0m)
            {
                var notional = order.Qty * order.AvgFillPrice.Value;
                if (notional > settings.MaxOrderNotionalUsd)
                {
                    failures.Add(new RiskFailure(
                        "order_size_cap",
                        $"notional {notional:C0} > cap {settings.MaxOrderNotionalUsd:C0}"));
                }
            }

            // Gate 3: velocity
            if (settings.MaxOrdersPerMinute > 0
                && !string.IsNullOrWhiteSpace(order.StrategyId))
            {
                var perMin = await CountOrdersInLastMinuteAsync(order.StrategyId);
                if (perMin >= settings.MaxOrdersPerMinute)
                {
                    failures.Add(new RiskFailure(
                        "velocity_gate",
                        $"strategy {order.StrategyId} has {perMin} orders in the last minute "
                        + $"(cap {settings.MaxOrdersPerMinute}) — runaway protection"));
                }
            }

            // Gate 4: cash check (T212 demo only — we only know its cash)
            if (settings.CashSafetyMargin > 0
                && string.Equals(order.Broker, "T212_DEMO", StringComparison.OrdinalIgnoreCase)
                && string.Equals(order.Side, "BUY", StringComparison.OrdinalIgnoreCase)
                && order.AvgFillPrice is > 0m)
            {
                var orderCost = order.Qty * order.AvgFillPrice.Value;
                try
                {
                    var cash = await _cashCache.GetAsync(ct);
                    if (cash.HttpStatus == (int)HttpStatusCode.TooManyRequests
                        && cash.Free is null)
                    {
                        if (settings.FailClosed)
                            failures.Add(new RiskFailure(
                                "cash_check",
                                "T212 cash unavailable (rate-limited, no cache) and risk_fail_closed=TRUE"));
                        else
                            warnings.Add(new RiskFailure(
                                "cash_check",
                                "T212 cash unavailable; risk_fail_closed=FALSE so allowed"));
                    }
                    else
                    {
                        var available = (cash.Free ?? 0m) * (decimal)settings.CashSafetyMargin;
                        context["t212_free"] = cash.Free ?? 0m;
                        context["available_after_margin"] = available;
                        context["order_cost"] = orderCost;
                        if (orderCost > available)
                        {
                            failures.Add(new RiskFailure(
                                "cash_check",
                                $"order cost {orderCost:C0} > available {available:C0} "
                                + $"(T212 free {cash.Free:C0} × safety {settings.CashSafetyMargin:P0})"));
                        }
                    }
                }
                catch (Exception ex)
                {
                    _log.LogWarning(ex, "cash_check failed to evaluate");
                    if (settings.FailClosed)
                        failures.Add(new RiskFailure(
                            "cash_check",
                            $"cash gate errored and risk_fail_closed=TRUE: {ex.Message}"));
                }
            }
        }
        catch (Exception ex) when (settings.FailClosed)
        {
            _log.LogError(ex, "risk gate evaluation errored — failing closed");
            failures.Add(new RiskFailure(
                "gate_internal",
                $"risk gate internal error and fail_closed=TRUE: {ex.Message}"));
        }

        var passed = failures.Count == 0;
        var decision = passed ? "ALLOWED" : "BLOCKED";

        // Write one risk_events row per failure (so each gate has its
        // own audit line) + one ALLOWED row when everything passed.
        await PersistEventsAsync(order, decision, failures, warnings, context);

        return new RiskCheckResult(passed, failures, warnings, context);
    }

    // ─────────────────────────────────────────────────────────────────

    private async Task<string?> IsBlacklistedAsync(string symbol)
    {
        await using var conn = await _db.OpenConnectionAsync();
        // Match both the natural ticker and the T212 broker form
        // (AAPL vs AAPL_US_EQ) — blacklist applies regardless of
        // how the order was named.
        var bareTicker = symbol;
        var underscore = symbol.IndexOf('_');
        if (underscore > 0) bareTicker = symbol[..underscore];
        return await conn.QueryFirstOrDefaultAsync<string>(@"
            SELECT reason FROM symbol_blacklist
            WHERE ticker = @symbol OR ticker = @bareTicker
            LIMIT 1;",
            new { symbol, bareTicker });
    }

    private async Task<int> CountOrdersInLastMinuteAsync(string strategyId)
    {
        await using var conn = await _db.OpenConnectionAsync();
        // Look at the actual oms_orders table — simpler than maintaining
        // risk_velocity_window for Day 1 (and accurate even when the
        // worker crashes and misses the bucket bump).
        return await conn.ExecuteScalarAsync<int>(@"
            SELECT COUNT(*) FROM oms_orders
            WHERE strategy_id = @strategyId
              AND created_at_utc >= NOW() - INTERVAL '1 minute';",
            new { strategyId });
    }

    private async Task<RiskSettings> ReadSettingsAsync()
    {
        await using var conn = await _db.OpenConnectionAsync();
        var rows = (await conn.QueryAsync<(string key, string value_text)>(@"
            SELECT key, value::text AS value_text
            FROM app_settings_kv
            WHERE key IN (
                'risk_max_order_qty',
                'risk_max_order_notional_usd',
                'risk_max_orders_per_minute',
                'risk_cash_safety_margin',
                'risk_fail_closed'
            );")).ToDictionary(r => r.key, r => r.value_text);

        T Parse<T>(string key, T fallback)
        {
            if (!rows.TryGetValue(key, out var raw) || string.IsNullOrEmpty(raw)) return fallback;
            try { return JsonSerializer.Deserialize<T>(raw)!; }
            catch { return fallback; }
        }

        return new RiskSettings(
            MaxOrderQty: Parse("risk_max_order_qty", 0m),
            MaxOrderNotionalUsd: Parse("risk_max_order_notional_usd", 0m),
            MaxOrdersPerMinute: Parse("risk_max_orders_per_minute", 0),
            CashSafetyMargin: Parse("risk_cash_safety_margin", 0.0),
            FailClosed: Parse("risk_fail_closed", true));
    }

    private async Task PersistEventsAsync(
        OmsOrder order, string decision,
        List<RiskFailure> failures, List<RiskFailure> warnings,
        Dictionary<string, object> context)
    {
        var ctxJson = JsonSerializer.Serialize(context);
        await using var conn = await _db.OpenConnectionAsync();
        if (failures.Count == 0)
        {
            // ALLOWED — one row that captures "every gate passed."
            await conn.ExecuteAsync(@"
                INSERT INTO risk_events
                    (order_id, strategy_id, symbol, side, qty, broker,
                     decision, gate, reason, detail_json)
                VALUES (@orderId, @strategyId, @symbol, @side, @qty, @broker,
                        @decision, 'all_gates', 'all gates passed', @ctxJson::jsonb);",
                new
                {
                    orderId = order.Id, strategyId = order.StrategyId ?? "",
                    symbol = order.Symbol, side = order.Side, qty = order.Qty,
                    broker = order.Broker, decision, ctxJson,
                });
            return;
        }
        // BLOCKED — one row per failure + warnings as separate rows so
        // the /risk audit panel can show each one with its own
        // (gate, reason) pair.
        foreach (var f in failures)
        {
            await conn.ExecuteAsync(@"
                INSERT INTO risk_events
                    (order_id, strategy_id, symbol, side, qty, broker,
                     decision, gate, reason, detail_json)
                VALUES (@orderId, @strategyId, @symbol, @side, @qty, @broker,
                        @decision, @gate, @reason, @ctxJson::jsonb);",
                new
                {
                    orderId = order.Id, strategyId = order.StrategyId ?? "",
                    symbol = order.Symbol, side = order.Side, qty = order.Qty,
                    broker = order.Broker, decision,
                    gate = f.Gate, reason = f.Reason, ctxJson,
                });
        }
        foreach (var w in warnings)
        {
            await conn.ExecuteAsync(@"
                INSERT INTO risk_events
                    (order_id, strategy_id, symbol, side, qty, broker,
                     decision, gate, reason, detail_json)
                VALUES (@orderId, @strategyId, @symbol, @side, @qty, @broker,
                        'ALLOWED', @gate, @reason, @ctxJson::jsonb);",
                new
                {
                    orderId = order.Id, strategyId = order.StrategyId ?? "",
                    symbol = order.Symbol, side = order.Side, qty = order.Qty,
                    broker = order.Broker,
                    gate = w.Gate, reason = "WARN: " + w.Reason, ctxJson,
                });
        }
    }

    private sealed record RiskSettings(
        decimal MaxOrderQty,
        decimal MaxOrderNotionalUsd,
        int MaxOrdersPerMinute,
        double CashSafetyMargin,
        bool FailClosed);
}

public sealed record RiskFailure(string Gate, string Reason);

public sealed record RiskCheckResult(
    bool Passed,
    IReadOnlyList<RiskFailure> Failures,
    IReadOnlyList<RiskFailure> Warnings,
    IReadOnlyDictionary<string, object> Context);

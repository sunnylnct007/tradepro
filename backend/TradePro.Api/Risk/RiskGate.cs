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

            // Gate 3b: market hours — never send an order into a CLOSED
            // venue. Spot FX is 24/5; reject FX orders on the weekend
            // regardless of which strategy/path emitted them or what the
            // (possibly stale, replayed) bar timestamp said. This is the
            // universal backstop for the "weekend duplicate flood": the
            // strategy-level guard can be bypassed (manual trigger,
            // replayed Friday bars); the risk engine cannot. Wall-clock
            // (UTC now), NOT the bar/order timestamp.
            if (IsFxSymbol(order.Symbol) && !FxMarketOpenUtc(DateTime.UtcNow))
            {
                failures.Add(new RiskFailure(
                    "market_closed",
                    $"FX market is closed (weekend) — refusing {order.Symbol}"));
            }

            // Gate 3b-ii: US equity market hours. Orders placed after the
            // cash-session close (or pre-open / weekend) don't fill — they
            // sit SUBMITTED queued for the next open (the exact cause of the
            // 10 stuck ichimoku_equity orders placed at 20:47 UTC, after the
            // 20:00 UTC EDT close). The trader's rule: "if market is closed,
            // don't send the order." Wall-clock now, converted to NY so it's
            // DST-correct (close is 20:00 UTC in summer, 21:00 UTC in winter).
            if (IsUsEquitySymbol(order.Symbol) && !UsEquityMarketOpenUtc(DateTime.UtcNow))
            {
                failures.Add(new RiskFailure(
                    "market_closed",
                    $"US equity market is closed — refusing {order.Symbol} "
                    + "(would queue unfilled until next open)"));
            }

            // Gate 3c: broker capability — Trading 212's public API is
            // Invest-only (equities + ETFs); it CANNOT trade FX/CFD. An FX
            // order routed to T212 (mis-config, or a manual trigger that
            // picked the default broker) can never fill — reject it here so
            // it doesn't sit PENDING or get rejected noisily downstream.
            // FX belongs on IG. (This is the "T212 ✗ FX" the UI flags.)
            if (IsFxSymbol(order.Symbol)
                && order.Broker.StartsWith("T212", StringComparison.OrdinalIgnoreCase))
            {
                failures.Add(new RiskFailure(
                    "broker_capability",
                    $"T212 cannot trade FX ({order.Symbol}) — its API is equity-only; route FX to IG"));
            }

            // Gate 4a: sentiment veto on BUYs. Reads the latest score
            // from sentiment_scores; vetoes new entries when the LLM
            // says the recent news is materially negative. Never blocks
            // SELLs (those are defensive exits — we WANT out when
            // sentiment turns). No-op when no score exists or the
            // score is too old (max_age setting).
            if (settings.SentimentBuyVetoScore < 0
                && string.Equals(order.Side, "BUY", StringComparison.OrdinalIgnoreCase))
            {
                var sent = await ReadLatestSentimentAsync(order.Symbol);
                if (sent is not null
                    && sent.Value.ageMinutes <= settings.SentimentMaxAgeMinutes
                    && sent.Value.score < settings.SentimentBuyVetoScore)
                {
                    failures.Add(new RiskFailure(
                        "sentiment_negative",
                        $"sentiment {sent.Value.score:F2} ({sent.Value.classification}) "
                        + $"on {order.Symbol} below veto threshold "
                        + $"{settings.SentimentBuyVetoScore:F2}; "
                        + $"scored {sent.Value.ageMinutes:F0}min ago"));
                }
            }

            // Gate 4-floor: hard minimum free cash. Runs even PRE-FILL (no
            // price needed), unlike the notional cash_check below which is
            // skipped until an order has a price. A near-empty demo account
            // blocks new BUYs LOCALLY with a clear reason instead of firing
            // a whole basket that T212 rejects en masse with
            // insufficient-free-for-stocks (the zero-fill root cause). 0
            // disables it (operator-tunable via risk_min_free_to_trade_usd).
            //
            // BUY-ONLY BY DESIGN: capital gates never block SELLs. A SELL is
            // a defensive exit / de-risk — it FREES capital and must always
            // be allowed to fill even when buying power is exhausted. Only
            // new BUYs (which CONSUME capital) are gated here and in
            // cash_check below. Do not add Side-agnostic capital checks.
            if (settings.MinFreeToTradeUsd > 0
                && string.Equals(order.Broker, "T212_DEMO", StringComparison.OrdinalIgnoreCase)
                && string.Equals(order.Side, "BUY", StringComparison.OrdinalIgnoreCase))
            {
                try
                {
                    var cash = await _cashCache.GetAsync(ct);
                    if (cash.Free is decimal free)
                    {
                        context["t212_free"] = free;
                        if (free < (decimal)settings.MinFreeToTradeUsd)
                        {
                            failures.Add(new RiskFailure(
                                "buying_power_floor",
                                $"T212 demo free {free:C0} below minimum {settings.MinFreeToTradeUsd:C0} "
                                + "to trade — top up / reset the demo account"));
                        }
                    }
                }
                catch (Exception ex)
                {
                    _log.LogWarning(ex, "buying_power_floor failed to evaluate");
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

    // FX symbol = IG epic "CS.D.<PAIR>.<size>.IP" or a bare 6-letter
    // currency pair. Used by the market-hours gate.
    private static bool IsFxSymbol(string symbol)
    {
        var s = (symbol ?? "").ToUpperInvariant();
        if (s.StartsWith("CS.D."))
        {
            var parts = s.Split('.');
            if (parts.Length >= 4
                && System.Text.RegularExpressions.Regex.IsMatch(parts[2], "^[A-Z]{6}$"))
                return true;
        }
        return System.Text.RegularExpressions.Regex.IsMatch(s, "^[A-Z]{6}$");
    }

    // Spot FX is 24/5: opens Sun ~21:00 UTC, closes Fri ~21:00 UTC,
    // closed all Saturday. Wall-clock (UTC now), not the order timestamp.
    private static bool FxMarketOpenUtc(DateTime nowUtc) => nowUtc.DayOfWeek switch
    {
        DayOfWeek.Saturday => false,
        DayOfWeek.Sunday => nowUtc.Hour >= 21,
        DayOfWeek.Friday => nowUtc.Hour < 21,
        _ => true,
    };

    // US equity = T212 "AAPL_US_EQ" / any "<TICKER>_EQ" form. NOT FX
    // (6-letter pairs are caught by IsFxSymbol first). Bare tickers are
    // ambiguous (could be UK/other), so we only gate the explicit US_EQ
    // form the equity strategies actually emit — conservative on purpose.
    private static bool IsUsEquitySymbol(string symbol)
    {
        var s = (symbol ?? "").ToUpperInvariant();
        return s.EndsWith("_US_EQ") || s.EndsWith("_EQ");
    }

    private static readonly TimeZoneInfo? _nyTz = ResolveNyTz();
    private static TimeZoneInfo? ResolveNyTz()
    {
        foreach (var id in new[] { "America/New_York", "Eastern Standard Time" })
        {
            try { return TimeZoneInfo.FindSystemTimeZoneById(id); }
            catch { /* try next */ }
        }
        return null;
    }

    // US cash session: 09:30–16:00 America/New_York, weekdays. Converting
    // UTC→NY keeps it DST-correct without hardcoding the seasonal UTC
    // offset. Holidays are not modelled — a holiday order just queues,
    // which is a minor, safe degradation. If the tz database is missing
    // (shouldn't happen on the EC2 image), fall back to the EDT window so
    // we still gate the common (summer) case rather than failing open.
    private static bool UsEquityMarketOpenUtc(DateTime nowUtc)
    {
        DateTime ny;
        if (_nyTz is not null)
            ny = TimeZoneInfo.ConvertTimeFromUtc(
                DateTime.SpecifyKind(nowUtc, DateTimeKind.Utc), _nyTz);
        else
            ny = nowUtc.AddHours(-4); // EDT fallback

        if (ny.DayOfWeek is DayOfWeek.Saturday or DayOfWeek.Sunday) return false;
        var mins = ny.Hour * 60 + ny.Minute;
        return mins >= 9 * 60 + 30 && mins < 16 * 60;
    }

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
                'risk_min_free_to_trade_usd',
                'risk_fail_closed',
                'risk_sentiment_buy_veto_score',
                'risk_sentiment_max_age_minutes'
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
            // 0 disables the floor (operator opts in). When set, blocks new
            // BUYs whenever T212 demo free cash is below this — the local
            // backstop for buying-power exhaustion.
            MinFreeToTradeUsd: Parse("risk_min_free_to_trade_usd", 0.0),
            FailClosed: Parse("risk_fail_closed", true),
            // 0 disables the sentiment veto entirely (useful while the
            // local LLM is calibrating + we haven't seen real signal
            // quality yet — per the paper-eval methodology in
            // project_overnight_risk_options).
            SentimentBuyVetoScore: Parse("risk_sentiment_buy_veto_score", -0.5),
            SentimentMaxAgeMinutes: Parse("risk_sentiment_max_age_minutes", 60));
    }

    private async Task<(double score, string classification, double ageMinutes)?>
        ReadLatestSentimentAsync(string symbol)
    {
        var bare = symbol.Trim().ToUpperInvariant();
        var underscore = bare.IndexOf('_');
        if (underscore > 0) bare = bare[..underscore];
        await using var conn = await _db.OpenConnectionAsync();
        var row = await conn.QueryFirstOrDefaultAsync<(double score, string classification, DateTime scored_at_utc)?>(@"
            SELECT score, classification, scored_at_utc
            FROM sentiment_scores
            WHERE symbol = @bare OR symbol = @symbol
            ORDER BY scored_at_utc DESC
            LIMIT 1;",
            new { bare, symbol });
        if (row is null) return null;
        var age = (DateTime.UtcNow - row.Value.scored_at_utc).TotalMinutes;
        return (row.Value.score, row.Value.classification, age);
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
        double MinFreeToTradeUsd,
        bool FailClosed,
        double SentimentBuyVetoScore,
        int SentimentMaxAgeMinutes);
}

public sealed record RiskFailure(string Gate, string Reason);

public sealed record RiskCheckResult(
    bool Passed,
    IReadOnlyList<RiskFailure> Failures,
    IReadOnlyList<RiskFailure> Warnings,
    IReadOnlyDictionary<string, object> Context);

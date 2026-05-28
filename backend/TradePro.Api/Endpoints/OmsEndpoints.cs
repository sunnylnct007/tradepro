using TradePro.Api.Oms;

namespace TradePro.Api.Endpoints;

/// <summary>
/// /api/oms — Order Management System surface. Backs the OMS UI page
/// (Phase 2) + the daemon's intent push (Phase 1d). Every order the
/// platform ever places flows through these endpoints.
/// </summary>
public static class OmsEndpoints
{
    public static IEndpointRouteBuilder MapOmsEndpoints(this IEndpointRouteBuilder app)
    {
        var orders = app.MapGroup("/oms/orders").WithTags("OMS");

        // List orders. ?state=PENDING_APPROVAL,SUBMITTED filters; absent
        // = all states. Newest first.
        orders.MapGet("/", async (string? states, int? limit, IOmsService oms) =>
        {
            var stateList = string.IsNullOrWhiteSpace(states)
                ? null
                : states.Split(',', StringSplitOptions.RemoveEmptyEntries | StringSplitOptions.TrimEntries);
            return Results.Ok(new
            {
                orders = await oms.ListAsync(stateList, limit ?? 100),
            });
        });

        orders.MapGet("/{orderId:guid}", async (Guid orderId, IOmsService oms) =>
        {
            var o = await oms.GetAsync(orderId);
            return o is null ? Results.NotFound() : Results.Ok(o);
        });

        orders.MapGet("/{orderId:guid}/events", async (Guid orderId, IOmsService oms) =>
        {
            var events = await oms.ListEventsAsync(orderId);
            return Results.Ok(new { events });
        });

        // GET /api/oms/orders/{id}/audit — full decision audit chain.
        // Surfaces every gate + LLM call + state transition for one
        // order so the operator can answer "on what basis was this
        // approved/rejected" without joining tables manually. This is
        // the trust-before-breadth surface — the trader can't trade
        // on what they can't audit.
        orders.MapGet("/{orderId:guid}/audit", async (
            Guid orderId, IOmsService oms, Npgsql.NpgsqlDataSource db) =>
        {
            var order = await oms.GetAsync(orderId);
            if (order is null) return Results.NotFound();
            await using var conn = await db.OpenConnectionAsync();

            // OMS state-machine timeline (PENDING → APPROVED → SUBMITTED → …)
            var events = await oms.ListEventsAsync(orderId);

            // RiskGate decisions — anything from the C# gate stack
            // (blacklist / size_cap / velocity / cash_check / sentiment_negative).
            var riskEvents = (await Dapper.SqlMapper.QueryAsync(conn, @"
                SELECT id, occurred_at_utc, gate, decision, reason, detail_json::text AS detail_json
                FROM risk_events
                WHERE order_id = @orderId OR
                      (strategy_id = @strategy AND symbol = @symbol AND
                       occurred_at_utc BETWEEN @startUtc AND @endUtc)
                ORDER BY occurred_at_utc DESC
                LIMIT 50;",
                new
                {
                    orderId,
                    strategy = order.StrategyId,
                    symbol = order.Symbol,
                    startUtc = order.CreatedAtUtc.AddSeconds(-30),
                    endUtc = order.LastStateChangeAtUtc.AddSeconds(30),
                })).ToList();

            // LLM evaluations — any model call that touched this order
            // OR ran for the same (strategy, symbol) within the order's
            // lifetime. The widened window means a pre-order sentiment
            // score gets stitched in even if the LLM was called before
            // the enqueue.
            var llmEvals = (await Dapper.SqlMapper.QueryAsync(conn, @"
                SELECT id, occurred_at_utc, purpose, llm_url, llm_model,
                       source_tag, latency_ms, decision, confidence,
                       reasoning, detail_json::text AS detail_json
                FROM llm_evaluations
                WHERE order_id = @orderId OR
                      (strategy_id = @strategy AND symbol = @symbol AND
                       occurred_at_utc BETWEEN @startUtc AND @endUtc)
                ORDER BY occurred_at_utc DESC
                LIMIT 50;",
                new
                {
                    orderId,
                    strategy = order.StrategyId,
                    symbol = order.Symbol,
                    startUtc = order.CreatedAtUtc.AddSeconds(-60),
                    endUtc = order.LastStateChangeAtUtc.AddSeconds(60),
                })).ToList();

            return Results.Ok(new
            {
                order,
                events,
                riskEvents,
                llmEvals,
                summary = new
                {
                    nStateTransitions = events.Count(),
                    nRiskEvents = riskEvents.Count,
                    nLlmEvals = llmEvals.Count,
                    riskBlocks = riskEvents.Count(e => (string)((dynamic)e).decision != "ALLOWED"),
                    llmApprovals = llmEvals.Count(e => (string)((dynamic)e).decision == "APPROVE"),
                    llmRejections = llmEvals.Count(e => (string)((dynamic)e).decision == "REJECT"),
                },
            });
        });

        // POST /api/oms/orders/{id}/llm-evaluation — record an LLM
        // evaluation against this order. Called by the LLM approver
        // worker (Python or in-process) when it scores an order.
        // Recording is idempotent on (order_id, llm_model, occurred_at_utc±1s).
        orders.MapPost("/{orderId:guid}/llm-evaluation", async (
            Guid orderId, LlmEvalBody body, Npgsql.NpgsqlDataSource db) =>
        {
            await using var conn = await db.OpenConnectionAsync();
            // Resolve order context — strategy/symbol/side/qty/broker —
            // so the row is queryable standalone without joining
            // oms_orders later (orders may be archived).
            var order = await Dapper.SqlMapper.QueryFirstOrDefaultAsync(conn, @"
                SELECT strategy_id, symbol, side, qty, broker FROM oms_orders WHERE id = @orderId;",
                new { orderId });
            if (order is null) return Results.NotFound(new { error = "order not found" });
            var id = await Dapper.SqlMapper.QuerySingleAsync<Guid>(conn, @"
                INSERT INTO llm_evaluations
                    (order_id, strategy_id, symbol, side, qty, broker,
                     purpose, llm_url, llm_model, source_tag, latency_ms,
                     prompt, response_raw, decision, confidence, reasoning,
                     detail_json)
                VALUES
                    (@orderId, @strategy, @symbol, @side, @qty, @broker,
                     @purpose, @llmUrl, @llmModel, @sourceTag, @latencyMs,
                     @prompt, @responseRaw, @decision, @confidence, @reasoning,
                     @detail::jsonb)
                RETURNING id;",
                new
                {
                    orderId,
                    strategy = (string?)order.strategy_id,
                    symbol = (string?)order.symbol,
                    side = (string?)order.side,
                    qty = (decimal?)order.qty,
                    broker = (string?)order.broker,
                    purpose = body.Purpose ?? "approve_order",
                    llmUrl = body.LlmUrl ?? "",
                    llmModel = body.LlmModel ?? "",
                    sourceTag = body.SourceTag,
                    latencyMs = body.LatencyMs,
                    prompt = body.Prompt ?? "",
                    responseRaw = body.ResponseRaw ?? "",
                    decision = body.Decision ?? "ADVISE",
                    confidence = body.Confidence,
                    reasoning = body.Reasoning,
                    detail = body.DetailJson ?? "{}",
                });
            return Results.Ok(new { id });
        });

        // POST /api/llm-evaluations — record a free-standing LLM
        // evaluation (no order context yet). Used by sentiment_score
        // and any future pre-trade signal-time LLM call so every model
        // touch lands in the audit table, not just the ones tied to a
        // live order. The /api/oms/orders/{id}/llm-evaluation variant
        // above is for the LLM-as-approver path.
        app.MapPost("/llm-evaluations", async (
            LlmEvalBody body, Npgsql.NpgsqlDataSource db) =>
        {
            await using var conn = await db.OpenConnectionAsync();
            var id = await Dapper.SqlMapper.QuerySingleAsync<Guid>(conn, @"
                INSERT INTO llm_evaluations
                    (order_id, strategy_id, symbol, side, qty, broker,
                     purpose, llm_url, llm_model, source_tag, latency_ms,
                     prompt, response_raw, decision, confidence, reasoning,
                     detail_json)
                VALUES
                    (NULL, @strategy, @symbol, @side, @qty, @broker,
                     @purpose, @llmUrl, @llmModel, @sourceTag, @latencyMs,
                     @prompt, @responseRaw, @decision, @confidence, @reasoning,
                     @detail::jsonb)
                RETURNING id;",
                new
                {
                    strategy = body.StrategyId,
                    symbol = body.Symbol,
                    side = body.Side,
                    qty = body.Qty,
                    broker = body.Broker,
                    purpose = body.Purpose ?? "sentiment_score",
                    llmUrl = body.LlmUrl ?? "",
                    llmModel = body.LlmModel ?? "",
                    sourceTag = body.SourceTag,
                    latencyMs = body.LatencyMs,
                    prompt = body.Prompt ?? "",
                    responseRaw = body.ResponseRaw ?? "",
                    decision = body.Decision ?? "ADVISE",
                    confidence = body.Confidence,
                    reasoning = body.Reasoning,
                    detail = body.DetailJson ?? "{}",
                });
            return Results.Ok(new { id });
        });

        // GET /api/llm-evaluations/recent — histogram + recent rows for
        // the connectivity / observability dashboard. Use limit to bound
        // payload; default 50. Optional filter by decision (APPROVE /
        // REJECT / ADVISE / ERROR) for the audit list view.
        app.MapGet("/llm-evaluations/recent", async (
            int? limit, string? decision, Npgsql.NpgsqlDataSource db) =>
        {
            await using var conn = await db.OpenConnectionAsync();
            var lim = Math.Clamp(limit ?? 50, 1, 500);
            var rows = await Dapper.SqlMapper.QueryAsync(conn, @"
                SELECT id, occurred_at_utc, order_id, strategy_id, symbol,
                       side, qty, broker, purpose, llm_model, decision,
                       confidence, reasoning, latency_ms
                FROM llm_evaluations
                WHERE (@decision IS NULL OR decision = @decision)
                ORDER BY occurred_at_utc DESC LIMIT @lim;",
                new { decision, lim });
            var counts = await Dapper.SqlMapper.QueryAsync<(string decision, int n)>(conn, @"
                SELECT decision, COUNT(*)::int AS n
                FROM llm_evaluations
                WHERE occurred_at_utc >= NOW() - INTERVAL '24 hours'
                GROUP BY decision;");
            return Results.Ok(new { rows, last24h = counts });
        });

        // Enqueue an intent. The daemon calls this after the strategy
        // emits orders. ClientOrderId from the caller doubles as the
        // idempotency key — retries with the same id return the same row.
        orders.MapPost("/", async (OrderIntent intent, HttpContext ctx, IOmsService oms) =>
        {
            if (intent.Qty <= 0)
                return Results.BadRequest(new { error = "qty must be > 0" });
            var actor = ResolveActor(ctx);
            try
            {
                var row = await oms.EnqueueAsync(intent, actor);
                return Results.Ok(row);
            }
            catch (Npgsql.PostgresException ex)
            {
                // CHECK constraint failure → 400 with a readable message
                // so the caller can fix the payload rather than seeing
                // a server-side 500.
                return Results.BadRequest(new { error = ex.MessageText });
            }
        });

        orders.MapPost("/{orderId:guid}/approve",
            async (Guid orderId, HttpContext ctx, IOmsService oms) =>
                await TransitionResult(() => oms.ApproveAsync(orderId, ResolveActor(ctx))));

        orders.MapPost("/{orderId:guid}/reject",
            async (Guid orderId, ReasonBody body, HttpContext ctx, IOmsService oms) =>
                await TransitionResult(() => oms.RejectAsync(orderId, ResolveActor(ctx), body.Reason)));

        orders.MapPost("/{orderId:guid}/cancel",
            async (Guid orderId, ReasonBody body, HttpContext ctx, IOmsService oms) =>
                await TransitionResult(() => oms.CancelAsync(orderId, ResolveActor(ctx), body.Reason)));

        // Record a fill. Called by the daemon's audit push (Phase 1d)
        // and, post-Phase 2, by the broker callback handler when a
        // real fill arrives. Idempotent at the OmsService layer via
        // FOR UPDATE on the parent row + delta math.
        orders.MapPost("/{orderId:guid}/fill",
            async (Guid orderId, FillBody body, HttpContext ctx, IOmsService oms) =>
            {
                if (body.Qty <= 0)
                    return Results.BadRequest(new { error = "qty must be > 0" });
                try
                {
                    var row = await oms.RecordFillAsync(
                        orderId,
                        body.Qty,
                        body.Price,
                        body.Fee,
                        string.IsNullOrWhiteSpace(body.Currency) ? "USD" : body.Currency,
                        body.BrokerFillId,
                        ResolveActor(ctx));
                    return Results.Ok(row);
                }
                catch (InvalidOperationException ex)
                {
                    return Results.Conflict(new { error = ex.Message });
                }
            });

        // Net positions derived from OMS fills. Strategies consume
        // this on session_start to seed their internal _fx_positions
        // so reruns don't double up on intents ("continuous
        // optimization" — task #28). Filter by ?strategyId= for a
        // single-strategy view; omit for everything.
        var positions = app.MapGroup("/oms/positions").WithTags("OMS");
        positions.MapGet("/", async (string? strategyId, IOmsService oms) =>
        {
            var rows = await oms.ListPositionsAsync(strategyId);
            return Results.Ok(new { positions = rows });
        });

        // Reconciliation: OMS-derived position vs T212 broker reality.
        // Drift = bug (T212 rejected something we recorded, or the
        // operator placed a manual trade outside the OMS). Surfaces
        // every (symbol) row with omsQty + t212Qty + diff, defaulting
        // to demo because that's where the trader's strategy is booking.
        // Task #29 — reconciliation; Phase 2 will run this on a timer
        // and alert on drift > threshold instead of pull-on-demand.
        positions.MapGet("/diff",
            async (
                string? strategyId,
                string? account,
                IOmsService oms,
                TradePro.Api.Providers.Trading212.Trading212PositionsCache liveCache,
                TradePro.Api.Providers.Trading212.Trading212DemoPositionsCache demoCache,
                TradePro.Api.Providers.Trading212.Trading212Client liveClient,
                TradePro.Api.Providers.Trading212.Trading212DemoClient demoClient,
                CancellationToken ct) =>
        {
            var useDemo = !string.Equals(account, "live", StringComparison.OrdinalIgnoreCase);
            var brokerLabel = useDemo ? "T212_DEMO" : "T212_LIVE";
            var omsRows = (await oms.ListPositionsAsync(strategyId))
                .Where(p => p.Broker == brokerLabel)
                .ToList();
            // Route through the same caches Portfolio uses so the drift
            // panel doesn't trip the 1 req/sec T212 limit.
            var t212 = useDemo
                ? await demoCache.GetAsync(ct)
                : await liveCache.GetAsync(ct);
            // Project T212 to a per-symbol dict. T212 uses tickers like
            // "AMZN_US_EQ"; the strategy stores plain "AMZN" — strip
            // the suffix here so the join works for the common case.
            // FX rows have no suffix today (post-015204a) so they pass
            // through as-is.
            var t212BySymbol = t212.Positions
                .GroupBy(p =>
                {
                    var t = p.Instrument?.Ticker ?? p.Ticker ?? "";
                    var underscore = t.IndexOf('_');
                    return underscore > 0 ? t[..underscore] : t;
                })
                .ToDictionary(g => g.Key, g => g.Sum(p => p.Quantity));

            // Union of (symbol) across both sources so a one-sided
            // position (OMS has it, T212 doesn't, or vice versa) is
            // visible as a non-zero diff instead of silently dropping.
            var omsBySymbol = omsRows
                .GroupBy(r => r.Symbol)
                .ToDictionary(g => g.Key, g => g.Sum(r => r.Quantity));
            var allSymbols = omsBySymbol.Keys.Union(t212BySymbol.Keys).OrderBy(s => s).ToList();
            var rows = allSymbols.Select(sym => new
            {
                symbol = sym,
                omsQty = omsBySymbol.GetValueOrDefault(sym, 0),
                t212Qty = t212BySymbol.GetValueOrDefault(sym, 0),
                diff = omsBySymbol.GetValueOrDefault(sym, 0) - t212BySymbol.GetValueOrDefault(sym, 0),
            }).ToList();
            var drifted = rows.Count(r => r.diff != 0);
            return Results.Ok(new
            {
                account = useDemo ? "demo" : "live",
                strategyId,
                brokerEnabled = useDemo ? demoClient.IsEnabled : liveClient.IsEnabled,
                t212Error = t212.Error,
                fetchedAtUtc = DateTime.UtcNow,
                totalSymbols = rows.Count,
                drifted,
                rows,
            });
        });

        // ── mode toggle ───────────────────────────────────────────
        var mode = app.MapGroup("/oms/mode").WithTags("OMS");

        mode.MapGet("/", (IOmsModeService svc) =>
            Results.Ok(new { mode = svc.Current.ToString().ToLowerInvariant() }));

        mode.MapPost("/", async (ModeBody body, HttpContext ctx, IOmsModeService svc) =>
        {
            if (!Enum.TryParse<OmsMode>(body.Mode, ignoreCase: true, out var target))
                return Results.BadRequest(new { error = "mode must be 'auto' or 'manual'" });
            var prior = svc.Current;
            var now = await svc.SetAsync(target, ResolveActor(ctx));
            return Results.Ok(new
            {
                mode = now.ToString().ToLowerInvariant(),
                prior = prior.ToString().ToLowerInvariant(),
            });
        });

        return app;
    }

    private static async Task<IResult> TransitionResult(Func<Task<OmsOrder>> action)
    {
        try
        {
            var row = await action();
            return Results.Ok(row);
        }
        catch (InvalidOperationException ex)
        {
            // State-machine guard tripped (wrong prior state) — return
            // 409 Conflict so the UI can re-fetch and re-render rather
            // than treating it as a generic 500.
            return Results.Conflict(new { error = ex.Message });
        }
    }

    private static string ResolveActor(HttpContext ctx) =>
        ctx.User?.Identity?.Name
        ?? ctx.Request.Headers["X-User"].FirstOrDefault()
        ?? "anonymous";

    public sealed record ReasonBody(string Reason);
    public sealed record ModeBody(string Mode);
    public sealed record FillBody(
        decimal Qty,
        decimal Price,
        decimal Fee = 0,
        string? Currency = null,
        string? BrokerFillId = null
    );

    public sealed record LlmEvalBody(
        string? Purpose,
        string? LlmUrl,
        string? LlmModel,
        string? SourceTag,
        int? LatencyMs,
        string? Prompt,
        string? ResponseRaw,
        string? Decision,
        decimal? Confidence,
        string? Reasoning,
        string? DetailJson,
        // Optional context — populated when the eval is a pre-trade
        // signal-time call (no order yet). Order-tied evals get
        // these from oms_orders inside the handler.
        string? StrategyId = null,
        string? Symbol = null,
        string? Side = null,
        decimal? Qty = null,
        string? Broker = null
    );
}

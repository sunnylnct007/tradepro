using Dapper;
using Npgsql;
using TradePro.Api.Oms;
using TradePro.Api.Providers.Trading212;

namespace TradePro.Api.Endpoints;

/// <summary>
/// /api/admin/* — Raw-table browser for IT investigation.
/// Every Postgres table the platform writes to is surfaced here so
/// an IT operator can diagnose data anomalies without a psql prompt.
///
/// All endpoints require AllowedUsers auth (same as the rest of /api).
/// Rows are returned newest-first; ?limit caps the result set.
/// </summary>
public static class AdminEndpoints
{
    public static IEndpointRouteBuilder MapAdminEndpoints(this IEndpointRouteBuilder app)
    {
        var g = app.MapGroup("/admin").WithTags("Admin");

        // ── events table ──────────────────────────────────────────
        // Generic domain event log — every order_emitted, fill_received,
        // risk decision, heartbeat, etc. Filterable by event_type.
        g.MapGet("/events", async (
            NpgsqlDataSource db,
            string? event_type,
            long? since_seq,
            long? before_seq,
            int? limit) =>
        {
            await using var conn = await db.OpenConnectionAsync();
            var rows = await conn.QueryAsync(@"
                SELECT seq, event_type, aggregate_id, payload::text AS payload_text, occurred_at
                FROM events
                WHERE (@event_type IS NULL OR event_type = @event_type)
                  AND (@since_seq  IS NULL OR seq > @since_seq)
                  AND (@before_seq IS NULL OR seq < @before_seq)
                ORDER BY seq DESC
                LIMIT @limit",
                new { event_type, since_seq, before_seq, limit = Math.Min(limit ?? 200, 1000) });
            return Results.Ok(new { rows = rows.AsList() });
        });

        // ── orders table ──────────────────────────────────────────
        // Append-only intent log. Every order any strategy ever emitted.
        g.MapGet("/orders", async (
            NpgsqlDataSource db,
            string? symbol,
            string? strategy,
            string? mode,
            int? limit) =>
        {
            await using var conn = await db.OpenConnectionAsync();
            var rows = await conn.QueryAsync(@"
                SELECT order_id, correlation_id, strategy_name, strategy_version, mode,
                       broker, symbol, side, quantity::float8, order_type,
                       limit_price::float8, bar_at_emit_close::float8, bar_at_emit_time,
                       tag, emitted_at_utc, risk_decision, risk_reason, risk_decided_at
                FROM orders
                WHERE (@symbol   IS NULL OR symbol        ILIKE '%' || @symbol   || '%')
                  AND (@strategy IS NULL OR strategy_name ILIKE '%' || @strategy || '%')
                  AND (@mode     IS NULL OR mode = @mode)
                ORDER BY emitted_at_utc DESC
                LIMIT @limit",
                new { symbol, strategy, mode, limit = Math.Min(limit ?? 200, 1000) });
            return Results.Ok(new { rows = rows.AsList() });
        });

        // ── fills table ───────────────────────────────────────────
        // Execution fills — one row per partial or full fill.
        g.MapGet("/fills", async (
            NpgsqlDataSource db,
            string? order_id,
            int? limit) =>
        {
            await using var conn = await db.OpenConnectionAsync();
            var rows = await conn.QueryAsync(@"
                SELECT fill_id, order_id, broker_order_id,
                       fill_qty::float8, fill_price::float8, commission::float8,
                       filled_at_utc, bar_at_fill_close::float8, bar_at_fill_time
                FROM fills
                WHERE (@order_id IS NULL OR order_id = @order_id)
                ORDER BY filled_at_utc DESC
                LIMIT @limit",
                new { order_id, limit = Math.Min(limit ?? 200, 1000) });
            return Results.Ok(new { rows = rows.AsList() });
        });

        // ── oms_order_events table ────────────────────────────────
        // Full audit trail for every OMS state transition.
        g.MapGet("/oms-events", async (
            NpgsqlDataSource db,
            string? order_id,
            int? limit) =>
        {
            await using var conn = await db.OpenConnectionAsync();
            var rows = await conn.QueryAsync(@"
                SELECT id, order_id, event_type, prior_state, new_state,
                       actor, detail_json, occurred_at_utc
                FROM oms_order_events
                WHERE (@order_id IS NULL OR order_id::text = @order_id)
                ORDER BY occurred_at_utc DESC
                LIMIT @limit",
                new { order_id, limit = Math.Min(limit ?? 200, 1000) });
            return Results.Ok(new { rows = rows.AsList() });
        });

        // ── strategy_versions table ───────────────────────────────
        // Registry of every strategy the Mac has ever registered.
        g.MapGet("/strategy-versions", async (NpgsqlDataSource db) =>
        {
            await using var conn = await db.OpenConnectionAsync();
            var rows = await conn.QueryAsync(@"
                SELECT name, version, code_hash, layer, description,
                       registered_at, deprecated_at
                FROM strategy_versions
                ORDER BY registered_at DESC
                LIMIT 500");
            return Results.Ok(new { rows = rows.AsList() });
        });

        // POST /api/admin/oms/bulk-cancel-pending — bulk-cancel every
        // PENDING_APPROVAL order matching the filter. Used to clear
        // backlogs when the operator switches from manual to auto
        // placement and doesn't want the old pending intents to fire.
        //
        // body: { strategyPrefix?: string, broker?: string,
        //         reason?: string }
        // Matches strategy_id LIKE @prefix% AND broker = @broker
        // when supplied; cancels all PENDING_APPROVAL otherwise.
        g.MapPost("/oms/bulk-cancel-pending", async (
            BulkCancelBody? body,
            HttpContext ctx,
            NpgsqlDataSource db) =>
        {
            await using var conn = await db.OpenConnectionAsync();
            var actor = ctx.User?.Identity?.Name ?? "admin-bulk-cancel";
            var reason = body?.Reason ?? "bulk_cancel_admin";
            var strategyLike = string.IsNullOrWhiteSpace(body?.StrategyPrefix)
                ? "%" : body!.StrategyPrefix + "%";
            var broker = string.IsNullOrWhiteSpace(body?.Broker) ? null : body!.Broker;

            // Pull the matching pending order ids first so we can audit
            // each individually + record the OMS state-machine event.
            // (Using oms.CancelAsync would loop one-by-one; we do this
            // in SQL because a 60-row UPDATE-then-loop is acceptable
            // for an admin call but a single SQL is cleanest.)
            var ids = (await conn.QueryAsync<Guid>(@"
                SELECT id FROM oms_orders
                WHERE state = 'PENDING_APPROVAL'
                  AND strategy_id LIKE @strategyLike
                  AND (@broker IS NULL OR broker = @broker);",
                new { strategyLike, broker })).ToList();

            if (ids.Count == 0)
                return Results.Ok(new { cancelled = 0, ids = Array.Empty<Guid>(), note = "no matching pending orders" });

            await using var tx = await conn.BeginTransactionAsync();
            try
            {
                await conn.ExecuteAsync(@"
                    UPDATE oms_orders
                    SET state = 'CANCELLED',
                        cancelled_reason = @reason,
                        last_state_change_at_utc = NOW()
                    WHERE id = ANY(@ids);",
                    new { reason, ids = ids.ToArray() }, transaction: tx);
                await conn.ExecuteAsync(@"
                    INSERT INTO oms_order_events
                        (order_id, occurred_at_utc, event_type, prior_state, new_state,
                         actor, detail)
                    SELECT id, NOW(), 'cancel', 'PENDING_APPROVAL', 'CANCELLED',
                           @actor, jsonb_build_object('bulk', true, 'reason', @reason::text)
                    FROM oms_orders WHERE id = ANY(@ids);",
                    new { actor, reason, ids = ids.ToArray() }, transaction: tx);
                await tx.CommitAsync();
            }
            catch
            {
                await tx.RollbackAsync();
                throw;
            }

            return Results.Ok(new
            {
                cancelled = ids.Count,
                strategyPrefix = body?.StrategyPrefix,
                broker = body?.Broker,
                actor,
                reason,
                ids,
            });
        });

        // POST /api/admin/oms/reconcile-from-t212-demo — for every T212
        // demo position that has no matching OMS position (oms drifted
        // because the broker_not_found_assume_terminal poller bug
        // falsely cancelled fills), create a synthetic FILLED order so
        // OMS knows about the holding. Audit trail is preserved:
        // strategy_id = body.StrategyId (default 'reconcile_from_broker')
        // and the order_event carries reason='reconcile_from_broker'.
        //
        // Idempotent: skips any (symbol, broker) where OMS already has
        // a non-zero position. Only adds rows for genuine drift.
        g.MapPost("/oms/reconcile-from-t212-demo", async (
            ReconcileBody? body,
            HttpContext ctx,
            Trading212DemoClient demoClient,
            Trading212DemoPositionsCache demoCache,
            NpgsqlDataSource db,
            CancellationToken ct) =>
        {
            // Prefer the cache (same path the cockpit/portfolio use)
            // — direct demoClient calls were intermittently hitting
            // T212's 429 rate limit on admin calls and returning 0,
            // leaving the user unable to reconcile. The cache is
            // already-warmed by the live UI so it has fresh data.
            var positionsResult = await demoCache.GetAsync(ct);
            var positions = positionsResult.Positions ?? new List<Trading212Position>();
            if (positions.Count == 0)
            {
                // Cold cache fallback — try the client directly once.
                positionsResult = await demoClient.GetPositionsAsync(ct);
                positions = positionsResult.Positions ?? new List<Trading212Position>();
            }
            var strategyId = body?.StrategyId ?? "reconcile_from_broker";
            var broker = body?.Broker ?? "T212_DEMO";
            var actor = ctx.User?.Identity?.Name ?? "admin-reconcile";

            await using var conn = await db.OpenConnectionAsync();

            // Build a set of (symbol → existing oms net qty) so we only
            // insert for symbols where OMS truly has nothing. Symbols
            // already covered by a live OMS row stay untouched.
            var omsHeld = (await Dapper.SqlMapper.QueryAsync<(string symbol, decimal qty)>(conn, @"
                SELECT symbol,
                       COALESCE(SUM(CASE WHEN side = 'BUY' THEN filled_qty
                                         WHEN side = 'SELL' THEN -filled_qty
                                         ELSE 0 END), 0) AS qty
                FROM oms_orders
                WHERE broker = @broker AND state = 'FILLED'
                GROUP BY symbol;",
                new { broker })).ToDictionary(r => r.symbol, r => r.qty);

            var created = new List<object>();
            var skipped = new List<object>();
            foreach (var p in positions)
            {
                // T212's /equity/positions nests the ticker inside
                // `instrument` instead of putting it at the top level.
                // Use Instrument.Ticker as the authoritative source.
                var ticker = !string.IsNullOrWhiteSpace(p.Ticker)
                    ? p.Ticker : p.Instrument?.Ticker;
                if (string.IsNullOrWhiteSpace(ticker)) continue;
                if (p.Quantity <= 0m) continue;
                var omsQty = omsHeld.TryGetValue(ticker, out var q) ? q : 0m;
                var drift = p.Quantity - omsQty;
                if (drift <= 0.0001m) { skipped.Add(new { Ticker = ticker, reason = "no drift", omsQty, brokerQty = p.Quantity }); continue; }

                // Synthetic FILLED order with side=BUY for the drift qty.
                // Price = average_price_paid (broker's record).
                var orderId = Guid.NewGuid();
                var clientOrderId = Guid.NewGuid();
                var fillPrice = p.AveragePricePaid ?? 0m;
                await using var tx = await conn.BeginTransactionAsync();
                try
                {
                    await conn.ExecuteAsync(@"
                        INSERT INTO oms_orders
                            (id, client_order_id, broker, strategy_id, symbol, side,
                             qty, order_type, time_in_force, state, placed_by,
                             filled_qty, avg_fill_price,
                             created_at_utc, last_state_change_at_utc)
                        VALUES
                            (@id, @clientOrderId, @broker, @strategyId, @symbol, 'BUY',
                             @qty, 'MKT', 'GTC', 'FILLED', 'STRATEGY_AUTO',
                             @qty, @price,
                             NOW(), NOW());",
                        new { id = orderId, clientOrderId, broker, strategyId,
                              symbol = ticker, qty = drift, price = fillPrice }, transaction: tx);
                    await conn.ExecuteAsync(@"
                        INSERT INTO oms_order_events
                            (order_id, occurred_at_utc, event_type, prior_state, new_state,
                             actor, detail)
                        VALUES
                            (@id, NOW(), 'reconcile', NULL, 'FILLED', @actor,
                             jsonb_build_object('reason', 'reconcile_from_broker',
                                                'broker_qty', @brokerQty::numeric,
                                                'oms_qty_before', @omsQty::numeric,
                                                'drift', @drift::numeric));",
                        new { id = orderId, actor,
                              brokerQty = p.Quantity, omsQty, drift }, transaction: tx);
                    await tx.CommitAsync();
                    created.Add(new { symbol = ticker, qty = drift, price = fillPrice, orderId });
                }
                catch
                {
                    await tx.RollbackAsync();
                    throw;
                }
            }

            return Results.Ok(new
            {
                broker, strategyId, actor,
                created = created.Count,
                skipped = skipped.Count,
                rows = new { created, skipped },
            });
        });

        // POST /api/admin/universes/sync-held-t212-demo — mirror the
        // T212 demo current holdings into a synthetic universe called
        // "held_t212_demo" so the comparator + strategies always have
        // a signal for every owned position. Otherwise the Today/Swing
        // columns on Portfolio show "—" for held symbols and the
        // strategy ignores held positions entirely (can't HOLD/SELL
        // them since they're not in any tracked universe). Re-runnable;
        // each call wipes + re-inserts.
        g.MapPost("/universes/sync-held-t212-demo", async (
            Trading212DemoClient demoClient,
            Trading212DemoPositionsCache demoCache,
            NpgsqlDataSource db,
            CancellationToken ct) =>
        {
            // Prefer the cache (same path the cockpit/portfolio use)
            // — direct demoClient calls were intermittently hitting
            // T212's 429 rate limit on admin calls and returning 0,
            // leaving the user unable to reconcile. The cache is
            // already-warmed by the live UI so it has fresh data.
            var positionsResult = await demoCache.GetAsync(ct);
            var positions = positionsResult.Positions ?? new List<Trading212Position>();
            if (positions.Count == 0)
            {
                // Cold cache fallback — try the client directly once.
                positionsResult = await demoClient.GetPositionsAsync(ct);
                positions = positionsResult.Positions ?? new List<Trading212Position>();
            }
            // Strip the T212 suffix (AAPL_US_EQ → AAPL) so the universe
            // matches the bare-ticker convention every other universe
            // uses (and so the comparator's symbol-key matches).
            var bareTickers = positions
                .Select(p => !string.IsNullOrWhiteSpace(p.Ticker) ? p.Ticker : p.Instrument?.Ticker)
                .Where(t => !string.IsNullOrWhiteSpace(t))
                .Select(t =>
                {
                    var s = t!.ToUpperInvariant();
                    var idx = s.IndexOf('_');
                    return idx > 0 ? s[..idx] : s;
                })
                .Distinct()
                .ToList();

            await using var conn = await db.OpenConnectionAsync();
            await using var tx = await conn.BeginTransactionAsync();
            try
            {
                const string name = "held_t212_demo";
                await conn.ExecuteAsync(@"
                    DELETE FROM universe_symbols WHERE universe_name = @name;
                    DELETE FROM universes WHERE name = @name;",
                    new { name }, transaction: tx);
                await conn.ExecuteAsync(@"
                    INSERT INTO universes (name, source_url, fetched_at_utc, symbol_count, source)
                    VALUES (@name, '', NOW(), @count, 'auto_t212_demo');",
                    new { name, count = bareTickers.Count }, transaction: tx);
                if (bareTickers.Count > 0)
                {
                    await conn.ExecuteAsync(@"
                        INSERT INTO universe_symbols (universe_name, ticker, name, sector, industry)
                        VALUES (@universe, @ticker, NULL, NULL, NULL)
                        ON CONFLICT (universe_name, ticker) DO NOTHING;",
                        bareTickers.Select(t => new { universe = name, ticker = t }),
                        transaction: tx);
                }
                await tx.CommitAsync();
            }
            catch
            {
                await tx.RollbackAsync();
                throw;
            }
            return Results.Ok(new
            {
                universe = "held_t212_demo",
                symbolCount = bareTickers.Count,
                symbols = bareTickers,
            });
        });

        // GET /api/admin/ig/search?term=EURUSD — IG /markets searchTerm
        // proxy so the operator can discover the correct epic from
        // anywhere (UI, curl, follow-up automation). Returns the matches
        // IG returns, untouched.
        g.MapGet("/ig/search", async (
            string? term,
            TradePro.Api.Providers.IG.IGClient ig,
            CancellationToken ct) =>
        {
            if (string.IsNullOrWhiteSpace(term))
                return Results.BadRequest(new { error = "term required" });
            if (!ig.IsEnabled)
                return Results.BadRequest(new { error = "IG disabled" });
            var result = await ig.SearchMarketsAsync(term, ct);
            return Results.Ok(new { term, matches = result.Matches, error = result.Error });
        });

        // POST /api/admin/ig/smoke-order — verify the IG enqueue →
        // approve → place → confirm chain end-to-end without waiting
        // for a strategy session. Body: { epic, side, size }. The
        // order goes through the SAME OMS path that strategy orders
        // use (PostgresOmsService.EnqueueAsync + ApproveAsync), so a
        // successful smoke test proves the real chain works.
        g.MapPost("/ig/smoke-order", async (
            IGSmokeOrderBody body,
            HttpContext ctx,
            IOmsService oms,
            CancellationToken ct) =>
        {
            if (string.IsNullOrWhiteSpace(body.Epic))
                return Results.BadRequest(new { error = "epic required" });
            if (string.IsNullOrWhiteSpace(body.Side))
                return Results.BadRequest(new { error = "side required (BUY/SELL)" });
            if (body.Size <= 0m)
                return Results.BadRequest(new { error = "size must be > 0" });

            var actor = ctx.User?.Identity?.Name ?? "admin-smoke";
            var clientId = Guid.NewGuid();
            var intent = new OrderIntent(
                ClientOrderId: clientId,
                Broker: "IG_DEMO",
                Symbol: body.Epic,
                Side: body.Side.ToUpperInvariant(),
                Qty: body.Size,
                OrderType: "MKT",
                StrategyId: "smoke_test_ig");
            try
            {
                var enq = await oms.EnqueueAsync(intent, actor);
                var done = await oms.ApproveAsync(enq.Id, actor);
                return Results.Ok(new
                {
                    orderId = done.Id,
                    state = done.State,
                    brokerOrderId = done.BrokerOrderId,
                    cancelledReason = done.CancelledReason,
                });
            }
            catch (Exception ex)
            {
                return Results.BadRequest(new
                {
                    error = "smoke order failed",
                    detail = ex.Message,
                });
            }
        });

        // POST /api/admin/t212/smoke-order — verify the T212 demo
        // enqueue → approve → place → fill chain end-to-end without
        // waiting for a strategy session. Mirror of the IG version.
        // Body: { ticker, side, qty }. Routes through PostgresOmsService
        // same as a strategy order would.
        g.MapPost("/t212/smoke-order", async (
            T212SmokeOrderBody body,
            HttpContext ctx,
            IOmsService oms,
            CancellationToken ct) =>
        {
            if (string.IsNullOrWhiteSpace(body.Ticker))
                return Results.BadRequest(new { error = "ticker required (e.g. AAPL_US_EQ)" });
            if (string.IsNullOrWhiteSpace(body.Side))
                return Results.BadRequest(new { error = "side required (BUY/SELL)" });
            if (body.Qty <= 0m)
                return Results.BadRequest(new { error = "qty must be > 0" });

            var actor = ctx.User?.Identity?.Name ?? "admin-smoke";
            var clientId = Guid.NewGuid();
            var intent = new OrderIntent(
                ClientOrderId: clientId,
                Broker: "T212_DEMO",
                Symbol: body.Ticker,
                Side: body.Side.ToUpperInvariant(),
                Qty: body.Qty,
                OrderType: "MKT",
                StrategyId: "smoke_test_t212");
            try
            {
                var enq = await oms.EnqueueAsync(intent, actor);
                var done = await oms.ApproveAsync(enq.Id, actor);
                return Results.Ok(new
                {
                    orderId = done.Id,
                    state = done.State,
                    brokerOrderId = done.BrokerOrderId,
                    cancelledReason = done.CancelledReason,
                });
            }
            catch (Exception ex)
            {
                return Results.BadRequest(new
                {
                    error = "smoke order failed",
                    detail = ex.Message,
                });
            }
        });

        // ─── strategy_broker_map editor ────────────────────────────
        // The strategy → broker mapping lives in `strategy_broker_map`
        // (migration 021 + seeds in 024). TradePlanEndpoints.cs reads
        // it at order-approval time so an UPDATE here changes routing
        // for every subsequent order — handle with care.
        //
        // GET  /api/admin/strategy-broker-map  → full snapshot for UI
        // PUT  /api/admin/strategy-broker-map/{strategy_id}  → upsert
        // DELETE  /api/admin/strategy-broker-map/{strategy_id}
        //   → drop the row so the strategy falls back to
        //     app_settings_kv.default_broker (the "use global" action).
        //
        // The CHECK constraint in migration 025 guards the broker value
        // at the DB layer too; the API just gives a friendlier error
        // before the round-trip.

        // The set the API will accept. Must match the CHECK constraint
        // in migration 025 and the oms_orders.broker CHECK in 023 —
        // if you add a broker, update all three in the same PR.
        var validBrokers = new[]
        {
            "T212_DEMO", "T212_LIVE",
            "IBKR_PAPER", "IBKR_LIVE",
            "IG_DEMO", "IG_LIVE",
            "PAPER",
        };

        g.MapGet("/strategy-broker-map", async (NpgsqlDataSource db) =>
        {
            await using var conn = await db.OpenConnectionAsync();
            var rows = await conn.QueryAsync(@"
                SELECT strategy_id, broker, account_id, note,
                       updated_at_utc, updated_by
                FROM strategy_broker_map
                ORDER BY strategy_id;");

            // Best-effort default_broker lookup — it's a free-form
            // setting in app_settings_kv; if absent we report null and
            // the UI shows ""(unset)"".
            var defaultBroker = await conn.ExecuteScalarAsync<string?>(@"
                SELECT value #>> '{}'
                FROM app_settings_kv
                WHERE key = 'default_broker';");

            return Results.Ok(new
            {
                validBrokers,
                defaultBroker,
                mappings = rows.AsList(),
            });
        });

        g.MapPut("/strategy-broker-map/{strategy_id}", async (
            string strategy_id,
            StrategyBrokerMapPutBody body,
            HttpContext ctx,
            NpgsqlDataSource db) =>
        {
            if (string.IsNullOrWhiteSpace(strategy_id))
                return Results.BadRequest(new { error = "strategy_id required in path" });
            if (string.IsNullOrWhiteSpace(body.Broker))
                return Results.BadRequest(new { error = "broker required in body" });
            if (!validBrokers.Contains(body.Broker, StringComparer.OrdinalIgnoreCase))
                return Results.BadRequest(new
                {
                    error = "invalid broker",
                    detail = $"broker must be one of: {string.Join(", ", validBrokers)}",
                });

            var actor = ctx.User?.Identity?.Name ?? "ui";
            var broker = body.Broker.ToUpperInvariant();
            await using var conn = await db.OpenConnectionAsync();

            // UPSERT — the migration seeds rows so most strategies
            // exist already; an admin call may also be adding a brand
            // new strategy. Both paths produce the same final state.
            await conn.ExecuteAsync(@"
                INSERT INTO strategy_broker_map
                    (strategy_id, broker, account_id, note,
                     updated_at_utc, updated_by)
                VALUES
                    (@strategy_id, @broker, @account_id, @note,
                     NOW(), @actor)
                ON CONFLICT (strategy_id) DO UPDATE
                SET broker         = EXCLUDED.broker,
                    account_id     = EXCLUDED.account_id,
                    note           = EXCLUDED.note,
                    updated_at_utc = NOW(),
                    updated_by     = EXCLUDED.updated_by;",
                new
                {
                    strategy_id,
                    broker,
                    account_id = string.IsNullOrWhiteSpace(body.AccountId)
                        ? null : body.AccountId,
                    note = string.IsNullOrWhiteSpace(body.Note)
                        ? null : body.Note,
                    actor,
                });

            // Round-trip the updated row so the UI can refresh without
            // a follow-up GET.
            var row = await conn.QuerySingleAsync(@"
                SELECT strategy_id, broker, account_id, note,
                       updated_at_utc, updated_by
                FROM strategy_broker_map
                WHERE strategy_id = @strategy_id;",
                new { strategy_id });
            return Results.Ok(new { row });
        });

        g.MapDelete("/strategy-broker-map/{strategy_id}", async (
            string strategy_id,
            HttpContext ctx,
            NpgsqlDataSource db) =>
        {
            if (string.IsNullOrWhiteSpace(strategy_id))
                return Results.BadRequest(new { error = "strategy_id required in path" });

            await using var conn = await db.OpenConnectionAsync();
            var rows = await conn.ExecuteAsync(@"
                DELETE FROM strategy_broker_map WHERE strategy_id = @strategy_id;",
                new { strategy_id });
            if (rows == 0)
                return Results.NotFound(new
                {
                    error = $"no mapping for '{strategy_id}' — already on global default",
                });
            return Results.Ok(new { deleted = strategy_id });
        });

        // POST /api/admin/oms/heal-false-cancels — retroactively flip
        // CANCELLED orders with reason 'broker_not_found_assume_terminal'
        // to FILLED. These are orders T212 actually filled but our
        // poller falsely cancelled — the position-tracking side is
        // already correct because broker is the golden source, but the
        // OMS UI shows the wrong terminal state. Run this once after
        // shipping the poller fix to clean up historical noise.
        //
        // Body: { olderThanMinutes?: int (default 60), broker?: string
        //         (default 'T212_DEMO') }.
        g.MapPost("/oms/heal-false-cancels", async (
            HealFalseCancelBody? body,
            HttpContext ctx,
            IOmsService oms,
            NpgsqlDataSource db) =>
        {
            var olderThan = Math.Clamp(body?.OlderThanMinutes ?? 60, 5, 1440);
            var broker = string.IsNullOrWhiteSpace(body?.Broker) ? "T212_DEMO" : body!.Broker;
            var actor = ctx.User?.Identity?.Name ?? "admin-heal";

            await using var conn = await db.OpenConnectionAsync();
            var falseCancels = (await conn.QueryAsync<(Guid id, decimal qty)>(@"
                SELECT id, qty FROM oms_orders
                WHERE state = 'CANCELLED'
                  AND cancelled_reason = 'broker_not_found_assume_terminal'
                  AND broker = @broker
                  AND last_state_change_at_utc <= NOW() - make_interval(mins => @olderThan);",
                new { broker, olderThan })).ToList();

            if (falseCancels.Count == 0)
                return Results.Ok(new { healed = 0, note = "no false-cancellations matching the filter" });

            await using var tx = await conn.BeginTransactionAsync();
            try
            {
                var ids = falseCancels.Select(r => r.id).ToArray();
                // Flip state + clear the false reason.
                await conn.ExecuteAsync(@"
                    UPDATE oms_orders
                    SET state = 'FILLED',
                        filled_qty = qty,
                        avg_fill_price = 0,
                        cancelled_reason = NULL,
                        last_state_change_at_utc = NOW()
                    WHERE id = ANY(@ids);",
                    new { ids }, transaction: tx);
                // Audit-log the heal so it's visible on the order trail.
                await conn.ExecuteAsync(@"
                    INSERT INTO oms_order_events
                        (order_id, occurred_at_utc, event_type, prior_state, new_state,
                         actor, detail)
                    SELECT id, NOW(), 'HEALED', 'CANCELLED', 'FILLED',
                           @actor,
                           jsonb_build_object(
                             'reason', 'broker_filled_per_position_truth',
                             'note', 'flipped from false-cancelled to filled — broker is the golden source')
                    FROM oms_orders WHERE id = ANY(@ids);",
                    new { ids, actor }, transaction: tx);
                await tx.CommitAsync();
            }
            catch
            {
                await tx.RollbackAsync();
                throw;
            }

            return Results.Ok(new
            {
                healed = falseCancels.Count,
                broker, olderThan,
                ids = falseCancels.Select(r => r.id),
            });
        });

        return app;
    }

    public sealed record StrategyBrokerMapPutBody(
        string Broker,
        string? AccountId,
        string? Note
    );

    public sealed record T212SmokeOrderBody(
        string Ticker,
        string Side,
        decimal Qty
    );

    public sealed record HealFalseCancelBody(
        int? OlderThanMinutes,
        string? Broker
    );

    public sealed record IGSmokeOrderBody(
        string Epic,
        string Side,
        decimal Size
    );

    public sealed record BulkCancelBody(
        string? StrategyPrefix,
        string? Broker,
        string? Reason
    );

    public sealed record ReconcileBody(
        string? StrategyId,
        string? Broker
    );
}

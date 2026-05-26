using Dapper;
using Npgsql;

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

        return app;
    }
}

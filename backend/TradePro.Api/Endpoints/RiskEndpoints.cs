using Dapper;
using Npgsql;

namespace TradePro.Api.Endpoints;

/// <summary>
/// /api/risk/* — read surface for the risk module.
///
/// Pre-trade gate evaluation happens inline in OmsService.ApproveAsync
/// (see Risk/RiskGate.cs). This endpoint surfaces the audit trail +
/// blacklist management for the /risk UI page.
///
/// Today-only by default per the no-clutter principle:
///   /events            → today's events (any decision)
///   /events?since=...  → explicit historical lookup
///   /events?decision=BLOCKED → today's blocks only (banner read)
/// </summary>
public static class RiskEndpoints
{
    public static IEndpointRouteBuilder MapRiskEndpoints(this IEndpointRouteBuilder app)
    {
        var group = app.MapGroup("/risk").WithTags("Risk");

        // GET /api/risk/events?decision=&since=&strategy=&limit=
        group.MapGet("/events", async (
            string? decision, DateTime? since, string? strategy, int? limit,
            NpgsqlDataSource db) =>
        {
            var lim = Math.Clamp(limit ?? 100, 1, 500);
            var sinceTs = since ?? DateTime.UtcNow.Date;
            var clauses = new List<string> { "occurred_at_utc >= @sinceTs" };
            var parms = new Dictionary<string, object> { ["sinceTs"] = sinceTs, ["lim"] = lim };
            if (!string.IsNullOrWhiteSpace(decision))
            {
                clauses.Add("decision = @decision");
                parms["decision"] = decision!.ToUpperInvariant();
            }
            if (!string.IsNullOrWhiteSpace(strategy))
            {
                clauses.Add("strategy_id = @strategy");
                parms["strategy"] = strategy!;
            }
            var where = "WHERE " + string.Join(" AND ", clauses);
            await using var conn = await db.OpenConnectionAsync();
            var rows = await conn.QueryAsync<RiskEventRow>($@"
                SELECT id, order_id AS OrderId,
                       strategy_id AS StrategyId, symbol, side, qty, broker,
                       decision, gate, reason,
                       detail_json::text AS DetailText,
                       occurred_at_utc AS OccurredAtUtc
                FROM risk_events
                {where}
                ORDER BY occurred_at_utc DESC
                LIMIT @lim;", parms);
            return Results.Ok(new
            {
                since = sinceTs,
                events = rows.Select(r => new
                {
                    id = r.Id, orderId = r.OrderId,
                    strategyId = r.StrategyId, symbol = r.Symbol,
                    side = r.Side, qty = r.Qty, broker = r.Broker,
                    decision = r.Decision, gate = r.Gate, reason = r.Reason,
                    occurredAtUtc = r.OccurredAtUtc,
                    detail = string.IsNullOrEmpty(r.DetailText)
                        ? null : (object)Data.Stores.JsonbHelpers.FromJsonb(r.DetailText),
                }),
            });
        });

        // GET /api/risk/summary?since=
        // Aggregate of today's decisions for the cockpit chip — how
        // many blocks vs allowed, which gates fired most.
        group.MapGet("/summary", async (
            DateTime? since, NpgsqlDataSource db) =>
        {
            var sinceTs = since ?? DateTime.UtcNow.Date;
            await using var conn = await db.OpenConnectionAsync();
            var byDecision = await conn.QueryAsync<(string decision, int count)>(@"
                SELECT decision, COUNT(*) AS count
                FROM risk_events
                WHERE occurred_at_utc >= @sinceTs
                GROUP BY decision;", new { sinceTs });
            var byGate = await conn.QueryAsync<(string gate, int count)>(@"
                SELECT gate, COUNT(*) AS count
                FROM risk_events
                WHERE occurred_at_utc >= @sinceTs
                  AND decision = 'BLOCKED'
                GROUP BY gate
                ORDER BY count DESC;", new { sinceTs });
            return Results.Ok(new
            {
                since = sinceTs,
                byDecision = byDecision.ToDictionary(x => x.decision, x => x.count),
                blockedByGate = byGate.Select(x => new { gate = x.gate, count = x.count }),
            });
        });

        // ─── Symbol blacklist management ────────────────────────────────
        group.MapGet("/blacklist", async (NpgsqlDataSource db) =>
        {
            await using var conn = await db.OpenConnectionAsync();
            var rows = await conn.QueryAsync<BlacklistRow>(@"
                SELECT ticker, reason,
                       added_at_utc AS AddedAtUtc,
                       added_by AS AddedBy
                FROM symbol_blacklist
                ORDER BY added_at_utc DESC;");
            return Results.Ok(new { blacklist = rows });
        });

        group.MapPost("/blacklist", async (
            BlacklistEntry body, HttpContext ctx, NpgsqlDataSource db) =>
        {
            if (string.IsNullOrWhiteSpace(body.Ticker))
                return Results.BadRequest(new { error = "ticker required" });
            var who = ctx.User?.Identity?.Name ?? body.AddedBy ?? "ui";
            await using var conn = await db.OpenConnectionAsync();
            await conn.ExecuteAsync(@"
                INSERT INTO symbol_blacklist (ticker, reason, added_by)
                VALUES (@ticker, @reason, @who)
                ON CONFLICT (ticker) DO UPDATE
                SET reason = EXCLUDED.reason,
                    added_at_utc = NOW(),
                    added_by = EXCLUDED.added_by;",
                new { ticker = body.Ticker.Trim().ToUpperInvariant(),
                      reason = body.Reason, who });
            return Results.Ok(new { added = true, ticker = body.Ticker });
        });

        group.MapDelete("/blacklist/{ticker}", async (
            string ticker, NpgsqlDataSource db) =>
        {
            await using var conn = await db.OpenConnectionAsync();
            var n = await conn.ExecuteAsync(
                "DELETE FROM symbol_blacklist WHERE ticker = @ticker;",
                new { ticker = ticker.Trim().ToUpperInvariant() });
            return n > 0
                ? Results.Ok(new { removed = true })
                : Results.NotFound(new { error = $"no blacklist entry for {ticker}" });
        });

        return app;
    }

    private sealed record RiskEventRow(
        long Id, Guid? OrderId, string StrategyId, string Symbol,
        string Side, decimal Qty, string Broker,
        string Decision, string Gate, string Reason,
        string? DetailText, DateTime OccurredAtUtc);

    private sealed record BlacklistRow(
        string Ticker, string? Reason, DateTime AddedAtUtc, string AddedBy);

    public sealed record BlacklistEntry(
        string Ticker, string? Reason, string? AddedBy);
}

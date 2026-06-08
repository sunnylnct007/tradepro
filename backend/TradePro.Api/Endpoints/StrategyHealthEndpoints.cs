using Dapper;
using Npgsql;

namespace TradePro.Api.Endpoints;

/// <summary>
/// GET /api/strategies/health — per-strategy activity + outcome health so a
/// silently-stopped strategy is VISIBLE on the cockpit instead of buried in /tmp
/// logs (see feedback_surface_system_health_in_ui).
///
/// Source of truth is the ORDER stream (oms_orders) that the LIVE paper daemons
/// write — NOT strategy_decisions, which is only populated by the comparison /
/// trade-plan flow and goes stale even while the daemon trades happily (that
/// mistake made this very panel cry wolf: "not running" on a strategy that had
/// filled 11 orders that day). last_order = true last activity; today's
/// fills/cancels = the outcome.
///
///   status: healthy  — filled at least one order today
///           blocked  — placed orders today but ALL cancelled, none filled
///           idle     — traded recently (≤30h) but no fills today (no setup)
///           stale    — no orders in >30h — likely not trading / daemon down
///           unknown  — no orders on record at all
///
/// A true per-run heartbeat (so "ran but idle" is distinguishable from "down"
/// even with zero orders) is the follow-up; order-activity is the honest signal
/// we have without a daemon change.
///
/// Registered on the `api` group → map "/strategies/health" (NOT "/api/..").
/// </summary>
public static class StrategyHealthEndpoints
{
    private static readonly (string Id, string Label)[] Known =
    {
        ("ichimoku_equity", "Ichimoku Equity (T212 cash)"),
        ("intraday_flat",   "Intraday Flat (IG 24h CFD)"),
        ("ichimoku_fx_mr",  "Ichimoku FX MR (IG FX)"),
    };

    public static IEndpointRouteBuilder MapStrategyHealthEndpoints(this IEndpointRouteBuilder app)
    {
        app.MapGet("/strategies/health", async (NpgsqlDataSource db) =>
        {
            await using var conn = await db.OpenConnectionAsync();

            var rows = (await conn.QueryAsync<(string Id, DateTime? LastOrder, int Fills, int Cancels, int Pending, int TodayTotal)>(
                @"SELECT strategy_id AS Id,
                         MAX(created_at_utc) AS LastOrder,
                         COUNT(*) FILTER (WHERE state IN ('FILLED','PARTIALLY_FILLED') AND created_at_utc >= date_trunc('day', NOW()))::int AS Fills,
                         COUNT(*) FILTER (WHERE state = 'CANCELLED'        AND created_at_utc >= date_trunc('day', NOW()))::int AS Cancels,
                         COUNT(*) FILTER (WHERE state = 'PENDING_APPROVAL' AND created_at_utc >= date_trunc('day', NOW()))::int AS Pending,
                         COUNT(*) FILTER (WHERE created_at_utc >= date_trunc('day', NOW()))::int AS TodayTotal
                  FROM oms_orders
                  WHERE strategy_id IS NOT NULL
                  GROUP BY strategy_id"))
                .ToDictionary(r => r.Id, r => r);

            var nowUtc = DateTime.UtcNow;
            var ids = Known.Select(k => k.Id).Union(rows.Keys).Distinct();

            var strategies = ids.Select(id =>
            {
                var meta = Known.FirstOrDefault(k => k.Id == id);
                var label = meta.Id == id ? meta.Label : id;
                rows.TryGetValue(id, out var r);

                DateTime? last = r.LastOrder;
                double? minsSince = last.HasValue ? (nowUtc - last.Value).TotalMinutes : null;
                bool stale = !last.HasValue || minsSince > 30 * 60; // active daily; >30h ≈ not trading

                string status, reason;
                if (r.Fills > 0)
                {
                    status = "healthy";
                    reason = $"{r.Fills} filled today" + (r.Cancels > 0 ? $" ({r.Cancels} cancelled)" : "");
                }
                else if (r.TodayTotal > 0 && r.Cancels > 0)
                {
                    status = "blocked";
                    reason = $"{r.Cancels} order(s) today, ALL cancelled — none filled";
                }
                else if (!last.HasValue)
                {
                    status = "unknown";
                    reason = "no orders on record";
                }
                else if (stale)
                {
                    status = "stale";
                    reason = $"no orders in {FmtAge(minsSince!.Value)} — may not be trading";
                }
                else
                {
                    status = "idle";
                    reason = "traded recently, no fills today (no qualifying signal)";
                }

                return new
                {
                    strategy = id,
                    label,
                    status,
                    reason,
                    lastOrderUtc = last,
                    minutesSinceOrder = minsSince.HasValue ? Math.Round(minsSince.Value, 1) : (double?)null,
                    today = new { fills = r.Fills, cancels = r.Cancels, pending = r.Pending, total = r.TodayTotal },
                };
            }).OrderBy(r => r.strategy).ToList();

            return Results.Ok(new { generatedAtUtc = nowUtc, strategies });
        }).WithTags("StrategyHealth");

        return app;
    }

    private static string FmtAge(double mins)
    {
        if (mins < 60) return $"{mins:F0}m";
        if (mins < 60 * 48) return $"{mins / 60:F0}h";
        return $"{mins / 1440:F0}d";
    }
}

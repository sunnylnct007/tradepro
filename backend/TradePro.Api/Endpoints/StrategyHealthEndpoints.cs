using System.Text.RegularExpressions;
using Dapper;
using Npgsql;

namespace TradePro.Api.Endpoints;

/// <summary>
/// GET /api/strategies/health — per-strategy activity + outcome health so a
/// silently-stopped strategy is VISIBLE on the cockpit instead of buried in /tmp
/// logs (see feedback_surface_system_health_in_ui).
///
/// Source of truth is the ORDER stream (oms_orders) the LIVE paper daemons write
/// — NOT strategy_decisions (only the comparison/trade-plan flow writes that, so
/// it goes stale even while the daemon trades and made this panel cry wolf once).
///
/// IMPORTANT: oms_orders also carries PER-SYMBOL engine ids like
/// "intraday-ichimoku_equity-AAPL" / "intraday-bollinger_bounce-MSFT" — hundreds
/// of them. We AGGREGATE those into ONE row per base strategy (with a symbol
/// count) so the panel shows a handful of rows, not 500. The 3 main paper
/// strategies stay individual.
///
///   status: healthy  — filled at least one order today
///           blocked  — placed orders today but ALL cancelled, none filled
///           idle     — traded recently (≤30h) but no fills today
///           stale    — no orders in >30h — likely not trading
///           unknown  — no orders on record
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

    // Per-symbol engine id → base, e.g. "intraday-ichimoku_equity-AAPL" → "intraday-ichimoku_equity".
    private static readonly Regex PerSymbol = new(@"^(.*)-[A-Z0-9.]{1,6}$", RegexOptions.Compiled);

    private static string GroupOf(string id)
    {
        if (Known.Any(k => k.Id == id)) return id;        // main strategies keep their id
        var m = PerSymbol.Match(id);
        return m.Success ? m.Groups[1].Value : id;        // collapse per-symbol ids to their base
    }

    public static IEndpointRouteBuilder MapStrategyHealthEndpoints(this IEndpointRouteBuilder app)
    {
        app.MapGet("/strategies/health", async (NpgsqlDataSource db) =>
        {
            await using var conn = await db.OpenConnectionAsync();

            var raw = (await conn.QueryAsync<(string Id, DateTime? LastOrder, int Fills, int Cancels, int Pending, int TodayTotal)>(
                @"SELECT strategy_id AS Id,
                         MAX(created_at_utc) AS LastOrder,
                         COUNT(*) FILTER (WHERE state IN ('FILLED','PARTIALLY_FILLED') AND created_at_utc >= date_trunc('day', NOW()))::int AS Fills,
                         COUNT(*) FILTER (WHERE state = 'CANCELLED'        AND created_at_utc >= date_trunc('day', NOW()))::int AS Cancels,
                         COUNT(*) FILTER (WHERE state = 'PENDING_APPROVAL' AND created_at_utc >= date_trunc('day', NOW()))::int AS Pending,
                         COUNT(*) FILTER (WHERE created_at_utc >= date_trunc('day', NOW()))::int AS TodayTotal
                  FROM oms_orders
                  WHERE strategy_id IS NOT NULL
                  GROUP BY strategy_id")).ToList();

            var nowUtc = DateTime.UtcNow;

            // Aggregate by group (main strategies = themselves; per-symbol ids collapsed).
            var groups = raw.GroupBy(r => GroupOf(r.Id)).ToDictionary(g => g.Key, g => g);
            var ids = Known.Select(k => k.Id).Union(groups.Keys).Distinct();

            var strategies = ids.Select(id =>
            {
                groups.TryGetValue(id, out var g);
                var members = g?.ToList() ?? new();
                int symbolCount = members.Count;
                bool isMain = Known.Any(k => k.Id == id);
                string label = Known.FirstOrDefault(k => k.Id == id).Label
                               ?? (symbolCount > 1 ? $"{id} ({symbolCount} symbols)" : id);

                DateTime? last = members.Where(m => m.LastOrder.HasValue).Select(m => m.LastOrder!.Value)
                                        .DefaultIfEmpty().Max();
                if (last == default(DateTime)) last = null;
                double? minsSince = last.HasValue ? (nowUtc - last.Value).TotalMinutes : null;
                bool stale = !last.HasValue || minsSince > 30 * 60;

                int fills = members.Sum(m => m.Fills);
                int cancels = members.Sum(m => m.Cancels);
                int pending = members.Sum(m => m.Pending);
                int total = members.Sum(m => m.TodayTotal);

                string status, reason;
                if (fills > 0)
                {
                    status = "healthy";
                    reason = $"{fills} filled today" + (cancels > 0 ? $" ({cancels} cancelled)" : "");
                }
                else if (total > 0 && cancels > 0)
                {
                    status = "blocked";
                    reason = $"{cancels} order(s) today, ALL cancelled — none filled";
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
                    isMain,
                    symbolCount,
                    status,
                    reason,
                    lastOrderUtc = last,
                    minutesSinceOrder = minsSince.HasValue ? Math.Round(minsSince.Value, 1) : (double?)null,
                    today = new { fills, cancels, pending, total },
                };
            })
            // Main strategies first, then groups with recent activity, then the rest.
            .OrderByDescending(r => r.isMain)
            .ThenByDescending(r => r.today.fills)
            .ThenBy(r => r.strategy)
            .ToList();

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

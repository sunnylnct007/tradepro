using Dapper;
using Npgsql;

namespace TradePro.Api.Endpoints;

/// <summary>
/// GET /api/strategies/health — per-strategy run-timing + order-outcome health so a
/// silently-stopped strategy is VISIBLE on the cockpit instead of buried in /tmp
/// logs. (Built after a market-hours gate bug made ichimoku_equity skip every order
/// for hours with zero UI signal — see feedback_surface_system_health_in_ui.)
///
///   last run   = MAX(uploaded_at_utc) from strategy_decisions — every daemon run
///                uploads its decision set, so this is the true "did it run / when".
///   today      = fills / cancels / pending counts from oms_orders by state.
///   status     = derived so a problem reads at a glance:
///                  stale   — no run within 2× the expected cadence (daemon down?)
///                  blocked — ran, but today's orders are ALL cancelled, none filled
///                  idle    — ran recently, no orders (no qualifying signal — OK)
///                  healthy — ran recently + has fills today
///                  unknown — no decision runs on record yet
///
/// Registered on the `api` group, so map "/strategies/health" (NOT
/// "/api/strategies/health" — that double-prefixed the catalysts route once).
/// </summary>
public static class StrategyHealthEndpoints
{
    // Known strategies + expected run cadence (minutes). The paper daemons run ~15m.
    private static readonly (string Id, string Label, int CadenceMin)[] Known =
    {
        ("ichimoku_equity", "Ichimoku Equity (T212 cash)", 15),
        ("intraday_flat",   "Intraday Flat (IG 24h CFD)",  15),
        ("ichimoku_fx_mr",  "Ichimoku FX MR (IG FX)",      15),
    };

    public static IEndpointRouteBuilder MapStrategyHealthEndpoints(this IEndpointRouteBuilder app)
    {
        app.MapGet("/strategies/health", async (NpgsqlDataSource db) =>
        {
            await using var conn = await db.OpenConnectionAsync();

            var runs = (await conn.QueryAsync<(string Strategy, DateTime? LastRun, DateTime? LastAsOf)>(
                @"SELECT strategy, MAX(uploaded_at_utc) AS LastRun, MAX(as_of_utc) AS LastAsOf
                  FROM strategy_decisions GROUP BY strategy"))
                .ToDictionary(r => r.Strategy, r => r);

            var counts = (await conn.QueryAsync<(string? StrategyId, string State, int N)>(
                @"SELECT strategy_id, state, COUNT(*)::int AS N
                  FROM oms_orders
                  WHERE created_at_utc >= date_trunc('day', NOW())
                  GROUP BY strategy_id, state")).ToList();

            var nowUtc = DateTime.UtcNow;
            var ids = Known.Select(k => k.Id)
                .Union(runs.Keys)
                .Union(counts.Where(c => c.StrategyId != null).Select(c => c.StrategyId!))
                .Distinct();

            var strategies = ids.Select(id =>
            {
                var meta = Known.FirstOrDefault(k => k.Id == id);
                var cadence = meta.Id == id ? meta.CadenceMin : 15;
                var label = meta.Id == id ? meta.Label : id;

                runs.TryGetValue(id, out var run);
                DateTime? lastRun = run.LastRun;
                double? minsSince = lastRun.HasValue ? (nowUtc - lastRun.Value).TotalMinutes : null;

                int fills = counts.Where(c => c.StrategyId == id && (c.State == "FILLED" || c.State == "PARTIALLY_FILLED")).Sum(c => c.N);
                int cancels = counts.Where(c => c.StrategyId == id && c.State == "CANCELLED").Sum(c => c.N);
                int pending = counts.Where(c => c.StrategyId == id && c.State == "PENDING_APPROVAL").Sum(c => c.N);
                int total = counts.Where(c => c.StrategyId == id).Sum(c => c.N);

                string status, reason;
                if (!lastRun.HasValue) { status = "unknown"; reason = "no decision runs recorded yet"; }
                else if (minsSince > cadence * 2) { status = "stale"; reason = $"no run in {minsSince:F0} min (expected every {cadence}m) — daemon may be down"; }
                else if (total > 0 && fills == 0 && cancels > 0) { status = "blocked"; reason = $"{cancels} order(s) today, ALL cancelled — none filled"; }
                else if (total == 0) { status = "idle"; reason = "ran recently, no orders (no qualifying signal)"; }
                else { status = "healthy"; reason = $"{fills} filled today"; }

                return new
                {
                    strategy = id,
                    label,
                    status,
                    reason,
                    lastRunUtc = lastRun,
                    minutesSinceRun = minsSince.HasValue ? Math.Round(minsSince.Value, 1) : (double?)null,
                    cadenceMin = cadence,
                    today = new { fills, cancels, pending, total },
                };
            }).OrderBy(r => r.strategy).ToList();

            return Results.Ok(new { generatedAtUtc = nowUtc, strategies });
        }).WithTags("StrategyHealth");

        return app;
    }
}

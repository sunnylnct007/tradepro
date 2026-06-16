using System.Text.Json;
using TradePro.Api.Simulation;

namespace TradePro.Api.Endpoints;

/// Read-side of the compare flow. Authenticated frontend users fetch the
/// most recent ranked-comparison payload that the Mac pushed in.
public static class CompareEndpoints
{
    public static IEndpointRouteBuilder MapCompareEndpoints(this IEndpointRouteBuilder app)
    {
        var group = app.MapGroup("/compare").WithTags("Compare");

        group.MapGet("/universes", (ICompareStore store) =>
            Results.Ok(new { universes = store.ListUniverses() }));

        group.MapGet("/latest", (string universe, ICompareStore store) =>
        {
            if (string.IsNullOrWhiteSpace(universe))
                return Results.BadRequest(new { error = "universe is required" });

            var env = store.GetLatest(universe);
            if (env is null)
                return Results.NotFound(new { error = $"no compare payload pushed for universe '{universe}' yet" });

            // Return the original Python payload as-is, plus the receivedAt
            // timestamp the API stamped on ingest.
            return Results.Ok(new
            {
                universe = env.Universe,
                runId = env.RunId,
                generatedAtUtc = env.GeneratedAtUtc,
                receivedAtUtc = env.ReceivedAtUtc,
                rankMetric = env.RankMetric,
                rowCount = env.RowCount,
                payload = env.Payload,
            });
        });

        // CANONICAL VERDICT for ONE symbol, read straight from the cached
        // compare payloads the Decide page serves. This is the SAME data and
        // the SAME shared_verdict pipeline output Decide renders, so Research
        // (which calls this) and Decide can never contradict for a tracked
        // symbol. Scans every cached universe and returns the best-ranked row.
        // 404 when the symbol isn't in any compared universe — the caller then
        // falls back to the live single-symbol sidecar (any symbol, fresher,
        // but needs the Python sidecar running).
        group.MapGet("/verdict", (string symbol, ICompareStore store) =>
        {
            if (string.IsNullOrWhiteSpace(symbol))
                return Results.BadRequest(new { error = "symbol is required" });
            var want = symbol.Trim().ToUpperInvariant();

            JsonElement? best = null;
            double bestRank = double.PositiveInfinity;
            string? foundUniverse = null;
            DateTime? generatedAt = null;
            var attribution = new List<object>();

            foreach (var summary in store.ListUniverses())
            {
                var env = store.GetLatest(summary.Universe);
                if (env is null) continue;
                if (!env.Payload.TryGetProperty("rows", out var rows)
                    || rows.ValueKind != JsonValueKind.Array) continue;

                foreach (var row in rows.EnumerateArray())
                {
                    if (!string.Equals(Str(row, "symbol"), want, StringComparison.OrdinalIgnoreCase))
                        continue;
                    var rank = Num(row, "rank") ?? 1e9;
                    if (best is null || rank < bestRank)
                    {
                        best = row;
                        bestRank = rank;
                        foundUniverse = env.Universe;
                        generatedAt = env.GeneratedAtUtc;
                    }
                }
            }

            if (best is null)
                return Results.NotFound(new { error = $"'{want}' is not in any compared universe yet" });

            // Per-strategy attribution: every row for this symbol in the
            // winning universe (each strategy's own latest action + fit).
            var winEnv = store.GetLatest(foundUniverse!);
            if (winEnv is not null
                && winEnv.Payload.TryGetProperty("rows", out var winRows)
                && winRows.ValueKind == JsonValueKind.Array)
            {
                foreach (var row in winRows.EnumerateArray())
                {
                    if (!string.Equals(Str(row, "symbol"), want, StringComparison.OrdinalIgnoreCase))
                        continue;
                    var stats = row.TryGetProperty("stats", out var s) && s.ValueKind == JsonValueKind.Object
                        ? (JsonElement?)s : null;
                    attribution.Add(new
                    {
                        strategy = Str(row, "strategy"),
                        label = Str(row, "strategy_label") ?? Str(row, "strategy"),
                        action = Str(row, "current_action"),
                        in_position = Bool(row, "in_position"),
                        horizon = Str(row, "horizon"),
                        strategy_type = Str(row, "strategy_type"),
                        excluded_for_fit = Bool(row, "excluded_for_fit"),
                        sharpe = stats is null ? null : Num(stats.Value, "sharpe"),
                        cagr_pct = stats is null ? null : Num(stats.Value, "cagr_pct"),
                        total_return_pct = stats is null ? null : Num(stats.Value, "total_return_pct"),
                    });
                }
            }

            var bestRow = best.Value;
            var horizon = Str(bestRow, "horizon");
            return Results.Ok(new
            {
                symbol = want,
                fetched_at = generatedAt?.ToString("o"),
                bucket = Str(bestRow, "bucket"),
                bucket_reason = Str(bestRow, "bucket_reason"),
                conviction = Str(bestRow, "conviction"),
                conviction_reason = Str(bestRow, "conviction_reason"),
                horizon,
                horizon_label = HorizonLabel(horizon),
                long_count = Num(bestRow, "long_count"),
                total = Num(bestRow, "total"),
                earnings_suppressed = Bool(bestRow, "earnings_suppressed"),
                strategies = attribution,
                universe = foundUniverse,
                generatedAtUtc = generatedAt,
                _source = $"cached://compare/{foundUniverse}/{want}",
            });
        });

        return app;
    }

    // Friendly label for the comparator's per-strategy horizon token —
    // mirrors the sidecar's _HORIZON_LABEL so cached + live verdicts read
    // identically. The "swing / short / long" timeline the user asked for.
    private static string? HorizonLabel(string? horizon) => horizon switch
    {
        "intraday"    => "Intraday",
        "swing"       => "Swing (days–weeks)",
        "medium_term" => "Short-term (weeks–months)",
        "long_term"   => "Long-term (months–years)",
        _             => horizon,
    };

    private static string? Str(JsonElement el, string key)
        => el.TryGetProperty(key, out var v) && v.ValueKind == JsonValueKind.String
            ? v.GetString() : null;

    private static double? Num(JsonElement el, string key)
        => el.TryGetProperty(key, out var v) && v.ValueKind == JsonValueKind.Number
            ? v.GetDouble() : null;

    private static bool? Bool(JsonElement el, string key)
        => el.TryGetProperty(key, out var v)
            && (v.ValueKind == JsonValueKind.True || v.ValueKind == JsonValueKind.False)
            ? v.GetBoolean() : null;
}

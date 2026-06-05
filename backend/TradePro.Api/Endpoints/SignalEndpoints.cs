using TradePro.Api.Models;
using TradePro.Api.Simulation;

namespace TradePro.Api.Endpoints;

public static class SignalEndpoints
{
    public static IEndpointRouteBuilder MapSignalEndpoints(this IEndpointRouteBuilder app)
    {
        var group = app.MapGroup("/signals").WithTags("Signals");

        group.MapPost("/evaluate", async (
            SignalRequest req,
            ISignalEngine engine,
            ICatalystEnricher catalysts,
            CancellationToken ct) =>
        {
            if (string.IsNullOrWhiteSpace(req.Symbol))
                return Results.BadRequest(new { error = "symbol is required" });
            if (string.IsNullOrWhiteSpace(req.Strategy))
                return Results.BadRequest(new { error = "strategy is required" });
            try
            {
                var decision = await engine.EvaluateAsync(req, ct);
                // Phase C-3 — attach active catalysts + divergence
                // flag. Best-effort: if the registry query throws,
                // log + return the unenriched decision so the
                // technical verdict still reaches the trader.
                try
                {
                    decision = await catalysts.EnrichOneAsync(decision, ct);
                }
                catch (Exception)
                {
                    // Swallow — decision already has the technical
                    // payload, and an enricher failure must not
                    // black-hole the response.
                }
                return Results.Ok(decision);
            }
            catch (ArgumentException ex)
            {
                return Results.BadRequest(new { error = ex.Message });
            }
        });

        group.MapPost("/scan", async (
            ScanRequest req,
            ISignalScanner scanner,
            ICatalystEnricher catalysts,
            CancellationToken ct) =>
        {
            if (string.IsNullOrWhiteSpace(req.Strategy))
                return Results.BadRequest(new { error = "strategy is required" });
            try
            {
                var result = await scanner.ScanAsync(req, ct);
                // Phase C-3 — batch-enrich every decision in the
                // scan result (Buys + Sells + Holds). One query
                // covers the whole scan. Best-effort same as
                // /evaluate: swallow enricher failures so the
                // scan grid still renders the technical verdict.
                try
                {
                    result = await EnrichScanAsync(result, catalysts, ct);
                }
                catch (Exception)
                {
                    // see /evaluate comment
                }
                return Results.Ok(result);
            }
            catch (ArgumentException ex)
            {
                return Results.BadRequest(new { error = ex.Message });
            }
        });

        group.MapPost("/hitrate", async (HitRateRequest req, IHitRateEngine engine, CancellationToken ct) =>
        {
            if (string.IsNullOrWhiteSpace(req.Symbol))
                return Results.BadRequest(new { error = "symbol is required" });
            if (string.IsNullOrWhiteSpace(req.Strategy))
                return Results.BadRequest(new { error = "strategy is required" });
            try
            {
                var result = await engine.ComputeAsync(req, ct);
                return Results.Ok(result);
            }
            catch (ArgumentException ex)
            {
                return Results.BadRequest(new { error = ex.Message });
            }
        });

        return app;
    }

    /// <summary>Phase C-3 — batch-enrich every decision in a
    /// ScanResult. Single registry query covers Buys + Sells +
    /// Holds together so the cost is O(1) DB round-trip, not
    /// O(N) per-symbol queries.</summary>
    private static async Task<ScanResult> EnrichScanAsync(
        ScanResult result,
        ICatalystEnricher catalysts,
        CancellationToken ct)
    {
        // Flatten + remember each item's bucket + position so we
        // can reassemble the result with the same ordering.
        var flat = new List<(string Bucket, int Index, ScanResultItem Item)>();
        for (int i = 0; i < result.Buys.Count; i++)
            flat.Add(("buys", i, result.Buys[i]));
        for (int i = 0; i < result.Sells.Count; i++)
            flat.Add(("sells", i, result.Sells[i]));
        for (int i = 0; i < result.Holds.Count; i++)
            flat.Add(("holds", i, result.Holds[i]));

        if (flat.Count == 0) return result;

        var decisions = flat.Select(f => f.Item.Decision).ToList();
        var enriched = await catalysts.EnrichManyAsync(decisions, ct);

        // Pair enriched decisions back with their (bucket, index)
        // and rebuild the three lists preserving original order.
        var buys = new List<ScanResultItem>(new ScanResultItem[result.Buys.Count]);
        var sells = new List<ScanResultItem>(new ScanResultItem[result.Sells.Count]);
        var holds = new List<ScanResultItem>(new ScanResultItem[result.Holds.Count]);
        for (int i = 0; i < flat.Count; i++)
        {
            var (bucket, idx, item) = flat[i];
            var newItem = item with { Decision = enriched[i] };
            switch (bucket)
            {
                case "buys":  buys[idx] = newItem; break;
                case "sells": sells[idx] = newItem; break;
                case "holds": holds[idx] = newItem; break;
            }
        }
        return result with { Buys = buys, Sells = sells, Holds = holds };
    }
}

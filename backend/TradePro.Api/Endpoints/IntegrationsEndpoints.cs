using TradePro.Api.Providers.Trading212;

namespace TradePro.Api.Endpoints;

public static class IntegrationsEndpoints
{
    public static IEndpointRouteBuilder MapIntegrationsEndpoints(this IEndpointRouteBuilder app)
    {
        // Surfaces whether the T212 layer can reach the broker with the
        // current config — used by the Settings page to confirm a key
        // pair is live before we let the user save it.
        app.MapGet("/integrations/trading212/status",
            async (Trading212Client client, CancellationToken ct) =>
                Results.Ok(await client.GetStatusAsync(ct)));

        // Cached T212 instruments registry — loads from
        // /equity/metadata/instruments on first access, refreshes every
        // 24h. Honours the T212 1-req-per-50s rate limit by holding the
        // result in a singleton service and persisting to disk so a
        // restart doesn't wipe the cache.
        app.MapGet("/integrations/trading212/instruments",
            async (
                string? q,
                int? limit,
                Trading212InstrumentsService svc,
                CancellationToken ct) =>
            {
                if (!svc.IsEnabled)
                {
                    return Results.Ok(new
                    {
                        enabled = false,
                        message = "Trading212 integration is disabled. Set Trading212:Mode and credentials.",
                        cachedCount = 0,
                        items = Array.Empty<Trading212Instrument>(),
                    });
                }
                if (string.IsNullOrWhiteSpace(q))
                {
                    var all = await svc.GetAllAsync(ct);
                    return Results.Ok(new
                    {
                        enabled = true,
                        cachedCount = svc.CachedCount,
                        loadedAtUtc = svc.LoadedAtUtc,
                        items = all.Take(Math.Clamp(limit ?? 50, 1, 500)),
                    });
                }
                var hits = await svc.SearchAsync(q, Math.Clamp(limit ?? 25, 1, 100), ct);
                return Results.Ok(new
                {
                    enabled = true,
                    query = q,
                    cachedCount = svc.CachedCount,
                    loadedAtUtc = svc.LoadedAtUtc,
                    items = hits,
                });
            });

        // Open T212 positions with computed unrealised P&L per row
        // and totals. T212's currentPrice is included so the operator
        // can reconcile against the Yahoo close that drives our
        // indicators (handy when the two diverge after a corporate
        // action or a stale Yahoo bar).
        app.MapGet("/integrations/trading212/positions",
            async (Trading212Client client, CancellationToken ct) =>
            {
                if (!client.IsEnabled)
                {
                    return Results.Ok(new
                    {
                        enabled = false,
                        message = "Trading212 integration is disabled. Set Trading212:Mode and credentials.",
                        positions = Array.Empty<object>(),
                    });
                }
                var raw = await client.GetPositionsAsync(ct);
                var rows = raw.Select(p =>
                {
                    decimal? unrealisedPct = null;
                    decimal? unrealisedAbs = null;
                    if (p.AveragePricePaid is decimal avg && avg > 0
                        && p.CurrentPrice is decimal cur)
                    {
                        unrealisedPct = (cur - avg) / avg * 100m;
                        unrealisedAbs = (cur - avg) * p.Quantity;
                    }
                    // T212 nests the ticker inside `instrument` on the
                    // /equity/positions response; the top-level Ticker
                    // we modelled isn't populated, hence the null seen
                    // in the wild. Fall back to it just in case a future
                    // shape change moves it back.
                    var t212Ticker = p.Instrument?.Ticker ?? p.Ticker;
                    return new
                    {
                        ticker = t212Ticker,
                        // Best-effort Yahoo-symbol derivation. T212
                        // tickers look like "AMZN_US_EQ"; we split on
                        // underscore and take the first part for US
                        // tickers (verified mapping). Other venues need
                        // explicit mapping; null tells the caller to
                        // not cross-reference against the compare cache.
                        yahooSymbol = DeriveYahooSymbol(t212Ticker),
                        instrumentName = p.Instrument?.Name,
                        currency = p.Instrument?.Currency,
                        isin = p.Instrument?.Isin,
                        quantity = p.Quantity,
                        averagePricePaid = p.AveragePricePaid,
                        currentPrice = p.CurrentPrice,
                        unrealisedPct,
                        unrealisedAbs,
                        createdAt = p.CreatedAt,
                    };
                }).ToList();
                return Results.Ok(new
                {
                    enabled = true,
                    fetchedAtUtc = DateTime.UtcNow,
                    positionCount = rows.Count,
                    positions = rows,
                });
            });

        return app;
    }

    /// <summary>
    /// T212 ticker → Yahoo Finance symbol for cross-reference against
    /// the compare cache. US-only mapping is reliable; non-US returns
    /// null so the caller skips the lookup rather than guessing.
    /// </summary>
    private static string? DeriveYahooSymbol(string? t212Ticker)
    {
        if (string.IsNullOrWhiteSpace(t212Ticker)) return null;
        var parts = t212Ticker.Split('_');
        if (parts.Length >= 2 && parts[1].Equals("US", StringComparison.OrdinalIgnoreCase))
        {
            return parts[0]; // AMZN_US_EQ → AMZN
        }
        return null;
    }
}

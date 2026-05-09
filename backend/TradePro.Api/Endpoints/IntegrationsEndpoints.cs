using TradePro.Api.Providers.Finnhub;
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
        // action or a stale Yahoo bar). Also surfaces `mode` on
        // every response — `demo` for paper trading, `live` for real
        // money — so every consumer (UI, email, MCP) can show the
        // user which world they're looking at.
        app.MapGet("/integrations/trading212/positions",
            async (
                Trading212Client client,
                Trading212PositionsCache cache,
                CancellationToken ct) =>
            {
                if (!client.IsEnabled)
                {
                    return Results.Ok(new
                    {
                        enabled = false,
                        mode = client.Mode,
                        message = "Trading212 integration is disabled. Set Trading212:Mode and credentials.",
                        positions = Array.Empty<object>(),
                    });
                }
                // Cache wraps the upstream client so multiple consumers
                // (HoldingsHealthCard + Portfolio page) on the same
                // session don't trip T212's 1 req/1s limit. Returns a
                // result envelope carrying FromCache + AgeSeconds so
                // the UI can show "as of 12s ago" honestly.
                var result = await cache.GetAsync(ct);
                var rows = result.Positions.Select(p =>
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
                    // /equity/portfolio response; the top-level Ticker
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
                    mode = client.Mode,
                    fetchedAtUtc = DateTime.UtcNow,
                    positionCount = rows.Count,
                    positions = rows,
                    // Surfaces the underlying T212 failure so the UI
                    // doesn't silently render "0 positions" when the
                    // real story is "401 Unauthorized" or "404 not
                    // found". Null when the call succeeded.
                    error = result.Error,
                    httpStatus = result.HttpStatus,
                    fromCache = result.FromCache,
                    ageSeconds = result.AgeSeconds,
                });
            });

        // Finnhub forward earnings calendar (next ~30 days by default,
        // overridable via `days`). Off by default — returns
        // {enabled: false} until Finnhub__ApiKey is set in config.
        // Used to flag "MSFT reports in 5 days" so the digest can warn
        // the user about position-into-earnings volatility risk.
        app.MapGet("/integrations/finnhub/earnings-calendar",
            async (
                string? symbol,
                int? days,
                FinnhubClient client,
                CancellationToken ct) =>
            {
                if (!client.IsEnabled)
                {
                    return Results.Ok(new
                    {
                        enabled = false,
                        message = "Finnhub integration is disabled. Set Finnhub:ApiKey in config (free tier signup at finnhub.io).",
                        events = Array.Empty<FinnhubEarningsEvent>(),
                    });
                }
                if (string.IsNullOrWhiteSpace(symbol))
                {
                    return Results.BadRequest(new { error = "symbol is required" });
                }
                var from = DateOnly.FromDateTime(DateTime.UtcNow.Date);
                var to = from.AddDays(Math.Clamp(days ?? 30, 1, 90));
                var events = await client.GetEarningsCalendarAsync(symbol, from, to, ct);
                return Results.Ok(new
                {
                    enabled = true,
                    symbol = symbol.ToUpperInvariant(),
                    from = from.ToString("yyyy-MM-dd"),
                    to = to.ToString("yyyy-MM-dd"),
                    eventCount = events.Count,
                    events,
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

using TradePro.Api.Models;
using TradePro.Api.Providers;
using TradePro.Api.Providers.Trading212;

namespace TradePro.Api.Endpoints;

public static class InstrumentEndpoints
{
    public static IEndpointRouteBuilder MapInstrumentEndpoints(this IEndpointRouteBuilder app)
    {
        // Symbol picker autocomplete. The Signals + Compare pages
        // hit this on every keystroke (debounced client-side) so the
        // user can't fat-finger an invalid ticker into evaluateSignal
        // and get a Yahoo 500.
        //
        // Result merging: T212 instruments cache (when configured)
        // is queried first because those are tickers the user can
        // actually trade in their T212 account — the right thing to
        // surface at the top of the dropdown. Yahoo search backfills
        // the rest so symbols outside T212's universe (e.g. UK
        // small-caps, niche sector ETFs) still resolve. Each match
        // carries its `source` field so the UI can show a "tradable
        // in T212" chip on the relevant rows.
        app.MapGet("/instruments/search",
            async (
                string? q,
                int? limit,
                YahooSearchProvider yahoo,
                Trading212InstrumentsService t212,
                CancellationToken ct) =>
            {
                var query = (q ?? string.Empty).Trim();
                if (query.Length < 1)
                {
                    return Results.Ok(new InstrumentSearchResponse(
                        query, 0, Array.Empty<InstrumentMatch>()));
                }
                var cap = Math.Clamp(limit ?? 10, 1, 25);
                var merged = new List<InstrumentMatch>(cap);
                var seenSymbols = new HashSet<string>(StringComparer.OrdinalIgnoreCase);

                // T212 hits first when enabled. Map to the same
                // InstrumentMatch shape Yahoo emits so the frontend
                // doesn't need a discriminator beyond `source`.
                if (t212.IsEnabled)
                {
                    var tHits = await t212.SearchAsync(query, cap, ct);
                    foreach (var inst in tHits)
                    {
                        var sym = DeriveYahooFromT212(inst.Ticker)
                                  ?? inst.ShortName
                                  ?? inst.Ticker;
                        if (string.IsNullOrWhiteSpace(sym)) continue;
                        if (!seenSymbols.Add(sym)) continue;
                        merged.Add(new InstrumentMatch(
                            Symbol: sym,
                            Name: inst.Name ?? inst.ShortName ?? sym,
                            Exchange: ExtractExchange(inst.Ticker),
                            Type: inst.Type,
                            Currency: inst.CurrencyCode,
                            Source: "trading212"));
                        if (merged.Count >= cap) break;
                    }
                }

                if (merged.Count < cap)
                {
                    var yahooHits = await yahoo.SearchAsync(query, cap, ct);
                    foreach (var m in yahooHits)
                    {
                        if (!seenSymbols.Add(m.Symbol)) continue;
                        merged.Add(m);
                        if (merged.Count >= cap) break;
                    }
                }

                return Results.Ok(new InstrumentSearchResponse(
                    query, merged.Count, merged));
            });

        // Full set of bare strategy symbols T212 LISTS as tradeable US
        // equities — for PER-BROKER universe validation. The Mac paper-session
        // daemon has no direct T212 creds (orders execute server-side via
        // ApproveAsync), so it reads this set to drop untradeable names BEFORE
        // sizing instead of 404-ing at the router every cycle. Backed by the
        // cached instruments registry (refreshes server-side). `enabled=false`
        // when T212 isn't configured so the caller fails open (trades full
        // universe rather than halting).
        app.MapGet("/instruments/t212/tradeable",
            async (Trading212InstrumentsService t212, CancellationToken ct) =>
            {
                if (!t212.IsEnabled)
                {
                    return Results.Ok(new
                    {
                        enabled = false,
                        count = 0,
                        symbols = Array.Empty<string>(),
                    });
                }
                var all = await t212.GetAllAsync(ct);
                var bare = all
                    .Select(i => DeriveYahooFromT212(i.Ticker))
                    .Where(s => !string.IsNullOrWhiteSpace(s))
                    .Select(s => s!.ToUpperInvariant())
                    .Distinct()
                    .OrderBy(s => s, StringComparer.Ordinal)
                    .ToArray();
                return Results.Ok(new
                {
                    enabled = true,
                    count = bare.Length,
                    loadedAtUtc = t212.LoadedAtUtc,
                    symbols = bare,
                });
            });

        return app;
    }

    /// <summary>T212 ticker (e.g. "AAPL_US_EQ") → Yahoo symbol
    /// ("AAPL"). Mirrors Trading212 endpoint mapping. Returns null
    /// for non-US venues; caller falls back to shortName.</summary>
    private static string? DeriveYahooFromT212(string? t212Ticker)
    {
        if (string.IsNullOrWhiteSpace(t212Ticker)) return null;
        var parts = t212Ticker.Split('_');
        if (parts.Length >= 2 && parts[1].Equals("US", StringComparison.OrdinalIgnoreCase))
        {
            return parts[0];
        }
        return null;
    }

    private static string? ExtractExchange(string? t212Ticker)
    {
        if (string.IsNullOrWhiteSpace(t212Ticker)) return null;
        var parts = t212Ticker.Split('_');
        return parts.Length >= 2 ? parts[1] : null;
    }
}

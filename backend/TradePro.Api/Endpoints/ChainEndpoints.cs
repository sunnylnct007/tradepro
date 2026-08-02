using TradePro.Api.Providers.IBKR;

namespace TradePro.Api.Endpoints;

/// <summary>
/// G3 chain feed (TRADEPRO_SPEC_V2.md — "THE blocking build" for Product 1,
/// Wheel Companion): real bid/ask/delta/OI option-chain data proxied live from
/// IBKR, ONE implementation for every consumer (Wheel Companion candidates,
/// the Symbol Lab options panel, and the existing OptionsDesk wheel-candidate
/// UI) instead of three separate integrations. Pure live proxy, same pattern
/// as <c>IBKRClient.GetPriceHistoryAsync</c> for equities — no persistence,
/// nothing to migrate.
///
/// NOT YET VERIFIED against a live IBKR session. The exact JSON shapes for
/// secdef/search's `sections`, secdef/info's contract array, and the
/// marketdata/snapshot field encoding are IBKR's documented cpapi
/// conventions (IBKR's own field-reference page returned HTTP 403 from this
/// environment, so it could not be read directly during this build) —
/// verify against the paper account on a known chain (e.g. SPY) before the
/// wheel loop trusts these numbers for sizing or strike selection.
/// </summary>
public static class ChainEndpoints
{
    public static IEndpointRouteBuilder MapChainEndpoints(this IEndpointRouteBuilder app)
    {
        var group = app.MapGroup("/ibkr/chain").WithTags("IBKR Chain");

        // GET /api/ibkr/chain/{symbol}/months — cheap first call: underlying conid +
        // available expiration months, for a month picker before the fuller (and
        // more IBKR-call-expensive) chain request below.
        group.MapGet("/{symbol}/months", async (string symbol, IBKRClient ibkr, CancellationToken ct) =>
        {
            var r = await ibkr.GetOptionMonthsAsync(symbol, ct);
            return Results.Ok(new
            {
                symbol = symbol.Trim().ToUpperInvariant(),
                underlyingConId = r.ConId,
                months = r.Months,
                error = r.Error,
            });
        });

        // GET /api/ibkr/chain/{symbol}?month=AUG26&right=P&maxStrikes=20
        // Full chain for one expiration: strikes -> contracts -> batched quote
        // snapshot. `right` = "C", "P", or omitted for both. `maxStrikes` bounds
        // IBKR call volume by keeping only the N strikes nearest spot per side —
        // a wheel candidate never needs the full chain, just near-the-money.
        group.MapGet("/{symbol}", async (
            string symbol, string? month, string? right, int? maxStrikes,
            IBKRClient ibkr, CancellationToken ct) =>
        {
            var sym = symbol.Trim().ToUpperInvariant();

            var monthsResult = await ibkr.GetOptionMonthsAsync(sym, ct);
            if (monthsResult.Error is not null || monthsResult.ConId is null)
                return Results.Ok(new ChainResponse(
                    sym, monthsResult.ConId, null, null, Array.Empty<ChainLeg>(),
                    monthsResult.Error ?? $"no underlying contract for {sym}"));
            var underlyingConId = monthsResult.ConId.Value;

            var chosenMonth = string.IsNullOrWhiteSpace(month)
                ? monthsResult.Months.FirstOrDefault()
                : monthsResult.Months.FirstOrDefault(
                      m => string.Equals(m, month, StringComparison.OrdinalIgnoreCase))
                  ?? month;
            if (string.IsNullOrWhiteSpace(chosenMonth))
                return Results.Ok(new ChainResponse(
                    sym, underlyingConId, null, null, Array.Empty<ChainLeg>(),
                    $"no expiration months available for {sym}"));

            var rights = string.IsNullOrWhiteSpace(right)
                ? new[] { "C", "P" }
                : new[] { right.Trim().ToUpperInvariant() };

            // Spot, for near-the-money filtering — best-effort; a null spot just
            // means maxStrikes can't narrow the set (the full chain is still returned).
            decimal? spot = null;
            var spotRaw = await ibkr.GetSnapshotRawAsync(underlyingConId, "31", ct);
            if (spotRaw is not null)
                spot = IBKRResponseParser.ParseSnapshotLast(spotRaw);

            var contracts = new List<IBKROptionContract>();
            var contractRight = new Dictionary<long, string>();
            string? contractsError = null;
            foreach (var r in rights)
            {
                var cr = await ibkr.GetOptionContractsAsync(underlyingConId, chosenMonth, r, ct);
                if (cr.Error is not null)
                    contractsError = contractsError is null ? cr.Error : $"{contractsError}; {cr.Error}";
                foreach (var c in cr.Contracts)
                {
                    contracts.Add(c);
                    contractRight[c.ConId] = r;
                }
            }
            if (contracts.Count == 0)
                return Results.Ok(new ChainResponse(
                    sym, underlyingConId, chosenMonth, spot, Array.Empty<ChainLeg>(),
                    contractsError ?? $"no contracts for {sym} {chosenMonth}"));

            var selected = contracts.AsEnumerable();
            if (spot is not null && maxStrikes is > 0)
                selected = contracts.OrderBy(c => Math.Abs(c.Strike - spot.Value)).Take(maxStrikes.Value * rights.Length);
            var selectedList = selected.ToList();

            var quotesResult = await ibkr.GetOptionSnapshotBatchAsync(
                selectedList.Select(c => c.ConId).ToArray(), ct);
            var byConId = quotesResult.Quotes.ToDictionary(q => q.ConId, q => q);

            var legs = selectedList
                .Select(c =>
                {
                    byConId.TryGetValue(c.ConId, out var q);
                    return new ChainLeg(
                        c.ConId, c.Strike, contractRight.GetValueOrDefault(c.ConId, "?"),
                        q?.Bid, q?.Ask, q?.Last, q?.Delta, q?.Gamma, q?.Theta, q?.Vega,
                        q?.ImpliedVolPct, q?.OpenInterest);
                })
                .OrderBy(l => l.Right).ThenBy(l => l.Strike)
                .ToList();

            var error = quotesResult.Error ?? contractsError;
            return Results.Ok(new ChainResponse(sym, underlyingConId, chosenMonth, spot, legs, error));
        });

        return app;
    }
}

public sealed record ChainLeg(
    long ConId, decimal Strike, string Right,
    decimal? Bid, decimal? Ask, decimal? Last,
    decimal? Delta, decimal? Gamma, decimal? Theta, decimal? Vega,
    decimal? ImpliedVolPct, decimal? OpenInterest);

public sealed record ChainResponse(
    string Symbol, long? UnderlyingConId, string? Month, decimal? Spot,
    IReadOnlyList<ChainLeg> Legs, string? Error);

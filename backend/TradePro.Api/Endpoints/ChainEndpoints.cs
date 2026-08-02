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

            // Spot, for near-the-money filtering. Unlike the equity-history path,
            // this ISN'T optional here: secdef/info needs a strike per call (see
            // GetOptionContractsAsync), so without a spot to rank by we'd have to
            // resolve EVERY listed strike individually — a null spot fails the
            // whole request rather than silently doing that.
            decimal? spot = null;
            var spotRaw = await ibkr.GetSnapshotRawAsync(underlyingConId, "31", ct);
            if (spotRaw is not null)
                spot = IBKRResponseParser.ParseSnapshotLast(spotRaw);
            if (spot is null)
                return Results.Ok(new ChainResponse(
                    sym, underlyingConId, chosenMonth, null, Array.Empty<ChainLeg>(),
                    $"could not read {sym} spot price (needed to select near-the-money strikes)"));

            // Strikes for the chosen month, narrowed to maxStrikes nearest spot
            // PER SIDE before any per-contract resolution — each selected strike
            // costs one secdef/info call, so this bounds IBKR call volume (and
            // pacing-limit risk) up front rather than after the fact.
            var strikesResult = await ibkr.GetOptionStrikesAsync(underlyingConId, chosenMonth, ct);
            if (strikesResult.Error is not null && strikesResult.Calls.Count == 0 && strikesResult.Puts.Count == 0)
                return Results.Ok(new ChainResponse(
                    sym, underlyingConId, chosenMonth, spot, Array.Empty<ChainLeg>(), strikesResult.Error));

            var wanted = new List<(decimal Strike, string Right)>();
            var cap = maxStrikes is > 0 ? maxStrikes.Value : 20;
            foreach (var r in rights)
            {
                var side = r == "C" ? strikesResult.Calls : strikesResult.Puts;
                wanted.AddRange(
                    side.OrderBy(s => Math.Abs(s - spot.Value)).Take(cap).Select(s => (Strike: s, Right: r)));
            }
            if (wanted.Count == 0)
                return Results.Ok(new ChainResponse(
                    sym, underlyingConId, chosenMonth, spot, Array.Empty<ChainLeg>(),
                    $"no strikes for {sym} {chosenMonth}"));

            // Resolve each selected (strike, right) to its tradeable conid.
            // Sequential, not parallel: IBKR's Web API rate-limits aggressively and
            // this whole flow already shares one session-cached client (see
            // IBKRClient's SESSION MODEL doc) — bursting these would risk the same
            // pacing lockouts already seen on the historical-data path.
            var contracts = new List<IBKROptionContract>();
            var contractRight = new Dictionary<long, string>();
            string? contractsError = null;
            foreach (var (strike, r) in wanted)
            {
                var cr = await ibkr.GetOptionContractsAsync(underlyingConId, chosenMonth, strike, r, ct);
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
                    contractsError ?? $"no contracts resolved for {sym} {chosenMonth}"));

            var quotesResult = await ibkr.GetOptionSnapshotBatchAsync(
                contracts.Select(c => c.ConId).ToArray(), ct);
            var byConId = quotesResult.Quotes.ToDictionary(q => q.ConId, q => q);

            var legs = contracts
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

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
/// LIVE-VERIFIED against the real IBKR paper account 2 Aug 2026 (SPY: real
/// conid, real strikes, real delta/gamma/theta/vega/OI from IBKR's tick
/// greeks). bid/ask/last were null in that check — plausibly just the
/// market being closed at the time rather than a feed bug; re-verify
/// in-session before trusting bid/ask for real premium sizing. The field
/// codes themselves are still cross-checked against an open-source client
/// rather than IBKR's own docs (their field-reference page 403's from this
/// environment).
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
            //
            // Live-verified 2 Aug 2026: IBKR's marketdata/snapshot needs a
            // "warm-up" — the FIRST request against a conid that isn't already
            // subscribed often comes back with the field still empty, and a
            // second request ~1s later returns the real value (documented IBKR
            // behaviour, confirmed here by hand: an immediate retry against SPY
            // succeeded where the first call returned nothing). One retry only.
            decimal? spot = null;
            var spotRaw = await ibkr.GetSnapshotRawAsync(underlyingConId, "31", ct);
            if (spotRaw is not null)
                spot = IBKRResponseParser.ParseSnapshotLast(spotRaw);
            if (spot is null)
            {
                await Task.Delay(TimeSpan.FromSeconds(1.2), ct);
                spotRaw = await ibkr.GetSnapshotRawAsync(underlyingConId, "31", ct);
                if (spotRaw is not null)
                    spot = IBKRResponseParser.ParseSnapshotLast(spotRaw);
            }
            if (spot is null)
                return Results.Ok(new ChainResponse(
                    sym, underlyingConId, chosenMonth, null, Array.Empty<ChainLeg>(),
                    $"could not read {sym} spot price after warm-up retry (needed to select near-the-money strikes)"));

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
            // Live-verified 2 Aug 2026: secdef/info for one (month, strike, right)
            // can return SEVERAL conids, not one — different listings/routings of
            // what a trader thinks of as "the same contract" (seen: 13 conids for
            // a single SPY strike). Only the first is kept; a wheel candidate
            // needs one tradeable contract per strike, not every routing variant.
            var contracts = new List<IBKROptionContract>();
            var contractRight = new Dictionary<long, string>();
            string? contractsError = null;
            foreach (var (strike, r) in wanted)
            {
                var cr = await ibkr.GetOptionContractsAsync(underlyingConId, chosenMonth, strike, r, ct);
                if (cr.Error is not null)
                    contractsError = contractsError is null ? cr.Error : $"{contractsError}; {cr.Error}";
                var first = cr.Contracts.FirstOrDefault();
                if (first is not null)
                {
                    contracts.Add(first);
                    contractRight[first.ConId] = r;
                }
            }
            if (contracts.Count == 0)
                return Results.Ok(new ChainResponse(
                    sym, underlyingConId, chosenMonth, spot, Array.Empty<ChainLeg>(),
                    contractsError ?? $"no contracts resolved for {sym} {chosenMonth}"));

            // Same warm-up quirk as the spot snapshot above, live-verified 2 Aug
            // 2026 on a month never requested before (SEP26): every leg came back
            // with delta/bid/ask/OI all null on the first call, populated on an
            // immediate retry. Detect "still cold" as ALL legs missing delta (the
            // field that's cheapest for IBKR to warm and most load-bearing for
            // strike selection) and retry the whole batch once.
            var conIds = contracts.Select(c => c.ConId).ToArray();
            var quotesResult = await ibkr.GetOptionSnapshotBatchAsync(conIds, ct);
            // Two cold signatures, up to two retries (still pacing-safe — the
            // flow is sequential and bounded per symbol):
            //   • ALL legs missing delta — the never-subscribed month (2 Aug).
            //   • MAJORITY of legs missing both bid and ask — the mid-session
            //     partial-cold batch (11 Aug): the sweep looked "warm" so no
            //     retry fired, but the one leg the wheel SELECTS was often
            //     among the cold ones → premium null → whole symbols rotated
            //     into carry-forward pricing DURING regular hours.
            // One retry, not two (12 Aug): the second retry's extra snapshot
            // volume across an 82-symbol sweep helped exhaust IBKR's pacing
            // budget ~18 names in — the IV snapshots for every later symbol
            // starved (the 62/82 iv-dark run). One retry keeps most of the
            // partial-cold recovery at half the budget cost.
            for (var attempt = 0; attempt < 1 && QuotesStillCold(quotesResult.Quotes); attempt++)
            {
                await Task.Delay(TimeSpan.FromSeconds(1.2), ct);
                quotesResult = await ibkr.GetOptionSnapshotBatchAsync(conIds, ct);
            }
            var byConId = quotesResult.Quotes.ToDictionary(q => q.ConId, q => q);

            var legs = contracts
                .Select(c =>
                {
                    byConId.TryGetValue(c.ConId, out var q);
                    return new ChainLeg(
                        c.ConId, c.Strike, contractRight.GetValueOrDefault(c.ConId, "?"),
                        q?.Bid, q?.Ask, q?.Last, q?.Delta, q?.Gamma, q?.Theta, q?.Vega,
                        q?.ImpliedVolPct, q?.OpenInterest, q?.PriorClose);
                })
                .OrderBy(l => l.Right).ThenBy(l => l.Strike)
                .ToList();

            // FAIL-LOUD (NO FALSE POSITIVES): a chain whose every leg is
            // quote-less after the warm-up retry is NOT a tradeable chain —
            // without an error here it flowed downstream as "success", where
            // consumers zero-filled the nulls into fabricated $0-premium /
            // 0-IV quotes (the Route-B gap flagged 8 Aug 2026). Legs are still
            // returned (strikes/conids are real); the error tells the caller
            // the QUOTES are not.
            var error = quotesResult.Error ?? contractsError;
            if (error is null && AllLegsQuoteless(legs))
                error = $"option quotes still cold after warm-up retry for {sym} {chosenMonth} — "
                    + "every leg returned null bid/ask/delta (market closed or IBKR quote cache cold); "
                    + "strikes are real but this is NOT a tradeable chain";
            return Results.Ok(new ChainResponse(sym, underlyingConId, chosenMonth, spot, legs, error));
        });

        return app;
    }

    /// <summary>True when a non-empty chain carries NO quote data at all —
    /// every leg's bid, ask AND delta are null. One populated field on one
    /// leg is enough to call the chain warm (partial coverage is normal:
    /// far-from-the-money legs legitimately have no bid).</summary>
    public static bool AllLegsQuoteless(IReadOnlyList<ChainLeg> legs)
        => legs.Count > 0
           && legs.All(l => l.Bid is null && l.Ask is null && l.Delta is null);

    /// <summary>True when the quote batch deserves a warm-up retry: every
    /// quote missing delta (never-subscribed month) OR more than half missing
    /// BOTH bid and ask (mid-session partial-cold). Far-OTM legs legitimately
    /// have no bid — hence majority, not any.</summary>
    public static bool QuotesStillCold(IReadOnlyList<IBKROptionQuote> quotes)
        => quotes.Count > 0
           && (quotes.All(q => q.Delta is null)
               || quotes.Count(q => q.Bid is null && q.Ask is null) * 2 > quotes.Count);
}

public sealed record ChainLeg(
    long ConId, decimal Strike, string Right,
    decimal? Bid, decimal? Ask, decimal? Last,
    decimal? Delta, decimal? Gamma, decimal? Theta, decimal? Vega,
    decimal? ImpliedVolPct, decimal? OpenInterest,
    // Prior-session close premium — the honest between-sessions indicative
    // value (options have no pre-market). Labeled, never a live quote.
    decimal? PriorClose = null);

public sealed record ChainResponse(
    string Symbol, long? UnderlyingConId, string? Month, decimal? Spot,
    IReadOnlyList<ChainLeg> Legs, string? Error);

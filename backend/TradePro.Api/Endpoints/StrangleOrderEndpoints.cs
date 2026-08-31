using TradePro.Api.Providers.IBKR;

namespace TradePro.Api.Endpoints;

/// <summary>
/// POST /api/integrations/ibkr/strangle — place BOTH legs of a short strangle
/// on the IBKR PAPER account, and report what actually filled.
///
/// Owner, 31 Aug 2026: "ok start with the us paper execution", and on why only
/// the US — "this cannot be done for NIFTY as we dont have paper trading but
/// with us and uk exchanges we can try".
///
/// WHY THIS IS THE POINT OF THE WHOLE EXERCISE. Every figure this strategy
/// publishes — 82.9% win, +0.029% per trade — comes from Black-Scholes premiums
/// derived from a volatility index. There is no skew in that, no bid-ask, and
/// no evidence it is what anyone would actually be filled at. Real paper fills
/// produce REAL premiums, which is the one input no backtest can manufacture
/// and no vendor sells cheaply. A month of these is worth more than another
/// year of modelled backtests.
///
/// SAFETY. This inherits IBKRClient.AllowOrders, which returns false for a live
/// account unconditionally — there is no setting that permits live placement.
/// On a live-mode client this endpoint refuses before resolving anything.
///
/// TWO LEGS, REPORTED SEPARATELY AND HONESTLY. A strangle whose call fills and
/// whose put does not is a NAKED CALL, which is a different and much worse
/// trade. So each leg reports its own status, and a partial fill is called out
/// as partial rather than averaged into a cheerful summary. Deciding what to do
/// about a half-filled strangle is the operator's call; hiding it is not.
/// </summary>
public static class StrangleOrderEndpoints
{
    public sealed record StrangleRequest(
        string Symbol,
        string Expiry,          // YYYY-MM-DD
        decimal PutStrike,
        decimal CallStrike,
        int Contracts = 1);

    public static IEndpointRouteBuilder MapStrangleOrderEndpoints(this IEndpointRouteBuilder app)
    {
        app.MapPost("/integrations/ibkr/strangle", async (
            StrangleRequest req,
            IBKRClient ibkr,
            ILoggerFactory lf,
            CancellationToken ct) =>
        {
            var log = lf.CreateLogger("StrangleOrder");

            if (!ibkr.IsEnabled)
                return Results.Json(new { error = "IBKR disabled" }, statusCode: 503);
            if (!ibkr.AllowOrders)
                return Results.Json(new
                {
                    error = ibkr.BlockedForLive
                        ? "REFUSED: live account. TradePro places no orders to live, ever."
                        : "order placement is off (AllowOrders=false)",
                    blockedForLive = ibkr.BlockedForLive,
                }, statusCode: 403);
            if (req is null || string.IsNullOrWhiteSpace(req.Symbol)
                || req.PutStrike <= 0 || req.CallStrike <= 0 || req.Contracts <= 0)
                return Results.BadRequest(new
                {
                    error = "symbol, expiry, putStrike>0, callStrike>0, contracts>0 required",
                });
            if (req.PutStrike >= req.CallStrike)
                return Results.BadRequest(new
                {
                    // A crossed strangle is an inverted position with an
                    // entirely different risk profile. The same guard exists in
                    // strike_pair on the Python side; both ends check, because
                    // this one can be called directly.
                    error = $"put {req.PutStrike} must be BELOW call {req.CallStrike} — "
                          + "a crossed strangle is a different trade",
                });

            var sym = req.Symbol.Trim().ToUpperInvariant();
            log.LogWarning(
                "STRANGLE (paper): SELL {Sym} {Exp} {Put}P + {Call}C x{Qty}",
                sym, req.Expiry, req.PutStrike, req.CallStrike, req.Contracts);

            // Resolve BOTH contracts BEFORE placing either. Placing leg one and
            // then discovering leg two does not exist would leave a naked short.
            var put = await ibkr.ResolveOptionConidAsync(sym, req.Expiry, req.PutStrike, "P", ct);
            var call = await ibkr.ResolveOptionConidAsync(sym, req.Expiry, req.CallStrike, "C", ct);
            if (put is null || call is null)
                return Results.Json(new
                {
                    ok = false, stage = "resolve",
                    error = "could not resolve one or both contracts — NOTHING was placed",
                    putResolved = put is not null, callResolved = call is not null,
                    symbol = sym, expiry = req.Expiry,
                    putStrike = req.PutStrike, callStrike = req.CallStrike,
                }, statusCode: 502);

            var putRes = await ibkr.PlaceMarketOrderAsync(put.Value, "SELL", req.Contracts, ct);
            var callRes = await ibkr.PlaceMarketOrderAsync(call.Value, "SELL", req.Contracts, ct);
            var putOk = putRes.Status == "ACCEPTED" && putRes.OrderId is not null;
            var callOk = callRes.Status == "ACCEPTED" && callRes.OrderId is not null;

            // A half-filled strangle is NAKED on the filled side. Say so.
            var partial = putOk ^ callOk;
            if (partial)
                log.LogError(
                    "PARTIAL STRANGLE on {Sym}: put={PutOk} call={CallOk} — the filled "
                    + "leg is now NAKED and needs an operator decision", sym, putOk, callOk);

            return Results.Json(new
            {
                ok = putOk && callOk,
                partial,
                warning = partial
                    ? "PARTIAL — only one leg was accepted. The filled leg is a NAKED "
                    + "short, not a strangle. Close it or complete the pair."
                    : null,
                symbol = sym, expiry = req.Expiry, contracts = req.Contracts,
                put = new { conid = put, strike = req.PutStrike, orderId = putRes.OrderId,
                            status = putRes.Status, reason = putRes.StatusReason },
                call = new { conid = call, strike = req.CallStrike, orderId = callRes.OrderId,
                             status = callRes.Status, reason = callRes.StatusReason },
                note = "PAPER account. Premiums are whatever the broker actually filled — "
                     + "that is the point; the modelled Black-Scholes credit is not evidence.",
            }, statusCode: (putOk && callOk) ? 200 : 502);
        })
        .WithName("PlaceStrangle");

        return app;
    }
}

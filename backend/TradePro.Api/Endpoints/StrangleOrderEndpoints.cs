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


    // NOTE: order placement uses IBKRClient.PlaceMarketOrderConfirmedAsync,
    // which already drives the full place -> reply/confirm -> real order id
    // sequence. An earlier version of this file called PlaceMarketOrderAsync
    // (which stops at NEEDS_CONFIRM) and then grew its own confirmation loop
    // beside the working one — re-solving a problem this codebase had already
    // solved and tested. IBKRClient's own comment records why it exists: the
    // unconfirmed path "persisted the REPLY id as broker_order_id while the
    // order never actually placed — the exact reason the clone's fills carried
    // no broker id".
    //
    // Owner, 31 Aug 2026, on the same class of mistake: "we have sorted all
    // data related call issues with IBKR ... so now we dont want to get into
    // that again."

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
            {
                // LOG IT, do not merely return it. On 1 Sep 2026 the scheduled
                // 13:45Z run failed to resolve SPY, QQQ and GLD; this branch
                // returned 502 and wrote nothing to the log, while the caller
                // swallowed the response just as silently. The failure was
                // invisible at BOTH ends and was found only by noticing that
                // positions had not changed.
                //
                // A refusal to place is an event worth a line in the log,
                // whatever the caller does with the response.
                log.LogError(
                    "STRANGLE RESOLVE FAILED for {Sym} {Exp}: put {Put}={PutOk} "
                    + "call {Call}={CallOk} — NOTHING was placed",
                    sym, req.Expiry, req.PutStrike, put is not null,
                    req.CallStrike, call is not null);
                return Results.Json(new
                {
                    ok = false, stage = "resolve",
                    error = "could not resolve one or both contracts — NOTHING was placed",
                    putResolved = put is not null, callResolved = call is not null,
                    symbol = sym, expiry = req.Expiry,
                    putStrike = req.PutStrike, callStrike = req.CallStrike,
                }, statusCode: 502);
            }

            var putRes = await ibkr.PlaceMarketOrderConfirmedAsync(
                put.Value, "SELL", req.Contracts, ct);
            var callRes = await ibkr.PlaceMarketOrderConfirmedAsync(
                call.Value, "SELL", req.Contracts, ct);
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

        // POST /api/integrations/ibkr/strangle/close — buy BOTH legs back.
        //
        // Owner, 31 Aug 2026: "a auto close one on either profit or end of
        // day". This is the exit half; the decision of WHEN lives in the
        // Python job, because it needs the credit collected and this endpoint
        // does not.
        //
        // BUYS BOTH LEGS, and reports them separately for the same reason the
        // open does: closing one leg of a strangle leaves the other NAKED. A
        // half-closed position is a different and worse trade than either the
        // strangle or being flat, and it must never be summarised as "closed".
        app.MapPost("/integrations/ibkr/strangle/close", async (
            StrangleRequest req,
            IBKRClient ibkr,
            ILoggerFactory lf,
            CancellationToken ct) =>
        {
            var log = lf.CreateLogger("StrangleClose");
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
                return Results.BadRequest(new { error = "symbol, expiry, strikes, contracts required" });

            var sym = req.Symbol.Trim().ToUpperInvariant();
            log.LogWarning("STRANGLE CLOSE (paper): BUY {Sym} {Exp} {Put}P + {Call}C x{Qty}",
                sym, req.Expiry, req.PutStrike, req.CallStrike, req.Contracts);

            var put = await ibkr.ResolveOptionConidAsync(sym, req.Expiry, req.PutStrike, "P", ct);
            var call = await ibkr.ResolveOptionConidAsync(sym, req.Expiry, req.CallStrike, "C", ct);
            if (put is null || call is null)
            {
                log.LogError("STRANGLE CLOSE RESOLVE FAILED for {Sym} {Exp} — NOTHING was closed",
                    sym, req.Expiry);
                return Results.Json(new
                {
                    ok = false, stage = "resolve",
                    error = "could not resolve one or both contracts — NOTHING was closed",
                    putResolved = put is not null, callResolved = call is not null,
                }, statusCode: 502);
            }

            var putRes = await ibkr.PlaceMarketOrderConfirmedAsync(
                put.Value, "BUY", req.Contracts, ct);
            var callRes = await ibkr.PlaceMarketOrderConfirmedAsync(
                call.Value, "BUY", req.Contracts, ct);
            var putOk = putRes.Status == "ACCEPTED" && putRes.OrderId is not null;
            var callOk = callRes.Status == "ACCEPTED" && callRes.OrderId is not null;
            var partial = putOk ^ callOk;
            if (partial)
                log.LogError("PARTIAL CLOSE on {Sym}: put={PutOk} call={CallOk} — the "
                           + "leg still open is NAKED", sym, putOk, callOk);

            return Results.Json(new
            {
                ok = putOk && callOk,
                partial,
                warning = partial
                    ? "PARTIAL — only one leg closed. The remaining leg is a NAKED short."
                    : null,
                symbol = sym, expiry = req.Expiry, contracts = req.Contracts,
                put = new { orderId = putRes.OrderId, status = putRes.Status, reason = putRes.StatusReason },
                call = new { orderId = callRes.OrderId, status = callRes.Status, reason = callRes.StatusReason },
            }, statusCode: (putOk && callOk) ? 200 : 502);
        })
        .WithName("CloseStrangle");

        // POST /api/integrations/ibkr/option-leg — act on ONE option contract.
        //
        // Owner, 31 Aug 2026: "u shd be able to close them" — and I could not.
        // Three short puts sat open with no way to close them through TradePro,
        // because every option path here assumed a matched PAIR. /strangle/close
        // buys both legs by construction, so closing a lone put through it would
        // have BOUGHT A CALL I did not own — opening a long, not closing a short.
        // /integrations/ibkr/orders resolves by symbol and cannot address a
        // strike at all. The session had to be handed back for a manual close.
        //
        // A strategy that can open a position it cannot close is not finished.
        // This is the missing primitive; /strangle/close stays as the paired
        // convenience on top of it.
        app.MapPost("/integrations/ibkr/option-leg", async (
            OptionLegRequest req,
            IBKRClient ibkr,
            ILoggerFactory lf,
            CancellationToken ct) =>
        {
            var log = lf.CreateLogger("OptionLeg");

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
                || string.IsNullOrWhiteSpace(req.Expiry)
                || req.Strike <= 0 || req.Contracts <= 0)
                return Results.BadRequest(new { error = "symbol, expiry, strike>0, contracts>0 required" });

            var right = (req.Right ?? "").Trim().ToUpperInvariant();
            if (right is not ("P" or "C"))
                return Results.BadRequest(new { error = "right must be P or C" });
            var side = (req.Side ?? "").Trim().ToUpperInvariant();
            if (side is not ("BUY" or "SELL"))
                return Results.BadRequest(new { error = "side must be BUY or SELL" });

            var sym = req.Symbol.Trim().ToUpperInvariant();

            // The caller's conid wins. Only fall back to the chain lookup when
            // there is nothing better.
            var conid = req.Conid ?? await ibkr.ResolveOptionConidAsync(
                sym, req.Expiry, req.Strike, right, ct);
            if (conid is null)
                return Results.Json(new
                {
                    ok = false, stage = "resolve",
                    error = "could not resolve the contract — NOTHING was placed",
                    symbol = sym, expiry = req.Expiry, strike = req.Strike, right,
                }, statusCode: 502);

            // THE GUARD THAT MATTERS. A BUY is assumed to be closing a short.
            // If we are not actually short that contract, the same order OPENS
            // A LONG — a different trade, paid for rather than collected, and
            // one nobody asked for. That is precisely the mistake /strangle/close
            // would have made on a put-only book, so the check is enforced here
            // rather than left to the caller to remember.
            //
            // Set closingOnly=false to buy deliberately (a protective long, the
            // long wing of a spread). It must be explicit.
            if (side == "BUY" && req.ClosingOnly)
            {
                var pos = await ibkr.GetPositionsAsync(ct, forceFresh: true);
                if (pos.Error is not null)
                    return Results.Json(new
                    {
                        ok = false, stage = "verify",
                        error = $"could not read positions to verify this is a close: {pos.Error}. "
                              + "NOTHING was placed — refusing to guess.",
                    }, statusCode: 502);

                var held = pos.Positions.FirstOrDefault(p => p.ConId == conid.Value);
                var heldQty = held?.Quantity ?? 0m;
                if (heldQty >= 0m || Math.Abs(heldQty) < req.Contracts)
                    return Results.Json(new
                    {
                        ok = false, stage = "verify",
                        error = $"REFUSED: you are short {heldQty} of this contract, so buying "
                              + $"{req.Contracts} would not close a position — it would OPEN A LONG. "
                              + "Pass closingOnly=false if that is genuinely what you want.",
                        symbol = sym, expiry = req.Expiry, strike = req.Strike, right,
                        heldQuantity = heldQty, requested = req.Contracts,
                    }, statusCode: 409);
            }

            log.LogWarning("OPTION LEG (paper): {Side} {Qty} {Sym} {Exp} {Strike}{Right} conid={ConId}",
                side, req.Contracts, sym, req.Expiry, req.Strike, right, conid.Value);

            var res = await ibkr.PlaceMarketOrderConfirmedAsync(conid.Value, side, req.Contracts, ct);
            var ok = res.Status == "ACCEPTED" && res.OrderId is not null;

            return Results.Json(new
            {
                ok, orderId = res.OrderId, status = res.Status, reason = res.StatusReason,
                symbol = sym, expiry = req.Expiry, strike = req.Strike, right, side,
                contracts = req.Contracts, conid = conid.Value,
                closing = side == "BUY" && req.ClosingOnly,
            }, statusCode: ok ? 200 : 502);
        })
        .WithName("PlaceOptionLeg");

        // POST /api/integrations/ibkr/options/flatten — buy back EVERY short
        // option at the broker.
        //
        // Owner, 31 Aug 2026: "a auto close one on either profit or end of day"
        // and "lets get in and out at end". This is the end-of-day sweep that
        // index_strangle_close needs, and it works off the BROKER's positions
        // rather than a local ledger — the broker is golden source for what we
        // actually hold, and an EOD flatten driven by a stale ledger would leave
        // exactly the position it believed it had closed.
        //
        // Reports EVERY leg individually. A flatten that closes three of four
        // legs has left a naked short open, and summarising that as "flattened"
        // is the failure this desk has already been bitten by.
        app.MapPost("/integrations/ibkr/options/flatten", async (
            IBKRClient ibkr,
            ILoggerFactory lf,
            CancellationToken ct) =>
        {
            var log = lf.CreateLogger("OptionFlatten");

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

            var pos = await ibkr.GetPositionsAsync(ct, forceFresh: true);
            if (pos.Error is not null)
                return Results.Json(new
                {
                    ok = false, error = $"could not read positions: {pos.Error}. NOTHING was closed.",
                }, statusCode: 502);

            var shorts = pos.Positions
                .Where(p => p.ConId is not null
                         && string.Equals(p.AssetClass, "OPT", StringComparison.OrdinalIgnoreCase)
                         && p.Quantity < 0)
                .ToList();

            if (shorts.Count == 0)
                return Results.Ok(new { ok = true, closed = 0, legs = Array.Empty<object>(),
                                        note = "no short option positions — already flat" });

            var legs = new List<object>();
            var failed = 0;
            foreach (var p in shorts)
            {
                var qty = (int)Math.Abs(p.Quantity);
                log.LogWarning("FLATTEN: BUY {Qty} {Desc} (conid={ConId})",
                    qty, p.ContractDesc ?? p.Symbol, p.ConId);
                var r = await ibkr.PlaceMarketOrderConfirmedAsync(p.ConId!.Value, "BUY", qty, ct);
                var legOk = r.Status == "ACCEPTED" && r.OrderId is not null;
                if (!legOk) failed++;
                legs.Add(new
                {
                    contract = p.ContractDesc ?? p.Symbol, conid = p.ConId, quantity = qty,
                    ok = legOk, orderId = r.OrderId, status = r.Status, reason = r.StatusReason,
                    unrealisedAtClose = p.UnrealizedPnl,
                });
            }

            if (failed > 0)
                log.LogError("FLATTEN INCOMPLETE: {Failed} of {Total} legs still OPEN and NAKED",
                    failed, shorts.Count);

            return Results.Json(new
            {
                ok = failed == 0,
                attempted = shorts.Count, closed = shorts.Count - failed, failed,
                warning = failed > 0
                    ? $"INCOMPLETE — {failed} leg(s) are STILL OPEN and short. Not flat."
                    : null,
                legs,
            }, statusCode: failed == 0 ? 200 : 502);
        })
        .WithName("FlattenShortOptions");

        return app;
    }

    /// <summary>One option contract. Right is P or C; side is BUY or SELL.</summary>
    public sealed record OptionLegRequest(
        string Symbol,
        string Expiry,          // YYYY-MM-DD
        decimal Strike,
        string Right,           // P | C
        string Side,            // BUY | SELL
        int Contracts = 1,
        // Default TRUE: a BUY is presumed to be closing a short, and is refused
        // if we are not short that contract. See the guard for why.
        bool ClosingOnly = true,
        // The contract's OWN id, when the caller already holds it. Skips
        // resolution entirely.
        //
        // WHY: resolving symbol+expiry+strike goes through IBKR's option chain,
        // a progressive snapshot that failed for EVERY symbol on 1 Sep 2026 —
        // including SPY 758P, a contract we were short at that very moment. A
        // close that cannot resolve cannot close. Since a position already
        // carries its conid, re-deriving it through a flaky lookup is throwing
        // away a known-good identifier for no reason.
        long? Conid = null);
}

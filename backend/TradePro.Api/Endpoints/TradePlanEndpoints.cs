using System.Security.Cryptography;
using System.Text;
using TradePro.Api.Oms;
using TradePro.Api.Positions;

namespace TradePro.Api.Endpoints;

/// <summary>
/// /api/trade-plan/{strategy} — what trades would ship if we approved
/// today's algo plan. Derived live from the latest live-portfolio run +
/// broker positions; no separate cache table (always-fresh by design).
///
/// Today-only by design — there is no historical /trade-plan endpoint
/// because the historical record IS the orders that were dispatched
/// (oms_orders) + the decision log that informed them
/// (strategy_decisions).
/// </summary>
public static class TradePlanEndpoints
{
    public static IEndpointRouteBuilder MapTradePlanEndpoints(this IEndpointRouteBuilder app)
    {
        var group = app.MapGroup("/trade-plan").WithTags("TradePlan");

        // GET /api/trade-plan/{strategy}
        // Returns the diff between current broker positions and the
        // algo's latest target portfolio. Read-only — does NOT dispatch
        // orders. Dispatch is a separate explicit action (orders flow
        // through OMS PENDING_APPROVAL like any other intent).
        group.MapGet("/{strategy}", async (
            string strategy, TradePlanService svc, CancellationToken ct) =>
        {
            var plan = await svc.BuildAsync(strategy, ct);
            if (!plan.HasPlan)
            {
                return Results.Ok(new
                {
                    strategy,
                    hasPlan = false,
                    noPlanReason = plan.NoPlanReason,
                });
            }
            // Summary stats for the cockpit chip — "3 buys, 1 sell, $2.4K net flow."
            var buys = plan.Intents.Count(i => i.Side == "BUY");
            var sells = plan.Intents.Count(i => i.Side == "SELL");
            var netFlow = plan.Intents.Sum(i => i.DiffNotional);
            var grossFlow = plan.Intents.Sum(i => Math.Abs(i.DiffNotional));
            return Results.Ok(new
            {
                strategy,
                hasPlan = true,
                runId = plan.RunId,
                asOfUtc = plan.AsOfUtc,
                regimeState = plan.RegimeState,
                portfolioValueUsd = plan.PortfolioValueUsd,
                summary = new
                {
                    nBuys = buys,
                    nSells = sells,
                    nIntents = plan.Intents.Count,
                    nSkipped = plan.Skipped,
                    netFlow,
                    grossFlow,
                    grossFlowPct = plan.PortfolioValueUsd > 0
                        ? grossFlow / plan.PortfolioValueUsd * 100m : 0m,
                },
                intents = plan.Intents.Select(i => new
                {
                    sleeve = i.Sleeve,
                    symbol = i.Symbol,
                    side = i.Side,
                    qty = i.Qty,
                    price = i.Price,
                    targetNotional = i.TargetNotional,
                    currentNotional = i.CurrentNotional,
                    diffNotional = i.DiffNotional,
                    riskClass = i.RiskClass,
                    reason = i.Reason,
                    priceUnavailable = i.PriceUnavailable,
                }),
            });
        });

        // POST /api/trade-plan/{strategy}/execute
        // Take the same plan that /api/trade-plan/{strategy} returns,
        // convert every intent into an OMS order, optionally
        // auto-approve so the order routes straight to the broker.
        //
        // Safety floor: every order still passes through RiskGate +
        // SystemState (so blacklist, size cap, velocity, cash check,
        // and the frozen/panic switch all still apply). Per-order
        // refusal logs to risk_events as usual.
        //
        // body.autoApprove:
        //   false (default) — orders sit in PENDING_APPROVAL; trader
        //                      clicks approve on /oms per order
        //   true            — algo-driven full autonomy: orders auto-
        //                      approve immediately after enqueue,
        //                      RiskGate is the safety net
        group.MapPost("/{strategy}/execute", async (
            string strategy, ExecuteBody? body,
            HttpContext ctx, TradePlanService planSvc, IOmsService oms,
            CancellationToken ct) =>
        {
            var autoApprove = body?.AutoApprove ?? false;
            var actor = ctx.User?.Identity?.Name ?? body?.Actor ?? "algo-auto";

            var plan = await planSvc.BuildAsync(strategy, ct);
            if (!plan.HasPlan)
            {
                return Results.Ok(new
                {
                    strategy, executed = false,
                    reason = plan.NoPlanReason,
                });
            }

            var results = new List<object>();
            int enqueued = 0, approved = 0, rejected = 0, skipped = 0;

            foreach (var intent in plan.Intents)
            {
                if (intent.PriceUnavailable || intent.Qty <= 0)
                {
                    skipped++;
                    results.Add(new
                    {
                        symbol = intent.Symbol, side = intent.Side,
                        status = "skipped",
                        reason = intent.PriceUnavailable
                            ? "broker price unavailable" : "qty <= 0",
                    });
                    continue;
                }

                // Deterministic ClientOrderId so re-runs of the same
                // plan are idempotent — same (strategy, symbol, side,
                // qty, run_id) → same UUID → OMS returns the existing
                // row instead of duplicating.
                var seed = $"{strategy}:{intent.Symbol}:{intent.Side}:" +
                           $"{intent.Qty:0.0000}:{plan.RunId}";
                var clientId = DeterministicGuid(seed);
                var omsIntent = new OrderIntent(
                    ClientOrderId: clientId,
                    Broker: "T212_DEMO",
                    Symbol: ToBrokerTicker(intent.Symbol),
                    Side: intent.Side,
                    Qty: intent.Qty,
                    OrderType: "MKT",
                    StrategyId: strategy);

                OmsOrder enqueuedOrder;
                try
                {
                    enqueuedOrder = await oms.EnqueueAsync(omsIntent, actor);
                    enqueued++;
                }
                catch (Exception ex)
                {
                    rejected++;
                    results.Add(new
                    {
                        symbol = intent.Symbol, side = intent.Side,
                        status = "enqueue_failed", reason = ex.Message,
                    });
                    continue;
                }

                if (!autoApprove)
                {
                    results.Add(new
                    {
                        symbol = intent.Symbol, side = intent.Side,
                        qty = intent.Qty,
                        orderId = enqueuedOrder.Id,
                        status = "pending_approval",
                    });
                    continue;
                }

                // Auto-approve — RiskGate + SystemState run inside
                // ApproveAsync; failures bubble up as
                // InvalidOperationException with the reason text.
                try
                {
                    var done = await oms.ApproveAsync(enqueuedOrder.Id, actor);
                    approved++;
                    results.Add(new
                    {
                        symbol = intent.Symbol, side = intent.Side,
                        qty = intent.Qty,
                        orderId = done.Id,
                        status = done.State,
                    });
                }
                catch (InvalidOperationException ex)
                {
                    rejected++;
                    results.Add(new
                    {
                        symbol = intent.Symbol, side = intent.Side,
                        qty = intent.Qty,
                        orderId = enqueuedOrder.Id,
                        status = "rejected_by_gate",
                        reason = ex.Message,
                    });
                }
            }

            return Results.Ok(new
            {
                strategy,
                executed = true,
                autoApprove,
                runId = plan.RunId,
                portfolioValueUsd = plan.PortfolioValueUsd,
                counts = new
                {
                    nIntents = plan.Intents.Count,
                    enqueued, approved, rejected, skipped,
                },
                results,
            });
        });

        return app;
    }

    // T212 expects "AAPL_US_EQ" style for US equities. Our trade plan
    // emits bare "AAPL" (strategy_decisions.symbol). Translate at the
    // OMS boundary.
    private static string ToBrokerTicker(string symbol)
    {
        var s = symbol.Trim().ToUpperInvariant();
        if (s.Contains('_')) return s; // already in broker form
        // Default to US equity suffix; later we'll consult
        // broker_ticker_map for non-US listings + FX.
        return s + "_US_EQ";
    }

    private static Guid DeterministicGuid(string input)
    {
        using var md5 = MD5.Create();
        var bytes = md5.ComputeHash(Encoding.UTF8.GetBytes(input));
        return new Guid(bytes);
    }

    public sealed record ExecuteBody(bool? AutoApprove, string? Actor);
}

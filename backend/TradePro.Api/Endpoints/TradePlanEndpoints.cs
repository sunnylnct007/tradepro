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

        return app;
    }
}

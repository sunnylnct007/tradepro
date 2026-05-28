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
            Npgsql.NpgsqlDataSource db,
            CancellationToken ct) =>
        {
            var autoApprove = body?.AutoApprove ?? false;
            var actor = ctx.User?.Identity?.Name ?? body?.Actor ?? "algo-auto";

            // Broker selection — priority order so multi-strategy
            // multi-broker setups Just Work:
            //   1. Explicit body.Broker (operator override for testing)
            //   2. strategy_broker_map.broker for this strategy
            //      (per-strategy routing — ichimoku_equity → IG_DEMO,
            //       indian_etf_sleeve → IBKR_PAPER, fx_strategy → IG_DEMO,
            //       us_swing → T212_DEMO, all running in parallel)
            //   3. settings_kv.default_broker (global fallback)
            //   4. Hard-coded T212_DEMO if nothing else found
            await using var conn = await db.OpenConnectionAsync();
            var perStrategyBroker = await Dapper.SqlMapper.ExecuteScalarAsync<string?>(
                conn,
                "SELECT broker FROM strategy_broker_map WHERE strategy_id = @strategy;",
                new { strategy });
            var defaultBroker = await Dapper.SqlMapper.ExecuteScalarAsync<string?>(
                conn,
                "SELECT trim(both '\"' from value::text) FROM app_settings_kv WHERE key = 'default_broker';");
            var brokerLabel = body?.Broker
                ?? (string.IsNullOrWhiteSpace(perStrategyBroker)
                    ? (string.IsNullOrWhiteSpace(defaultBroker) ? "T212_DEMO" : defaultBroker)
                    : perStrategyBroker);

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
                    Broker: brokerLabel,
                    Symbol: ToBrokerTicker(intent.Symbol, brokerLabel),
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
    private static string ToBrokerTicker(string symbol, string brokerLabel)
    {
        var s = symbol.Trim().ToUpperInvariant();
        // T212 — already in T212 suffix form (AAPL_US_EQ) or convert.
        if (brokerLabel.StartsWith("T212", StringComparison.OrdinalIgnoreCase))
        {
            if (s.Contains('_')) return s;
            return s + "_US_EQ";
        }
        // IG — uses EPICs like "US.D.AAPL.CASH.IP". For Day-1 the
        // operator-maintained broker_ticker_map handles the lookup;
        // here we strip any T212 suffix back to the bare ticker so
        // the lookup matches. Real mapping comes in the next IG
        // iteration once we wire broker_ticker_map joins.
        if (brokerLabel.StartsWith("IG", StringComparison.OrdinalIgnoreCase))
        {
            var underscore = s.IndexOf('_');
            return underscore > 0 ? s[..underscore] : s;
        }
        return s;
    }

    private static Guid DeterministicGuid(string input)
    {
        using var md5 = MD5.Create();
        var bytes = md5.ComputeHash(Encoding.UTF8.GetBytes(input));
        return new Guid(bytes);
    }

    public sealed record ExecuteBody(bool? AutoApprove, string? Actor, string? Broker);
}

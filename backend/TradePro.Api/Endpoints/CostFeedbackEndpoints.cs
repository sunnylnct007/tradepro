using Dapper;
using Npgsql;

namespace TradePro.Api.Endpoints;

/// <summary>
/// /api/cost-feedback/{strategy} — diffs realised execution cost vs
/// the backtest's assumed cost (5bps default in QuantEngineConfig).
///
/// Why: the equity pipeline backtest applies cost_bps to each
/// position change. If T212 actual spread + slippage is materially
/// worse than 5bps, the live Sharpe will be lower than the backtest
/// Sharpe and we'll lose trust in the validation. This endpoint is
/// the honesty loop that catches the divergence early.
///
/// Today-only by default; ?since= for explicit historical lookup.
///
/// Computes slippage by comparing fill_price to bar_at_fill_close
/// (the close of the bar at fill-time). The pricer is a rough
/// estimate — for a real institutional cost-attribution model we'd
/// need order timestamps + bid/ask snapshots, which T212's API
/// doesn't surface. The trader-facing answer is "is it close to
/// 5bps?" not "what's the perfect TCA decomposition."
/// </summary>
public static class CostFeedbackEndpoints
{
    // Backtest's default assumed cost (QuantEngineConfig.cost_bps).
    // Plumbed in here as a constant rather than read from settings —
    // it's a backtest property, not a runtime knob.
    private const double BacktestAssumedCostBps = 5.0;

    public static IEndpointRouteBuilder MapCostFeedbackEndpoints(this IEndpointRouteBuilder app)
    {
        var group = app.MapGroup("/cost-feedback").WithTags("CostFeedback");

        // GET /api/cost-feedback/{strategy}?since=
        // Default: today-only. since= for historical comparison.
        group.MapGet("/{strategy}", async (
            string strategy, DateTime? since, NpgsqlDataSource db) =>
        {
            var sinceTs = since ?? DateTime.UtcNow.Date;
            await using var conn = await db.OpenConnectionAsync();

            // Pull every fill for orders that match the strategy in the
            // window. bar_at_fill_close is the reference price we
            // compare fill_price against — when null (older rows), we
            // skip the slippage calc for that fill and only count it
            // toward notional/commission.
            var rows = (await conn.QueryAsync<FillRow>(@"
                SELECT o.id::text AS OrderId, o.symbol, o.side,
                       f.fill_qty AS FillQty,
                       f.fill_price AS FillPrice,
                       f.commission,
                       f.bar_at_fill_close AS BarClose,
                       f.fill_at_utc AS FillAtUtc
                FROM oms_fills f
                JOIN oms_orders o ON o.id = f.order_id
                WHERE o.strategy_id = @strategy
                  AND f.fill_at_utc >= @sinceTs;",
                new { strategy, sinceTs })).ToList();

            if (rows.Count == 0)
            {
                return Results.Ok(new
                {
                    strategy,
                    since = sinceTs,
                    backtestAssumption = new { costBps = BacktestAssumedCostBps },
                    hasData = false,
                    message = "no fills for this strategy in the window",
                });
            }

            // Slippage for one fill, signed by side:
            //   BUY:  positive = paid above the reference close (bad)
            //   SELL: positive = received below the reference close (bad)
            // We normalise so positive == cost to us. Convert to bps.
            var slippagePerFill = new List<double>();
            decimal totalNotional = 0m;
            decimal totalCommission = 0m;
            int nWithSlippage = 0;
            foreach (var r in rows)
            {
                var notional = r.FillQty * r.FillPrice;
                totalNotional += notional;
                totalCommission += r.Commission;
                if (r.BarClose is not null && r.BarClose.Value > 0m)
                {
                    var raw = (double)((r.FillPrice - r.BarClose.Value) / r.BarClose.Value);
                    var signed = r.Side == "BUY" ? raw : -raw;
                    slippagePerFill.Add(signed * 10_000.0);  // → bps
                    nWithSlippage++;
                }
            }

            double? avgSlipBps = slippagePerFill.Count > 0
                ? slippagePerFill.Average() : null;
            double? medianSlipBps = slippagePerFill.Count > 0
                ? Median(slippagePerFill) : null;
            double? maxSlipBps = slippagePerFill.Count > 0
                ? slippagePerFill.Max() : null;
            double? minSlipBps = slippagePerFill.Count > 0
                ? slippagePerFill.Min() : null;

            // Total realised cost = commission + slippage portion of notional.
            // Approximate by applying the average slippage bps to total notional.
            var avgSlipFrac = avgSlipBps.HasValue ? avgSlipBps.Value / 10_000.0 : 0.0;
            var slippageDollars = (decimal)avgSlipFrac * totalNotional;
            var totalRealisedCost = totalCommission + slippageDollars;
            var realisedCostBps = totalNotional > 0m
                ? (double)(totalRealisedCost / totalNotional) * 10_000.0 : 0.0;

            var divergenceBps = realisedCostBps - BacktestAssumedCostBps;
            // 5bps absolute = "material" — half the backtest assumption.
            // If divergence > 5bps, the live system is meaningfully more
            // expensive than the backtest and the validation card needs
            // recalibration.
            var materiallyDiverged = Math.Abs(divergenceBps) > 5.0;

            return Results.Ok(new
            {
                strategy,
                since = sinceTs,
                backtestAssumption = new { costBps = BacktestAssumedCostBps },
                hasData = true,
                actual = new
                {
                    nFills = rows.Count,
                    nFillsWithSlippage = nWithSlippage,
                    totalNotional,
                    totalCommission,
                    avgSlippageBps = avgSlipBps,
                    medianSlippageBps = medianSlipBps,
                    minSlippageBps = minSlipBps,
                    maxSlippageBps = maxSlipBps,
                    estimatedTotalCost = totalRealisedCost,
                    estimatedCostBps = realisedCostBps,
                },
                divergence = new
                {
                    bps = divergenceBps,
                    materiallyDiverged,
                    note = materiallyDiverged
                        ? "live execution materially diverges from the 5bps backtest "
                          + "assumption — re-run the equity pipeline with cost_bps "
                          + "tuned to the realised cost to recalibrate the validation card."
                        : "live cost tracks the backtest assumption within ±5bps — "
                          + "validation card numbers are honest at this scale.",
                },
            });
        });

        return app;
    }

    private static double Median(IList<double> xs)
    {
        var sorted = xs.OrderBy(x => x).ToList();
        var n = sorted.Count;
        return n % 2 == 0
            ? (sorted[n / 2 - 1] + sorted[n / 2]) / 2.0
            : sorted[n / 2];
    }

    private sealed record FillRow(
        string OrderId, string Symbol, string Side,
        decimal FillQty, decimal FillPrice, decimal Commission,
        decimal? BarClose, DateTime FillAtUtc);
}

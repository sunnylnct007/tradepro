using Dapper;
using Npgsql;
using TradePro.Api.Positions;

namespace TradePro.Api.Endpoints;

/// <summary>
/// GET /api/fills/attribution?date=2026-06-06[&amp;strategy=intraday_flat]
/// GET /api/fills/attribution?days=7[&amp;strategy=intraday_flat]
///
/// Per-symbol P&amp;L breakdown from OMS fills for a given date or rolling
/// window. Answers "what caused the loss on Friday?" — every symbol the
/// strategy traded, ranked worst-first, with fill count, win/loss split,
/// and FIFO-realised P&amp;L from the OMS ledger.
///
/// IG-routed strategies: P&amp;L is from FIFO matching of OMS fill prices.
/// The result matches the IG history endpoint directionally; the OMS
/// basis may diverge by spread/financing, so treat this as indicative.
/// For the EXACT GBP realised, use /api/integrations/ig/history.
///
/// T212-routed strategies: same FIFO; T212 has no separate realised feed.
///
/// NOTE: oms_orders.avg_fill_price must be populated (set on FILLED
/// transition). Rows missing avg_fill_price are counted but not P&amp;L'd.
/// </summary>
public static class FillAttributionEndpoints
{
    public static IEndpointRouteBuilder MapFillAttributionEndpoints(
        this IEndpointRouteBuilder app)
    {
        app.MapGet("/api/fills/attribution", async (
            string? date,
            int? days,
            string? strategy,
            NpgsqlDataSource db,
            CancellationToken ct) =>
        {
            // ── date range ────────────────────────────────────────────────
            DateOnly fromDate, toDate;
            if (date is not null)
            {
                if (!DateOnly.TryParse(date, out fromDate))
                    return Results.BadRequest($"Invalid date: {date}");
                toDate = fromDate;
            }
            else
            {
                var window = Math.Clamp(days ?? 1, 1, 90);
                toDate = DateOnly.FromDateTime(DateTime.UtcNow);
                fromDate = toDate.AddDays(-(window - 1));
            }

            // ── fetch filled orders in window ─────────────────────────────
            await using var conn = await db.OpenConnectionAsync(ct);

            var sql = """
                SELECT
                    id, strategy_id, symbol, side, filled_qty, avg_fill_price,
                    last_state_change_at_utc
                FROM oms_orders
                WHERE state = 'FILLED'
                  AND filled_qty > 0
                  AND avg_fill_price IS NOT NULL
                  AND last_state_change_at_utc::date
                        BETWEEN @from AND @to
                  AND (@strategy IS NULL OR strategy_id ILIKE '%' || @strategy || '%')
                ORDER BY last_state_change_at_utc
                """;

            var rows = (await conn.QueryAsync<FillRow>(sql, new
            {
                from = fromDate.ToDateTime(TimeOnly.MinValue),
                to   = toDate.ToDateTime(TimeOnly.MaxValue),
                strategy,
            })).ToList();

            if (rows.Count == 0)
                return Results.Ok(new
                {
                    kind = "fill_attribution",
                    from_date = fromDate,
                    to_date = toDate,
                    message = "No filled orders found for this date/strategy.",
                    total_fills = 0,
                    per_symbol = Array.Empty<object>(),
                });

            // ── group by symbol → FIFO P&L ────────────────────────────────
            var today = DateOnly.FromDateTime(DateTime.UtcNow);
            var bySymbol = rows
                .GroupBy(r => r.Symbol ?? "UNKNOWN")
                .Select(g =>
                {
                    var fills = g.Select(r => new PnlByStrategy.DatedFill(
                        Side: r.Side ?? "BUY",
                        Qty: r.FilledQty,
                        Price: r.AvgFillPrice ?? 0m,
                        IsToday: DateOnly.FromDateTime(r.LastStateChangeAtUtc) == today
                    ));
                    var fifo = PnlByStrategy.FifoRealisedSplit(fills);
                    var allFills = g.ToList();
                    return new
                    {
                        symbol         = g.Key,
                        realised_pnl   = Math.Round(fifo.RealisedLtd, 2),
                        fills          = allFills.Count,
                        closed_trades  = fifo.ClosedTrades,
                        winning_trades = fifo.WinningTrades,
                        win_rate_pct   = fifo.WinRatePct.HasValue
                                            ? Math.Round(fifo.WinRatePct.Value, 1)
                                            : (double?)null,
                        unmatched_sell_qty = fifo.UnmatchedSellQty,
                        buys  = allFills.Count(f => (f.Side ?? "").StartsWith("B", StringComparison.OrdinalIgnoreCase)),
                        sells = allFills.Count(f => (f.Side ?? "").StartsWith("S", StringComparison.OrdinalIgnoreCase)),
                    };
                })
                .OrderBy(r => r.realised_pnl)   // worst first
                .ToList();

            var totalPnl   = bySymbol.Sum(r => r.realised_pnl);
            var totalFills = bySymbol.Sum(r => r.fills);
            var worst = bySymbol.FirstOrDefault();
            var best  = bySymbol.LastOrDefault();

            return Results.Ok(new
            {
                kind        = "fill_attribution",
                from_date   = fromDate,
                to_date     = toDate,
                strategy_filter = strategy,
                total_realised_pnl = Math.Round(totalPnl, 2),
                total_fills = totalFills,
                symbols_traded = bySymbol.Count,
                worst_symbol = worst?.symbol,
                worst_pnl    = worst?.realised_pnl,
                best_symbol  = best?.symbol,
                best_pnl     = best?.realised_pnl,
                per_symbol   = bySymbol,
                note = "P&L is FIFO-matched from OMS fill prices. For IG strategies " +
                       "the exact GBP figure including spread+financing is at " +
                       "/api/integrations/ig/history.",
            });
        });

        return app;
    }

    private sealed record FillRow(
        Guid Id,
        string? StrategyId,
        string? Symbol,
        string? Side,
        decimal FilledQty,
        decimal? AvgFillPrice,
        DateTime LastStateChangeAtUtc);
}

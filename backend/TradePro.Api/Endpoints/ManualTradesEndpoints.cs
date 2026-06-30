using System.Text.Json;
using Dapper;
using Npgsql;
using TradePro.Api.Data.Stores;

namespace TradePro.Api.Endpoints;

/// <summary>
/// /api/paper/manual-trade(s) — manually-booked paper trades from the USER's
/// own analysis (Ichimoku/range-risk checklist, Today's Setups), recorded
/// separately from the signal engine's order flow (zero session conflict).
/// Pillar #3: the decision→outcome journal that scores discretionary calls.
///
///   POST  /api/paper/manual-trade            book a trade
///   GET   /api/paper/manual-trades?status=   list (default all; OPEN/CLOSED)
///   PATCH /api/paper/manual-trade/{id}/close close + compute realised P&L
/// </summary>
public static class ManualTradesEndpoints
{
    public static IEndpointRouteBuilder MapManualTradesEndpoints(this IEndpointRouteBuilder app)
    {
        // GET /api/paper/manual-trades — list, newest first; optional ?status=
        app.MapGet("/paper/manual-trades", async (string? status, NpgsqlDataSource db) =>
        {
            await using var conn = await db.OpenConnectionAsync();
            var rows = await conn.QueryAsync<ManualTradeRow>(@"
                SELECT id, symbol, side, qty, entry_price, entry_time, stop_loss, take_profit,
                       reason, source, checklist_score, currency, status,
                       exit_price, exit_time, exit_reason, pnl_usd, pnl_pct, created_at_utc
                FROM manual_trades
                WHERE (@status IS NULL OR status = @status)
                ORDER BY entry_time DESC;",
                new { status = string.IsNullOrWhiteSpace(status) ? null : status.ToUpperInvariant() });
            return Results.Ok(new { rows });
        });

        // POST /api/paper/manual-trade — book a new OPEN trade.
        app.MapPost("/paper/manual-trade", async (JsonElement b, NpgsqlDataSource db) =>
        {
            if (b.ValueKind != JsonValueKind.Object)
                return Results.BadRequest(new { error = "body must be a JSON object" });
            var symbol = Str(b, "symbol");
            if (string.IsNullOrWhiteSpace(symbol))
                return Results.BadRequest(new { error = "symbol is required" });
            var qty = Dec(b, "qty");
            if (qty is null or 0)
                return Results.BadRequest(new { error = "qty is required and non-zero" });
            var side = (Str(b, "side") ?? "BUY").ToUpperInvariant();
            if (side != "BUY" && side != "SELL")
                return Results.BadRequest(new { error = "side must be BUY or SELL" });

            await using var conn = await db.OpenConnectionAsync();
            var row = await conn.QueryFirstAsync<ManualTradeRow>(@"
                INSERT INTO manual_trades
                  (symbol, side, qty, entry_price, stop_loss, take_profit,
                   reason, source, checklist_score, currency)
                VALUES (@symbol, @side, @qty, @entry, @stop, @target,
                        @reason, @source, @score, @ccy)
                RETURNING id, symbol, side, qty, entry_price, entry_time, stop_loss, take_profit,
                          reason, source, checklist_score, currency, status,
                          exit_price, exit_time, exit_reason, pnl_usd, pnl_pct, created_at_utc;",
                new
                {
                    symbol = symbol.ToUpperInvariant(), side, qty,
                    entry = Dec(b, "entry_price"), stop = Dec(b, "stop_loss"), target = Dec(b, "take_profit"),
                    reason = Str(b, "reason"), source = Str(b, "source") ?? "manual",
                    score = Str(b, "checklist_score"), ccy = (Str(b, "currency") ?? "USD").ToUpperInvariant(),
                });
            return Results.Ok(row);
        });

        // PATCH /api/paper/manual-trade/{id}/close — close + compute realised P&L.
        app.MapPatch("/paper/manual-trade/{id:guid}/close", async (Guid id, JsonElement b, NpgsqlDataSource db) =>
        {
            var exit = Dec(b, "exit_price");
            if (exit is null or 0)
                return Results.BadRequest(new { error = "exit_price is required and non-zero" });
            await using var conn = await db.OpenConnectionAsync();
            var t = await conn.QueryFirstOrDefaultAsync<CloseInfo>(
                "SELECT side, qty, entry_price, status FROM manual_trades WHERE id = @id;", new { id });
            if (t is null) return Results.NotFound(new { error = $"manual trade {id} not found" });
            if (t.status == "CLOSED") return Results.BadRequest(new { error = "trade already closed" });

            decimal? pnlUsd = null, pnlPct = null;
            if (t.entry_price is decimal entry && entry != 0)
            {
                var dir = t.side == "SELL" ? -1m : 1m;       // long: gain on rise; short: gain on fall
                pnlUsd = (exit.Value - entry) * t.qty * dir;
                pnlPct = (exit.Value / entry - 1) * 100m * dir;
            }
            var row = await conn.QueryFirstAsync<ManualTradeRow>(@"
                UPDATE manual_trades
                SET status = 'CLOSED', exit_price = @exit, exit_time = NOW(),
                    exit_reason = @reason, pnl_usd = @pnlUsd, pnl_pct = @pnlPct
                WHERE id = @id
                RETURNING id, symbol, side, qty, entry_price, entry_time, stop_loss, take_profit,
                          reason, source, checklist_score, currency, status,
                          exit_price, exit_time, exit_reason, pnl_usd, pnl_pct, created_at_utc;",
                new { id, exit, reason = Str(b, "exit_reason") ?? "manual", pnlUsd, pnlPct });
            return Results.Ok(row);
        });

        return app;
    }

    private static string? Str(JsonElement o, string k) =>
        o.TryGetProperty(k, out var v) && v.ValueKind == JsonValueKind.String ? v.GetString() : null;

    private static decimal? Dec(JsonElement o, string k) =>
        o.TryGetProperty(k, out var v) && v.ValueKind == JsonValueKind.Number ? v.GetDecimal() : null;

    private sealed record CloseInfo(string side, decimal qty, decimal? entry_price, string status);

    private sealed record ManualTradeRow(
        Guid id, string symbol, string side, decimal qty, decimal? entry_price, DateTime entry_time,
        decimal? stop_loss, decimal? take_profit, string? reason, string source, string? checklist_score,
        string currency, string status, decimal? exit_price, DateTime? exit_time, string? exit_reason,
        decimal? pnl_usd, decimal? pnl_pct, DateTime created_at_utc);
}

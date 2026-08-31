using Dapper;
using Npgsql;

namespace TradePro.Api.Endpoints;

/// <summary>
/// /api/strangle-manual-trades — REAL fills from manually-placed strangles.
///
/// Owner, 31 Aug 2026: "it got closed but lets record these so we can learn
/// from it", and on why it matters — "we need to start storing these execution
/// data as no platform will provide these for free".
///
/// THE TABLE EXISTED WITH NO WAY IN OR OUT. Migration 070 created
/// strangle_manual_trade and nothing was ever wired to it — no reader, no
/// writer. So the owner's BANKNIFTY trade had nowhere to go, and a GET against
/// the obvious path returned nothing. A table with no endpoint is the same as
/// no table, which is the same lesson as the wheel board that screened 82 names
/// daily for weeks with no nav entry.
///
/// WHY THESE ROWS ARE THE MOST VALUABLE IN THE PROJECT. Every published figure
/// for this strategy is Black-Scholes off a volatility index — no skew, no
/// bid-ask, and no evidence anyone would be filled there. On 31 Aug the model
/// said roughly -12,000 on 150 lots while the real position made +396 on 30.
/// These are the only honest prices this project will ever have, and they
/// CANNOT be backfilled — an unrecorded fill is gone.
/// </summary>
public static class StrangleManualTradeEndpoints
{
    public sealed record ManualTrade(
        string Market, DateTime EntryDate,
        string? Account = null, string? Product = null,
        DateTime? ExitDate = null, DateTime? Expiry = null,
        int Lots = 1, int? LotSize = null,
        decimal? PutStrike = null, decimal? PutEntry = null, decimal? PutExit = null,
        decimal? CallStrike = null, decimal? CallEntry = null, decimal? CallExit = null,
        decimal? IndexEntry = null, decimal? IndexExit = null,
        decimal? RealisedPnl = null, string? Currency = null,
        // The single most useful column for learning: it separates "the
        // strategy did this" from "I did this". On 31 Aug they differed on
        // every leg — the email said 56,600/58,200 and the trades placed were
        // 56,900/57,900 and 56,900/58,000.
        bool? FollowedSignal = null,
        decimal? SignalPutStrike = null, decimal? SignalCallStrike = null,
        string? Notes = null);

    public static IEndpointRouteBuilder MapStrangleManualTradeEndpoints(
        this IEndpointRouteBuilder app)
    {
        var g = app.MapGroup("/strangle-manual-trades").WithTags("StrangleManualTrades");

        // POST — book one real trade. Deliberately permissive about what is
        // known: a trade recorded with only strikes and a P&L is far better
        // than one not recorded because the leg prices were not to hand.
        // Precision can be added later; a fill nobody wrote down is gone.
        g.MapPost("/", async (ManualTrade t, NpgsqlDataSource db, CancellationToken ct) =>
        {
            if (t is null || string.IsNullOrWhiteSpace(t.Market))
                return Results.BadRequest(new { error = "market and entryDate required" });

            await using var conn = await db.OpenConnectionAsync(ct);
            var id = await conn.ExecuteScalarAsync<long>(@"
                INSERT INTO strangle_manual_trade
                    (market, account, product, entry_date, exit_date, expiry,
                     lots, lot_size, put_strike, put_entry, put_exit,
                     call_strike, call_entry, call_exit, index_entry, index_exit,
                     realised_pnl, currency, followed_signal,
                     signal_put_strike, signal_call_strike, notes)
                VALUES
                    (@Market, @Account, @Product, @EntryDate, @ExitDate, @Expiry,
                     @Lots, @LotSize, @PutStrike, @PutEntry, @PutExit,
                     @CallStrike, @CallEntry, @CallExit, @IndexEntry, @IndexExit,
                     @RealisedPnl, @Currency, @FollowedSignal,
                     @SignalPutStrike, @SignalCallStrike, @Notes)
                RETURNING id;", t);
            return Results.Ok(new { ok = true, id });
        });

        // GET — newest first. `market` narrows; `days` bounds.
        g.MapGet("/", async (NpgsqlDataSource db, string? market, int days,
                             CancellationToken ct) =>
        {
            await using var conn = await db.OpenConnectionAsync(ct);
            var rows = await conn.QueryAsync(@"
                SELECT id, market, account, product, entry_date, exit_date, expiry,
                       lots, lot_size, put_strike, put_entry, put_exit,
                       call_strike, call_entry, call_exit, index_entry, index_exit,
                       realised_pnl, currency, followed_signal,
                       signal_put_strike, signal_call_strike, notes, recorded_at_utc
                  FROM strangle_manual_trade
                 WHERE (@market IS NULL OR market = @market)
                   AND entry_date >= CURRENT_DATE - (@days || ' days')::interval
                 ORDER BY entry_date DESC, id DESC;",
                new { market = string.IsNullOrWhiteSpace(market) ? null : market.ToUpperInvariant(),
                      days = days <= 0 ? 90 : days });
            return Results.Ok(new { rows });
        });

        // GET /summary — what the real fills actually did, per market.
        //
        // Reports the count WITH a P&L separately from the total count. A mean
        // computed over rows that mostly lack a P&L is a number that looks like
        // evidence and is not, which is the failure mode this whole table
        // exists to correct.
        g.MapGet("/summary", async (NpgsqlDataSource db, int days, CancellationToken ct) =>
        {
            await using var conn = await db.OpenConnectionAsync(ct);
            var rows = await conn.QueryAsync(@"
                SELECT market,
                       COUNT(*)                                    AS trades,
                       COUNT(realised_pnl)                         AS with_pnl,
                       COUNT(*) FILTER (WHERE exit_date IS NULL)   AS still_open,
                       COUNT(*) FILTER (WHERE followed_signal)     AS followed_signal,
                       SUM(realised_pnl)                           AS total_pnl,
                       AVG(realised_pnl)                           AS mean_pnl,
                       MIN(realised_pnl)                           AS worst_pnl
                  FROM strangle_manual_trade
                 WHERE entry_date >= CURRENT_DATE - (@days || ' days')::interval
                 GROUP BY market ORDER BY market;",
                new { days = days <= 0 ? 90 : days });
            return Results.Ok(new
            {
                rows,
                note = "with_pnl is reported separately from trades on purpose — a mean "
                     + "over rows that mostly lack a P&L looks like evidence and is not.",
            });
        });

        return app;
    }
}

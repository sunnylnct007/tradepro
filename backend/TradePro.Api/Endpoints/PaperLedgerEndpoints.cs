using Dapper;
using Npgsql;
using TradePro.Api.Positions;

namespace TradePro.Api.Endpoints;

/// <summary>
/// GET /api/pnl/ledger — the paper P&amp;L ledger (see <see cref="PaperLedger"/>).
///
/// Reads every non-deleted OMS fill once, hands the rows to the pure builder,
/// and attaches the one broker-side fact the ledger cannot compute from the
/// OMS: how many broker positions are currently UNEXPLAINED by any order
/// (open rows in position_drift_events). That count is the honest caveat on
/// every strategy line — a sleeve's realised figure is only as complete as the
/// fills the broker reported to us.
///
/// PAPER ONLY by construction: oms_orders never holds a live-account order
/// (OWNER BASICS 1); the payload says so in `account`.
/// </summary>
public static class PaperLedgerEndpoints
{
    public static IEndpointRouteBuilder MapPaperLedgerEndpoints(this IEndpointRouteBuilder app)
    {
        app.MapGet("/pnl/ledger", async (NpgsqlDataSource db, CancellationToken ct) =>
        {
            var utc = DateTime.UtcNow;
            string? error = null;
            var rows = new List<PaperLedger.FillRow>();
            int openDrift = 0; string? driftError = null;
            try
            {
                await using var conn = await db.OpenConnectionAsync(ct);
                var raw = await conn.QueryAsync<(string? strategy_id, string broker, string symbol, string side,
                                                 decimal qty, decimal price, string? currency,
                                                 string? broker_fill_id, string? broker_order_id, DateTime fill_at_utc)>(@"
                    SELECT o.strategy_id, o.broker, o.symbol, o.side,
                           f.qty, f.price, f.currency,
                           f.broker_fill_id, o.broker_order_id, f.fill_at_utc
                    FROM oms_orders o
                    JOIN oms_fills f ON f.order_id = o.id
                    WHERE o.deleted_at IS NULL
                    ORDER BY f.fill_at_utc ASC, f.id ASC;");
                foreach (var r in raw)
                    rows.Add(new PaperLedger.FillRow(
                        r.strategy_id, r.broker, r.symbol, r.side, r.qty, r.price,
                        r.currency ?? "USD", r.broker_fill_id, r.broker_order_id,
                        DateTime.SpecifyKind(r.fill_at_utc, DateTimeKind.Utc)));
                try
                {
                    openDrift = await conn.ExecuteScalarAsync<int>(
                        "SELECT COUNT(*)::int FROM position_drift_events WHERE resolved_at_utc IS NULL;");
                }
                catch (Exception ex) { driftError = ex.Message; }
            }
            catch (Exception ex)
            {
                error = ex.Message;
            }

            var ledger = PaperLedger.Build(rows, utc);
            return Results.Ok(new
            {
                utc,
                account = "PAPER · SIMULATED, NO REAL MONEY",
                error,
                basis = ledger.Basis,
                fillsSeen = ledger.FillsSeen,
                confirmedFills = ledger.ConfirmedFills,
                assumedFills = ledger.AssumedFills,
                // Broker positions no order explains — the other half of "unattributed".
                unexplainedBrokerPositions = driftError is null ? openDrift : (int?)null,
                unexplainedBrokerPositionsError = driftError,
                strategies = ledger.Strategies,
                unattributed = ledger.Unattributed,
            });
        })
        .WithTags("Pnl")
        .WithName("GetPaperLedger");
        return app;
    }
}

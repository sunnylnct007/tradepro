using Dapper;
using Npgsql;
using TradePro.Api.Providers.IBKR;

namespace TradePro.Api.Endpoints;

/// <summary>
/// /api/options/* — the Options Desk (wheel) surfaces. v1: the candidate
/// SCREEN snapshot. The Mac-side `tradepro-options-screen` job computes IV-Rank
/// (IBKR) + regime (bar cache) + runs the risk engine, then POSTs the whole
/// screen here; the Options tab GETs it. Mirrors the bar-cache-health feed
/// (Mac computes → API stores → frontend reads). Empty default until the first
/// screen runs — never a 404 the UI has to special-case.
/// </summary>
public static class OptionsEndpoints
{
    private const string EmptyScreen =
        "{\"generated_at_utc\":null,\"market_open\":false,\"candidates\":[]}";

    public static IEndpointRouteBuilder MapOptionsEndpoints(this IEndpointRouteBuilder app)
    {
        var g = app.MapGroup("/options").WithTags("Options");

        // Latest wheel candidate screen (empty default if none pushed yet).
        g.MapGet("/candidates", async (NpgsqlDataSource db) =>
        {
            await using var conn = await db.OpenConnectionAsync();
            var json = await conn.ExecuteScalarAsync<string?>(
                "SELECT payload::text FROM options_candidate_screen WHERE id = 1");
            return Results.Content(
                string.IsNullOrWhiteSpace(json) ? EmptyScreen : json!, "application/json");
        });

        // Mac screen job pushes the latest screen (whole payload, verbatim JSONB).
        g.MapPost("/candidates", async (HttpContext ctx, NpgsqlDataSource db) =>
        {
            using var reader = new StreamReader(ctx.Request.Body);
            var body = await reader.ReadToEndAsync();
            if (string.IsNullOrWhiteSpace(body))
                return Results.BadRequest(new { error = "empty body" });
            await using var conn = await db.OpenConnectionAsync();
            await conn.ExecuteAsync(@"
                INSERT INTO options_candidate_screen (id, payload, updated_at_utc)
                VALUES (1, @p::jsonb, NOW())
                ON CONFLICT (id) DO UPDATE SET
                    payload = EXCLUDED.payload, updated_at_utc = NOW();",
                new { p = body });
            return Results.Ok(new { ok = true });
        });

        // ── Paper wheel positions (BRD §11 ledger) ──────────────────
        // Record a paper CSP entry + its risk-engine verdict; list/track them.
        g.MapGet("/positions", async (NpgsqlDataSource db, string? state) =>
        {
            await using var conn = await db.OpenConnectionAsync();
            var rows = await conn.QueryAsync(@"
                SELECT id, symbol, structure, state, strike::float8, expiry, dte,
                       delta::float8, iv_rank::float8, premium::float8, contracts,
                       cash_secured_gbp::float8, regime, opened_at_utc, closed_at_utc,
                       realised_pnl_gbp::float8, notes, risk_decision::text AS risk_decision_json,
                       updated_at_utc
                FROM options_paper_position
                WHERE (@state IS NULL OR state = @state)
                ORDER BY opened_at_utc DESC;",
                new { state });
            return Results.Ok(new { positions = rows.AsList() });
        });

        g.MapPost("/positions", async (PaperPositionBody body, NpgsqlDataSource db) =>
        {
            if (string.IsNullOrWhiteSpace(body.Symbol))
                return Results.BadRequest(new { error = "symbol required" });
            await using var conn = await db.OpenConnectionAsync();
            var id = await conn.ExecuteScalarAsync<long>(@"
                INSERT INTO options_paper_position
                    (symbol, structure, state, strike, expiry, dte, delta, iv_rank,
                     premium, contracts, cash_secured_gbp, regime, notes, risk_decision)
                VALUES
                    (@Symbol, COALESCE(@Structure,'CASH_SECURED_PUT'), COALESCE(@State,'SHORT_PUT_OPEN'),
                     @Strike, @Expiry::date, @Dte, @Delta, @IvRank,
                     @Premium, COALESCE(@Contracts,1), @CashSecuredGbp, @Regime, @Notes,
                     @RiskDecision::jsonb)
                RETURNING id;",
                new
                {
                    body.Symbol, body.Structure, body.State, body.Strike, body.Expiry,
                    body.Dte, body.Delta, body.IvRank, body.Premium, body.Contracts,
                    body.CashSecuredGbp, body.Regime, body.Notes,
                    RiskDecision = string.IsNullOrWhiteSpace(body.RiskDecisionJson) ? null : body.RiskDecisionJson,
                });
            return Results.Ok(new { ok = true, id });
        });

        // State transition / close (e.g. assigned, rolled, closed with P&L).
        g.MapPost("/positions/{id:long}/event", async (long id, PaperPositionEventBody body, NpgsqlDataSource db) =>
        {
            await using var conn = await db.OpenConnectionAsync();
            var n = await conn.ExecuteAsync(@"
                UPDATE options_paper_position SET
                    state            = COALESCE(@State, state),
                    realised_pnl_gbp = COALESCE(@RealisedPnlGbp, realised_pnl_gbp),
                    closed_at_utc    = CASE WHEN @State = 'CLOSED' THEN NOW() ELSE closed_at_utc END,
                    notes            = COALESCE(@Notes, notes),
                    updated_at_utc   = NOW()
                WHERE id = @id;",
                new { id, body.State, body.RealisedPnlGbp, body.Notes });
            return n == 0 ? Results.NotFound(new { error = "no such position" }) : Results.Ok(new { ok = true });
        });

        // Remove a paper position (mis-entry / cleanup). Paper ledger only.
        g.MapDelete("/positions/{id:long}", async (long id, NpgsqlDataSource db) =>
        {
            await using var conn = await db.OpenConnectionAsync();
            var n = await conn.ExecuteAsync(
                "DELETE FROM options_paper_position WHERE id = @id;", new { id });
            return n == 0 ? Results.NotFound(new { error = "no such position" }) : Results.Ok(new { ok = true });
        });

        // ── Position watchdog (v1 §F0.1 + BABA addendum) ─────────────
        // Expiry clock + assignment-risk (moneyness) + a dead-collateral flag
        // for every OPEN paper position — the thing D6c calls "does the trader
        // need to look at this today". Live spot comes from the same IBKRClient
        // G3 already uses (GetSnapshotRawAsync field 31), so a symbol that fails
        // to resolve surfaces spot=null + a visible reason rather than a fabricated
        // "all clear" (NO FALSE POSITIVES). Read-only — never mutates a position.
        g.MapGet("/watchdog", async (NpgsqlDataSource db, IBKRClient ibkr, CancellationToken ct) =>
        {
            await using var conn = await db.OpenConnectionAsync(ct);
            var rows = (await conn.QueryAsync(@"
                SELECT id, symbol, structure, state, strike::float8 AS strike,
                       expiry, dte, delta::float8 AS delta, contracts,
                       cash_secured_gbp::float8 AS cash_secured_gbp,
                       opened_at_utc, premium::float8 AS premium
                FROM options_paper_position
                WHERE state NOT IN ('CLOSED')
                ORDER BY expiry NULLS LAST;")).ToList();

            var today = DateOnly.FromDateTime(DateTime.UtcNow);
            var alerts = new List<WatchdogAlert>();
            foreach (var r in rows)
            {
                string symbol = r.symbol;
                DateOnly? expiry = r.expiry is DateTime dt ? DateOnly.FromDateTime(dt) : null;
                int? daysToExpiry = expiry.HasValue ? expiry.Value.DayNumber - today.DayNumber : null;

                string expiryUrgency = daysToExpiry switch
                {
                    null => "unknown",
                    <= 0 => "expired",
                    <= 5 => "urgent",
                    <= 10 => "warn",
                    _ => "ok",
                };

                // Live spot — best-effort. A resolve/fetch failure surfaces as
                // spot=null + error text; the row still renders (expiry clock
                // alone is still useful), it just can't show moneyness.
                decimal? spot = null;
                string? spotError = null;
                try
                {
                    var conid = await ibkr.ResolveConidAsync(symbol, "STK", ct);
                    if (conid is null)
                    {
                        spotError = $"no IBKR contract for {symbol}";
                    }
                    else
                    {
                        var raw = await ibkr.GetSnapshotRawAsync(conid.Value, "31", ct);
                        spot = raw is not null ? IBKRResponseParser.ParseSnapshotLast(raw) : null;
                        if (spot is null) spotError = $"no live snapshot for {symbol}";
                    }
                }
                catch (Exception ex)
                {
                    spotError = ex.Message;
                }

                // Moneyness — CASH_SECURED_PUT only for now (the covered-call leg
                // has the opposite ITM direction; add when the wheel actually
                // reaches that state in the ledger). Distance is signed: negative
                // = spot below strike = assignment risk.
                decimal? strike = r.strike;
                decimal? distancePct = null;
                string? moneyness = null;
                if (spot is decimal s && strike is decimal k && s > 0)
                {
                    distancePct = (s - k) / s * 100m;
                    moneyness = s < k ? "ITM (assignment risk)" : "OTM";
                }

                // Dead-collateral heuristic — deep OTM with most of the contract's
                // life still ahead: cash is tied up capturing very little further
                // theta. No live option mark needed for this first pass; refine
                // with a real premium/greeks check (G3) once this is proven useful.
                bool deadCollateral = distancePct is decimal d && d > 20m
                    && daysToExpiry is int dte && dte > 15;

                alerts.Add(new WatchdogAlert(
                    Id: (long)r.id,
                    Symbol: symbol,
                    Structure: (string)r.structure,
                    State: (string)r.state,
                    Strike: strike,
                    Expiry: expiry,
                    DaysToExpiry: daysToExpiry,
                    ExpiryUrgency: expiryUrgency,
                    Spot: spot,
                    SpotError: spotError,
                    DistancePct: distancePct.HasValue ? Math.Round(distancePct.Value, 1) : (decimal?)null,
                    Moneyness: moneyness,
                    DeadCollateral: deadCollateral,
                    Contracts: (int)r.contracts,
                    CashSecuredGbp: (decimal?)r.cash_secured_gbp,
                    Premium: (decimal?)r.premium));
            }

            var needsAttention = alerts.Count(a =>
                a.ExpiryUrgency is "urgent" or "expired" || a.DeadCollateral
                || a.Moneyness == "ITM (assignment risk)");

            return Results.Ok(new
            {
                generatedAtUtc = DateTime.UtcNow,
                count = alerts.Count,
                needsAttention,
                positions = alerts,
            });
        });

        return app;
    }

    public sealed record PaperPositionBody(
        string Symbol, string? Structure, string? State, decimal? Strike, string? Expiry,
        int? Dte, decimal? Delta, decimal? IvRank, decimal? Premium, int? Contracts,
        decimal? CashSecuredGbp, string? Regime, string? Notes, string? RiskDecisionJson);

    public sealed record PaperPositionEventBody(string? State, decimal? RealisedPnlGbp, string? Notes);

    public sealed record WatchdogAlert(
        long Id, string Symbol, string Structure, string State,
        decimal? Strike, DateOnly? Expiry, int? DaysToExpiry, string ExpiryUrgency,
        decimal? Spot, string? SpotError, decimal? DistancePct, string? Moneyness,
        bool DeadCollateral, int Contracts, decimal? CashSecuredGbp, decimal? Premium);
}

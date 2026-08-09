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

        // ── IV-history dataset (OAuth-only IV-Rank, migration 061) ───
        // The Mac/AWS screen job snapshots current IV (IBKR Web API field
        // 7283) per wheel underlying and upserts it here daily; IV-Rank is
        // then computed from OUR accumulated series — the honest replacement
        // for the retired Gateway's OPTION_IMPLIED_VOLATILITY history.
        g.MapPost("/iv-daily", async (IvDailyBatchBody body, NpgsqlDataSource db) =>
        {
            if (body?.Rows is null || body.Rows.Count == 0)
                return Results.BadRequest(new { error = "rows required" });
            var bad = body.Rows.FirstOrDefault(r =>
                string.IsNullOrWhiteSpace(r.Symbol) || r.Iv <= 0 || r.Iv > 5.0);
            if (bad is not null)
                return Results.BadRequest(new
                {
                    error = $"invalid row for '{bad?.Symbol}': iv must be a fraction in (0, 5] "
                          + "(0.25 = 25% annualised) — refusing to poison the rank dataset",
                });
            await using var conn = await db.OpenConnectionAsync();
            foreach (var r in body.Rows)
            {
                await conn.ExecuteAsync(@"
                    INSERT INTO options_iv_daily (symbol, trade_date, iv, hv30, source)
                    VALUES (@Symbol, COALESCE(@TradeDate::date, CURRENT_DATE), @Iv, @Hv30,
                            COALESCE(@Source, 'ibkr_web_7283'))
                    ON CONFLICT (symbol, trade_date) DO UPDATE SET
                        iv = EXCLUDED.iv, hv30 = EXCLUDED.hv30,
                        source = EXCLUDED.source, captured_at_utc = NOW();",
                    new { Symbol = r.Symbol.Trim().ToUpperInvariant(), r.TradeDate, r.Iv, r.Hv30, r.Source });
            }
            return Results.Ok(new { ok = true, upserted = body.Rows.Count });
        });

        // Accumulated IV series for one symbol, oldest→newest, capped to ~52w.
        // The caller (iv_rank_from_history) decides whether the window is deep
        // enough to call the result a rank — this endpoint just serves the data
        // plus the window depth so nobody has to guess.
        g.MapGet("/iv-daily/{symbol}", async (string symbol, NpgsqlDataSource db, int? days) =>
        {
            var sym = symbol.Trim().ToUpperInvariant();
            var window = days is > 0 and <= 400 ? days.Value : 370;
            await using var conn = await db.OpenConnectionAsync();
            var rows = (await conn.QueryAsync(@"
                SELECT trade_date, iv, hv30, source
                FROM options_iv_daily
                WHERE symbol = @sym AND trade_date >= CURRENT_DATE - @window
                ORDER BY trade_date ASC;",
                new { sym, window })).AsList();
            return Results.Ok(new
            {
                symbol = sym,
                days = rows.Count,
                windowDays = window,
                series = rows,
            });
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
        g.MapGet("/watchdog", async (NpgsqlDataSource db, IBKRClient ibkr, CancellationToken ct,
            decimal? profitTarget) =>
        {
            // The wheel's profit-capture rule: captured ≥ this % of the entry
            // premium → suggest closing and redeploying (owner: "75% recovered
            // — roll it off to a better one"). Query-tunable for the UI knob.
            var target = profitTarget is > 0 and <= 100 ? profitTarget.Value : 75m;
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
                //
                // Same IBKR snapshot warm-up quirk live-verified on the G3 chain
                // feed: a conid's first marketdata/snapshot request often comes
                // back empty, a retry ~1s later returns the real value. One retry.
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
                        if (spot is null)
                        {
                            await Task.Delay(TimeSpan.FromSeconds(1.2), ct);
                            raw = await ibkr.GetSnapshotRawAsync(conid.Value, "31", ct);
                            spot = raw is not null ? IBKRResponseParser.ParseSnapshotLast(raw) : null;
                        }
                        if (spot is null) spotError = $"no live snapshot for {symbol} after warm-up retry";
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
                decimal? strike = (decimal?)(double?)r.strike;
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

                // Live option mark → premium-captured % → the roll rule. Chain
                // resolution is per-position (months → contract at the stored
                // strike → snapshot); max_positions caps this at a handful of
                // IBKR calls. Any failure = mark null + visible reason — the
                // suggestion then simply doesn't render (never fabricated).
                decimal? currentMark = null;
                string? markError = null;
                var right = (string)r.structure == "COVERED_CALL" ? "C" : "P";
                decimal? entryPremium = (decimal?)(double?)r.premium;
                if (strike is decimal k2 && expiry is DateOnly exp && daysToExpiry is > 0)
                {
                    try
                    {
                        var monthLabel = ExpiryToMonthLabel(exp);
                        var mr = await ibkr.GetOptionMonthsAsync(symbol, ct);
                        if (mr.Error is not null || mr.ConId is null)
                            markError = mr.Error ?? $"no underlying contract for {symbol}";
                        else if (!mr.Months.Contains(monthLabel, StringComparer.OrdinalIgnoreCase))
                            markError = $"expiry month {monthLabel} not in listed months ({string.Join(",", mr.Months.Take(6))})";
                        else
                        {
                            var cr = await ibkr.GetOptionContractsAsync(mr.ConId.Value, monthLabel, k2, right, ct);
                            var contract = cr.Contracts.FirstOrDefault();
                            if (contract is null)
                                markError = cr.Error ?? $"no {right} contract at strike {k2} {monthLabel}";
                            else
                            {
                                var qr = await ibkr.GetOptionSnapshotBatchAsync(new[] { contract.ConId }, ct);
                                var q = qr.Quotes.FirstOrDefault();
                                if (q is not null && (q.Bid is null && q.Ask is null && q.Last is null))
                                {
                                    // Same snapshot warm-up quirk as spot above.
                                    await Task.Delay(TimeSpan.FromSeconds(1.2), ct);
                                    qr = await ibkr.GetOptionSnapshotBatchAsync(new[] { contract.ConId }, ct);
                                    q = qr.Quotes.FirstOrDefault();
                                }
                                if (q is null)
                                    markError = qr.Error ?? "no option quote returned";
                                else if (q.Bid is decimal b2 && q.Ask is decimal a2 && a2 >= b2 && b2 >= 0)
                                    currentMark = Math.Round((b2 + a2) / 2m, 4);
                                else if (q.Last is decimal l2 && l2 > 0)
                                    currentMark = l2;   // stale-but-real; better than nothing, still not fabricated
                                else
                                    markError = "option quote had no usable bid/ask/last after warm-up retry";
                            }
                        }
                    }
                    catch (Exception ex)
                    {
                        markError = ex.Message;
                    }
                }
                var capturedPct = PremiumCapturedPct(entryPremium, currentMark);
                var suggestion = RollSuggestion(capturedPct, moneyness, target);

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
                    CashSecuredGbp: (decimal?)(double?)r.cash_secured_gbp,
                    Premium: entryPremium,
                    CurrentMark: currentMark,
                    MarkError: markError,
                    PremiumCapturedPct: capturedPct,
                    Suggestion: suggestion));
            }

            var needsAttention = alerts.Count(a =>
                a.ExpiryUrgency is "urgent" or "expired" || a.DeadCollateral
                || a.Moneyness == "ITM (assignment risk)"
                || a.Suggestion is not null);

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

    // ── Pure helpers (unit-tested) ───────────────────────────────────

    /// <summary>IBKR month label for an expiry date — "SEP26" for 2026-09-18.
    /// The chain endpoints key months by this label, so the watchdog can find
    /// the live mark for a stored position's expiry.</summary>
    public static string ExpiryToMonthLabel(DateOnly expiry)
        => expiry.ToString("MMM", System.Globalization.CultureInfo.InvariantCulture).ToUpperInvariant()
           + expiry.ToString("yy", System.Globalization.CultureInfo.InvariantCulture);

    /// <summary>Share of the entry premium already captured by a SHORT option:
    /// (entry − current mark) / entry × 100. Positive = winning (option decayed);
    /// negative = the short is under water. Null when either input is missing or
    /// the entry premium is non-positive — never a fabricated 0%.</summary>
    public static decimal? PremiumCapturedPct(decimal? entryPremium, decimal? currentMark)
    {
        if (entryPremium is not > 0m || currentMark is null) return null;
        return Math.Round((entryPremium.Value - currentMark.Value) / entryPremium.Value * 100m, 1);
    }

    /// <summary>The wheel's management rule (owner's "75% recovered — roll it
    /// to a better one"): captured ≥ target → close and redeploy the collateral
    /// into the screen's current best candidate; short put gone ITM → the
    /// decision is roll-vs-accept-assignment. Null = leave it alone.</summary>
    public static string? RollSuggestion(decimal? capturedPct, string? moneyness, decimal profitTargetPct)
    {
        if (moneyness is not null && moneyness.StartsWith("ITM", StringComparison.Ordinal))
            return "ROLL_OR_ACCEPT_ASSIGNMENT";
        if (capturedPct is decimal c && c >= profitTargetPct)
            return "CLOSE_AND_REDEPLOY";
        return null;
    }

    public sealed record IvDailyRow(
        string Symbol, string? TradeDate, double Iv, double? Hv30, string? Source);

    public sealed record IvDailyBatchBody(List<IvDailyRow> Rows);

    public sealed record PaperPositionBody(
        string Symbol, string? Structure, string? State, decimal? Strike, string? Expiry,
        int? Dte, decimal? Delta, decimal? IvRank, decimal? Premium, int? Contracts,
        decimal? CashSecuredGbp, string? Regime, string? Notes, string? RiskDecisionJson);

    public sealed record PaperPositionEventBody(string? State, decimal? RealisedPnlGbp, string? Notes);

    public sealed record WatchdogAlert(
        long Id, string Symbol, string Structure, string State,
        decimal? Strike, DateOnly? Expiry, int? DaysToExpiry, string ExpiryUrgency,
        decimal? Spot, string? SpotError, decimal? DistancePct, string? Moneyness,
        bool DeadCollateral, int Contracts, decimal? CashSecuredGbp, decimal? Premium,
        decimal? CurrentMark = null, string? MarkError = null,
        decimal? PremiumCapturedPct = null, string? Suggestion = null);
}

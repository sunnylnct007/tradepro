using System.Text.Json;
using Dapper;
using Npgsql;
using TradePro.Api.Providers;

namespace TradePro.Api.Endpoints;

/// <summary>
/// /api/verdicts — the verdict store (TRADEPRO_REDESIGN_SPEC.md §5). System of
/// record for every swing/wheel/invest call Symbol Lab (or any future caller)
/// makes, logged with its counter-case up front and scored against what
/// actually happened once its horizon passes. Raw Dapper over NpgsqlDataSource,
/// same shape as AdminEndpoints' strategy-broker-map — no dedicated service
/// layer needed yet for a straight CRUD + aggregate surface this small.
/// </summary>
public static class VerdictsEndpoints
{
    public static IEndpointRouteBuilder MapVerdictsEndpoints(this IEndpointRouteBuilder app)
    {
        var group = app.MapGroup("/verdicts").WithTags("Verdicts");

        // POST /api/verdicts — body = row minus id/ts. counter_case is
        // mandatory (spec: "no verdict without the argument against it").
        group.MapPost("/", async (VerdictPostBody body, NpgsqlDataSource db, CancellationToken ct) =>
        {
            if (string.IsNullOrWhiteSpace(body.Symbol))
                return Results.BadRequest(new { error = "symbol is required" });
            if (string.IsNullOrWhiteSpace(body.Horizon)
                || !new[] { "swing", "wheel", "invest" }.Contains(body.Horizon))
                return Results.BadRequest(new { error = "horizon must be one of: swing, wheel, invest" });
            if (string.IsNullOrWhiteSpace(body.Verdict)
                || !new[] { "YES", "NO", "NOT_YET", "WAIT" }.Contains(body.Verdict))
                return Results.BadRequest(new { error = "verdict must be one of: YES, NO, NOT_YET, WAIT" });
            if (string.IsNullOrWhiteSpace(body.Why))
                return Results.BadRequest(new { error = "why is required" });
            if (string.IsNullOrWhiteSpace(body.CounterCase))
                return Results.BadRequest(new { error = "counter_case is required — no verdict without the argument against it" });
            if (body.Evidence is null)
                return Results.BadRequest(new { error = "evidence is required" });

            await using var conn = await db.OpenConnectionAsync(ct);
            var id = await conn.ExecuteScalarAsync<long>(new CommandDefinition(@"
                INSERT INTO verdicts
                    (user_id, symbol, spot, horizon, verdict, spec, why, counter_case, evidence, source)
                VALUES
                    (@user_id, @symbol, @spot, @horizon, @verdict, @spec::jsonb, @why, @counter_case, @evidence::jsonb, @source)
                RETURNING id;",
                new
                {
                    user_id = body.UserId ?? 1,
                    symbol = body.Symbol.Trim().ToUpperInvariant(),
                    spot = body.Spot,
                    horizon = body.Horizon,
                    verdict = body.Verdict,
                    spec = body.Spec.HasValue ? body.Spec.Value.GetRawText() : null,
                    why = body.Why,
                    counter_case = body.CounterCase,
                    evidence = body.Evidence.Value.GetRawText(),
                    source = string.IsNullOrWhiteSpace(body.Source) ? "symbol_lab" : body.Source,
                },
                cancellationToken: ct));

            return Results.Created($"/api/verdicts/{id}", new { id });
        });

        // GET /api/verdicts?symbol=&horizon=&from= — list, newest first.
        group.MapGet("/", async (
            string? symbol, string? horizon, DateTime? from, int? limit,
            NpgsqlDataSource db, CancellationToken ct) =>
        {
            await using var conn = await db.OpenConnectionAsync(ct);
            var rows = await conn.QueryAsync(new CommandDefinition(@"
                SELECT id, user_id, ts, symbol, spot, horizon, verdict,
                       spec::text AS spec, why, counter_case,
                       evidence::text AS evidence, source
                FROM verdicts
                WHERE (@symbol IS NULL OR symbol = @symbol)
                  AND (@horizon IS NULL OR horizon = @horizon)
                  -- explicit cast: a NULL @from with no other typed use in this
                  -- branch leaves Postgres unable to infer its type (42P08
                  -- indeterminate_datatype) when the value is actually null.
                  AND (@from::timestamptz IS NULL OR ts >= @from::timestamptz)
                ORDER BY ts DESC
                LIMIT @limit;",
                new
                {
                    symbol = string.IsNullOrWhiteSpace(symbol) ? null : symbol.Trim().ToUpperInvariant(),
                    horizon = string.IsNullOrWhiteSpace(horizon) ? null : horizon,
                    from,
                    limit = Math.Clamp(limit ?? 100, 1, 1000),
                },
                cancellationToken: ct));
            return Results.Ok(new { verdicts = rows });
        });

        group.MapGet("/{id:long}", async (long id, NpgsqlDataSource db, CancellationToken ct) =>
        {
            await using var conn = await db.OpenConnectionAsync(ct);
            var row = await conn.QuerySingleOrDefaultAsync(new CommandDefinition(@"
                SELECT id, user_id, ts, symbol, spot, horizon, verdict,
                       spec::text AS spec, why, counter_case,
                       evidence::text AS evidence, source
                FROM verdicts WHERE id = @id;",
                new { id }, cancellationToken: ct));
            if (row is null)
                return Results.NotFound(new { error = $"no verdict {id}" });
            var outcomes = await conn.QueryAsync(new CommandDefinition(@"
                SELECT horizon_days, scored_at, ref_price, return_pct, outcome
                FROM verdict_outcomes WHERE verdict_id = @id ORDER BY horizon_days NULLS FIRST;",
                new { id }, cancellationToken: ct));
            return Results.Ok(new { verdict = row, outcomes });
        });

        // GET /api/verdicts/scorecard — per-horizon hit-rate/avg-return, only
        // over SCORED outcomes (outcome NOT IN ('PENDING', NULL) — an unscored
        // verdict must never silently count as a loss or get excluded in a way
        // that inflates the rate; it just doesn't contribute yet).
        group.MapGet("/scorecard", async (NpgsqlDataSource db, CancellationToken ct) =>
        {
            await using var conn = await db.OpenConnectionAsync(ct);
            var rows = await conn.QueryAsync(new CommandDefinition(@"
                SELECT
                    v.horizon,
                    count(*) FILTER (WHERE o.outcome IS NOT NULL AND o.outcome <> 'PENDING') AS scored_count,
                    count(*) FILTER (WHERE o.outcome IN ('WIN', 'ASSIGNED')) AS win_count,
                    count(*) FILTER (WHERE o.outcome IN ('LOSS')) AS loss_count,
                    avg(o.return_pct) FILTER (WHERE o.outcome IS NOT NULL AND o.outcome <> 'PENDING') AS avg_return_pct
                FROM verdicts v
                LEFT JOIN verdict_outcomes o ON o.verdict_id = v.id
                GROUP BY v.horizon
                ORDER BY v.horizon;",
                cancellationToken: ct));
            return Results.Ok(new { scorecard = rows });
        });

        // POST /api/verdicts/score-pending — spec §5.3, the nightly scoring job.
        // Idempotent (safe to call more than once a day, or by hand): every
        // query it runs is itself the "is this due AND not already scored"
        // filter, so a re-run just finds nothing new to do. Called on a
        // schedule by .github/workflows/verdicts-score-nightly.yml.
        group.MapPost("/score-pending", async (
            NpgsqlDataSource db, IMarketDataRegistry providers,
            ILogger<Program> log, CancellationToken ct) =>
        {
            await using var conn = await db.OpenConnectionAsync(ct);
            var priceCache = new Dictionary<string, decimal?>();

            async Task<decimal?> LatestPriceAsync(string symbol)
            {
                if (priceCache.TryGetValue(symbol, out var cached)) return cached;
                decimal? px = null;
                try
                {
                    var provider = providers.Resolve(null);
                    var series = await provider.GetCandlesAsync(
                        symbol, "1d", DateTime.UtcNow.AddDays(-10), DateTime.UtcNow, ct);
                    if (series.Candles.Count > 0) px = series.Candles[^1].AdjOrClose;
                }
                catch (Exception ex)
                {
                    log.LogWarning(ex, "score-pending: price fetch failed for {Symbol}", symbol);
                }
                priceCache[symbol] = px;
                return px;
            }

            // ---- swing/invest: directional calls, scored 20/60 calendar days
            // after the verdict. NOT_YET/WAIT are explicitly non-committal — no
            // false positives: scoring them would fabricate a call that was
            // never actually made. YES = act (win if price rose); NO = avoid
            // (win if price DIDN'T run away without you — correctly avoiding a
            // loser is the point of a NO call, not a coin flip against it).
            var due = (await conn.QueryAsync(new CommandDefinition(@"
                SELECT v.id AS verdict_id, v.symbol, v.spot, v.verdict, hd.horizon_days
                FROM verdicts v
                CROSS JOIN (VALUES (20), (60)) AS hd(horizon_days)
                WHERE v.horizon IN ('swing', 'invest')
                  AND v.verdict IN ('YES', 'NO')
                  AND v.ts <= now() - make_interval(days => hd.horizon_days)
                  AND NOT EXISTS (
                      SELECT 1 FROM verdict_outcomes o
                      WHERE o.verdict_id = v.id AND o.horizon_days = hd.horizon_days
                  );",
                cancellationToken: ct))).ToList();

            var scoredDirectional = 0;
            foreach (var row in due)
            {
                string symbol = row.symbol;
                decimal spot = row.spot;
                string verdict = row.verdict;
                int horizonDays = row.horizon_days;

                var px = await LatestPriceAsync(symbol);
                if (px is null || px <= 0) continue; // stays unscored — retried next run, never guessed

                var returnPct = (px.Value - spot) / spot * 100m;
                var outcome = verdict == "YES"
                    ? (returnPct > 0 ? "WIN" : "LOSS")
                    : (returnPct <= 0 ? "WIN" : "LOSS");

                await conn.ExecuteAsync(new CommandDefinition(@"
                    INSERT INTO verdict_outcomes (verdict_id, horizon_days, scored_at, ref_price, return_pct, outcome)
                    VALUES (@verdict_id, @horizon_days, now(), @ref_price, @return_pct, @outcome);",
                    new
                    {
                        verdict_id = (long)row.verdict_id, horizon_days = horizonDays,
                        ref_price = px.Value, return_pct = returnPct, outcome,
                    },
                    cancellationToken: ct));
                scoredDirectional++;
            }

            // ---- wheel: scored at option EXPIRY, not a fixed day-count. Only
            // handles the structured spec shape {action,strike,expiry,premium}
            // (spec §5.1's own DDL example) — a free-text spec (e.g. imported
            // seed rows) is skipped, not guessed at by parsing prose. SELL_PUT
            // only for now (covered-call/other structures are future work,
            // same honesty principle: score what we can prove, skip the rest).
            var dueWheel = (await conn.QueryAsync(new CommandDefinition(@"
                SELECT v.id AS verdict_id, v.symbol, v.spot, v.spec::text AS spec
                FROM verdicts v
                WHERE v.horizon = 'wheel' AND v.verdict = 'YES'
                  AND NOT EXISTS (SELECT 1 FROM verdict_outcomes o WHERE o.verdict_id = v.id);",
                cancellationToken: ct))).ToList();

            var scoredWheel = 0;
            var wheelSkippedUnstructured = 0;
            foreach (var row in dueWheel)
            {
                string symbol = row.symbol;
                string? specText = row.spec;
                if (string.IsNullOrWhiteSpace(specText)) { wheelSkippedUnstructured++; continue; }

                JsonElement root;
                try { root = JsonDocument.Parse(specText).RootElement; }
                catch (JsonException) { wheelSkippedUnstructured++; continue; }

                if (!root.TryGetProperty("action", out var actionEl)
                    || actionEl.GetString() != "SELL_PUT"
                    || !root.TryGetProperty("strike", out var strikeEl)
                    || !root.TryGetProperty("expiry", out var expiryEl)
                    || !root.TryGetProperty("premium", out var premiumEl)
                    || !DateTime.TryParse(expiryEl.GetString(), out var expiry))
                {
                    wheelSkippedUnstructured++;
                    continue;
                }
                if (expiry > DateTime.UtcNow) continue; // not due yet — not an error, just not expired

                var strike = strikeEl.GetDecimal();
                var premium = premiumEl.GetDecimal();
                var px = await LatestPriceAsync(symbol);
                if (px is null) continue;

                string outcome;
                decimal returnPct;
                if (px.Value < strike)
                {
                    outcome = "ASSIGNED";
                    returnPct = (premium - (strike - px.Value)) / strike * 100m;
                }
                else
                {
                    outcome = "EXPIRED_WORTHLESS";
                    returnPct = premium / strike * 100m;
                }

                await conn.ExecuteAsync(new CommandDefinition(@"
                    INSERT INTO verdict_outcomes (verdict_id, horizon_days, scored_at, ref_price, return_pct, outcome)
                    VALUES (@verdict_id, NULL, now(), @ref_price, @return_pct, @outcome);",
                    new { verdict_id = (long)row.verdict_id, ref_price = px.Value, return_pct = returnPct, outcome },
                    cancellationToken: ct));
                scoredWheel++;
            }

            return Results.Ok(new
            {
                scoredDirectional,
                directionalChecked = due.Count,
                scoredWheel,
                wheelChecked = dueWheel.Count,
                wheelSkippedUnstructuredSpec = wheelSkippedUnstructured,
            });
        });

        return app;
    }

    public sealed record VerdictPostBody(
        int? UserId,
        string Symbol,
        decimal Spot,
        string Horizon,
        string Verdict,
        JsonElement? Spec,
        string Why,
        string CounterCase,
        JsonElement? Evidence,
        string? Source
    );
}

using System.Text.Json;
using Dapper;
using Npgsql;
using TradePro.Api.Data.Stores;

namespace TradePro.Api.Endpoints;

/// <summary>
/// /api/live-portfolio/* — read API for the slow-loop output
/// (today's algo-recommended target portfolio). Backed by the
/// strategy_runs + strategy_decisions tables. Worker pushes via
/// /api/ingest/live-portfolio.
///
/// Today-only by default per the no-clutter principle: `/latest`
/// returns the most-recent run, full stop. History lookup is a
/// separate endpoint (`/runs`) with explicit date filters so
/// historical data only shows up when asked.
/// </summary>
public static class LivePortfolioEndpoints
{
    public static IEndpointRouteBuilder MapLivePortfolioUserEndpoints(this IEndpointRouteBuilder app)
    {
        var group = app.MapGroup("/live-portfolio").WithTags("LivePortfolio");

        // GET /api/live-portfolio/{strategy}/latest
        // Returns the most-recent algo run + its decisions. 404 with
        // a CLI hint when no row exists yet.
        group.MapGet("/{strategy}/latest", async (
            string strategy, NpgsqlDataSource db) =>
        {
            await using var conn = await db.OpenConnectionAsync();
            var head = await conn.QueryFirstOrDefaultAsync<RunRow>(@"
                SELECT run_id, strategy, mode, as_of_utc,
                       n_decisions, n_long, regime_state,
                       summary::text AS summary_text,
                       uploaded_at_utc, uploaded_by
                FROM strategy_runs
                WHERE strategy = @strategy
                ORDER BY as_of_utc DESC
                LIMIT 1;",
                new { strategy });
            if (head is null)
            {
                return Results.NotFound(new
                {
                    error = $"no live-portfolio run for {strategy}",
                    hint = "run `tradepro-live-portfolio --push` on the worker host",
                });
            }
            var rows = await conn.QueryAsync<DecisionRow>(@"
                SELECT sleeve, symbol, target_weight, signal,
                       regime_pass, vol, risk_class,
                       detail::text AS detail_text
                FROM strategy_decisions
                WHERE run_id = @runId
                ORDER BY target_weight DESC, sleeve, symbol;",
                new { runId = head.run_id });

            return Results.Ok(new
            {
                runId = head.run_id,
                strategy = head.strategy,
                mode = head.mode,
                asOfUtc = head.as_of_utc,
                uploadedAtUtc = head.uploaded_at_utc,
                uploadedBy = head.uploaded_by,
                regimeState = head.regime_state,
                nDecisions = head.n_decisions,
                nLong = head.n_long,
                summary = JsonbHelpers.FromJsonb(head.summary_text ?? "{}"),
                decisions = rows.Select(r => new
                {
                    sleeve = r.sleeve,
                    symbol = r.symbol,
                    targetWeight = r.target_weight,
                    signal = r.signal,
                    regimePass = r.regime_pass,
                    vol = r.vol,
                    riskClass = r.risk_class,
                    detail = JsonbHelpers.FromJsonb(r.detail_text ?? "{}"),
                }),
            });
        });

        // GET /api/live-portfolio/{strategy}/runs?since=...&until=...
        // Historical lookup — list of past runs (header only, no
        // decisions). Explicit date filters required so this endpoint
        // can't accidentally become the default screen-cluttering view.
        // Defaults to last 30 days when no params given.
        group.MapGet("/{strategy}/runs", async (
            string strategy, DateTime? since, DateTime? until,
            NpgsqlDataSource db) =>
        {
            var fromTs = since ?? DateTime.UtcNow.AddDays(-30);
            var toTs = until ?? DateTime.UtcNow;
            await using var conn = await db.OpenConnectionAsync();
            var rows = await conn.QueryAsync<RunRow>(@"
                SELECT run_id, strategy, mode, as_of_utc,
                       n_decisions, n_long, regime_state,
                       summary::text AS summary_text,
                       uploaded_at_utc, uploaded_by
                FROM strategy_runs
                WHERE strategy = @strategy
                  AND as_of_utc BETWEEN @fromTs AND @toTs
                ORDER BY as_of_utc DESC
                LIMIT 200;",
                new { strategy, fromTs, toTs });
            return Results.Ok(new
            {
                strategy,
                since = fromTs,
                until = toTs,
                runs = rows.Select(r => new
                {
                    runId = r.run_id,
                    mode = r.mode,
                    asOfUtc = r.as_of_utc,
                    nDecisions = r.n_decisions,
                    nLong = r.n_long,
                    regimeState = r.regime_state,
                    uploadedBy = r.uploaded_by,
                }),
            });
        });

        // GET /api/live-portfolio/by-symbol/{symbol}?strategy=
        // Returns the algo's latest decision for one symbol — used by
        // the per-symbol AlgoVerdictPill on /compare so the existing
        // Decide cards can show the trader-algo's verdict alongside
        // the multi-indicator consensus. 404 if the symbol isn't in
        // the algo's universe (large_50 + high_beta + gold today).
        group.MapGet("/by-symbol/{symbol}", async (
            string symbol, string? strategy, NpgsqlDataSource db) =>
        {
            var strat = string.IsNullOrWhiteSpace(strategy) ? "ichimoku_equity" : strategy;
            // Match both bare AAPL and broker-form AAPL_US_EQ.
            var bare = symbol.Trim().ToUpperInvariant();
            var underscore = bare.IndexOf('_');
            if (underscore > 0) bare = bare[..underscore];
            await using var conn = await db.OpenConnectionAsync();
            var row = await conn.QueryFirstOrDefaultAsync<BySymbolRow>(@"
                SELECT d.sleeve, d.symbol,
                       d.target_weight AS TargetWeight,
                       d.signal,
                       d.regime_pass AS RegimePass,
                       d.vol,
                       d.risk_class AS RiskClass,
                       d.detail::text AS DetailText,
                       d.as_of_utc AS AsOfUtc,
                       r.regime_state AS RegimeState
                FROM strategy_decisions d
                JOIN strategy_runs r ON r.run_id = d.run_id
                WHERE d.strategy = @strat
                  AND (UPPER(d.symbol) = @sym OR UPPER(d.symbol) LIKE @symLike)
                  AND r.run_id = (
                      SELECT run_id FROM strategy_runs
                      WHERE strategy = @strat
                      ORDER BY as_of_utc DESC LIMIT 1
                  )
                LIMIT 1;",
                new { strat, sym = bare, symLike = bare + "_%" });
            if (row is null)
            {
                return Results.Ok(new
                {
                    symbol = bare, inAlgoUniverse = false,
                    verdict = "OUT_OF_UNIVERSE",
                });
            }
            // Verdict mapping:
            //   target_weight > 0     → BUY (with weight as conviction)
            //   signal == 1 && weight 0 → HOLD (regime-gated)
            //   signal == 0           → FLAT (signal said no)
            var verdict = (row.TargetWeight, row.Signal, row.RegimePass) switch
            {
                ( > 0.0, _, _) => "BUY",
                (_, > 0.0, false) => "HOLD_REGIME_BLOCKED",
                (_, > 0.0, true) => "HOLD",
                _ => "FLAT",
            };
            return Results.Ok(new
            {
                symbol = bare,
                inAlgoUniverse = true,
                verdict,
                sleeve = row.Sleeve,
                targetWeight = row.TargetWeight,
                signal = row.Signal,
                regimePass = row.RegimePass,
                regimeState = row.RegimeState,
                vol = row.Vol,
                riskClass = row.RiskClass,
                asOfUtc = row.AsOfUtc,
                detail = string.IsNullOrEmpty(row.DetailText)
                    ? null : (object)JsonbHelpers.FromJsonb(row.DetailText),
            });
        });

        // GET /api/live-portfolio/{strategy}/runs/{runId}
        // Specific historical run — header + decisions. Used when the
        // operator clicks a row in the history list to inspect.
        group.MapGet("/{strategy}/runs/{runId:guid}", async (
            string strategy, Guid runId, NpgsqlDataSource db) =>
        {
            await using var conn = await db.OpenConnectionAsync();
            var head = await conn.QueryFirstOrDefaultAsync<RunRow>(@"
                SELECT run_id, strategy, mode, as_of_utc,
                       n_decisions, n_long, regime_state,
                       summary::text AS summary_text,
                       uploaded_at_utc, uploaded_by
                FROM strategy_runs
                WHERE strategy = @strategy AND run_id = @runId;",
                new { strategy, runId });
            if (head is null)
                return Results.NotFound(new { error = $"no run {runId} for {strategy}" });
            var rows = await conn.QueryAsync<DecisionRow>(@"
                SELECT sleeve, symbol, target_weight, signal,
                       regime_pass, vol, risk_class,
                       detail::text AS detail_text
                FROM strategy_decisions
                WHERE run_id = @runId
                ORDER BY target_weight DESC;",
                new { runId });
            return Results.Ok(new
            {
                runId = head.run_id,
                strategy = head.strategy,
                mode = head.mode,
                asOfUtc = head.as_of_utc,
                uploadedAtUtc = head.uploaded_at_utc,
                uploadedBy = head.uploaded_by,
                regimeState = head.regime_state,
                summary = JsonbHelpers.FromJsonb(head.summary_text ?? "{}"),
                decisions = rows.Select(r => new
                {
                    sleeve = r.sleeve,
                    symbol = r.symbol,
                    targetWeight = r.target_weight,
                    signal = r.signal,
                    regimePass = r.regime_pass,
                    vol = r.vol,
                    riskClass = r.risk_class,
                    detail = JsonbHelpers.FromJsonb(r.detail_text ?? "{}"),
                }),
            });
        });

        return app;
    }

    public static IEndpointRouteBuilder MapLivePortfolioIngestEndpoints(this IEndpointRouteBuilder app)
    {
        var group = app.MapGroup("/ingest")
            .WithTags("LivePortfolio/Ingest")
            .RequireAuthorization(Auth.IngestTokenAuth.Policy);

        // POST /api/ingest/live-portfolio
        // Body shape (mirrors strategies/cli/live_portfolio.py build_payload):
        //   { strategy, run_id, mode, as_of_utc, uploaded_by,
        //     summary, regime_state, decisions: [{sleeve, symbol,
        //     target_weight, signal, regime_pass, vol, risk_class,
        //     detail}] }
        // Inserts one strategy_runs row + N strategy_decisions rows
        // atomically.
        group.MapPost("/live-portfolio", async (
            JsonElement payload, NpgsqlDataSource db) =>
        {
            if (payload.ValueKind != JsonValueKind.Object)
                return Results.BadRequest(new { error = "payload must be a JSON object" });

            var strategy = JsonbHelpers.ReadString(payload, "strategy");
            if (string.IsNullOrWhiteSpace(strategy))
                return Results.BadRequest(new { error = "strategy is required" });

            var runIdStr = JsonbHelpers.ReadString(payload, "run_id");
            if (string.IsNullOrWhiteSpace(runIdStr) || !Guid.TryParse(runIdStr, out var runId))
                return Results.BadRequest(new { error = "run_id must be a UUID" });

            var mode = JsonbHelpers.ReadString(payload, "mode") ?? "live";
            var uploadedBy = JsonbHelpers.ReadString(payload, "uploaded_by");
            var regimeState = JsonbHelpers.ReadString(payload, "regime_state");

            DateTime asOf = DateTime.UtcNow;
            if (payload.TryGetProperty("as_of_utc", out var asOfEl)
                && asOfEl.ValueKind == JsonValueKind.String
                && DateTime.TryParse(asOfEl.GetString(), out var parsed))
            {
                asOf = parsed.ToUniversalTime();
            }

            if (!payload.TryGetProperty("decisions", out var decisionsEl)
                || decisionsEl.ValueKind != JsonValueKind.Array)
            {
                return Results.BadRequest(new { error = "decisions must be a JSON array" });
            }

            // Summary blob → JSONB. Counting long + total decisions
            // server-side so the header is always consistent with the
            // detail rows (caller can lie; we can't).
            JsonElement? summary = null;
            if (payload.TryGetProperty("summary", out var s) && s.ValueKind == JsonValueKind.Object)
                summary = s;
            var summaryJson = summary.HasValue
                ? JsonbHelpers.ToJsonb(summary.Value)
                : "{}";

            int nDecisions = 0;
            int nLong = 0;
            var rows = new List<DecisionInsert>();
            foreach (var d in decisionsEl.EnumerateArray())
            {
                if (d.ValueKind != JsonValueKind.Object) continue;
                var sleeve = JsonbHelpers.ReadString(d, "sleeve") ?? "";
                var symbol = JsonbHelpers.ReadString(d, "symbol") ?? "";
                if (string.IsNullOrWhiteSpace(symbol) || string.IsNullOrWhiteSpace(sleeve)) continue;
                var targetWeight = _readDouble(d, "target_weight");
                var signal = _readDouble(d, "signal");
                var regimePass = !d.TryGetProperty("regime_pass", out var rp)
                    || rp.ValueKind != JsonValueKind.False;
                var vol = _readNullableDouble(d, "vol");
                var riskClass = JsonbHelpers.ReadString(d, "risk_class");
                var detailJson = "{}";
                if (d.TryGetProperty("detail", out var det) && det.ValueKind == JsonValueKind.Object)
                {
                    detailJson = JsonbHelpers.ToJsonb(det);
                }

                nDecisions++;
                if (targetWeight > 0) nLong++;

                rows.Add(new DecisionInsert(
                    RunId: runId, Sleeve: sleeve, Symbol: symbol,
                    TargetWeight: targetWeight, Signal: signal,
                    RegimePass: regimePass, Vol: vol,
                    RiskClass: riskClass, DetailJson: detailJson,
                    AsOfUtc: asOf, UploadedBy: uploadedBy));
            }

            await using var conn = await db.OpenConnectionAsync();
            await using var tx = await conn.BeginTransactionAsync();
            try
            {
                // Header — upsert on run_id so re-pushes of the same
                // run_id (e.g. retry after network blip) don't fail.
                await conn.ExecuteAsync(@"
                    INSERT INTO strategy_runs
                      (run_id, strategy, mode, as_of_utc,
                       n_decisions, n_long, regime_state, summary,
                       uploaded_at_utc, uploaded_by)
                    VALUES (@runId, @strategy, @mode, @asOf,
                            @nDecisions, @nLong, @regimeState, @summaryJson::jsonb,
                            NOW(), @uploadedBy)
                    ON CONFLICT (run_id) DO UPDATE
                    SET strategy = EXCLUDED.strategy,
                        mode = EXCLUDED.mode,
                        as_of_utc = EXCLUDED.as_of_utc,
                        n_decisions = EXCLUDED.n_decisions,
                        n_long = EXCLUDED.n_long,
                        regime_state = EXCLUDED.regime_state,
                        summary = EXCLUDED.summary,
                        uploaded_at_utc = NOW(),
                        uploaded_by = EXCLUDED.uploaded_by;",
                    new
                    {
                        runId, strategy, mode, asOf,
                        nDecisions, nLong, regimeState,
                        summaryJson, uploadedBy,
                    },
                    transaction: tx);

                // Decisions — same upsert pattern in case the same
                // (run_id, sleeve, symbol) gets re-pushed.
                foreach (var r in rows)
                {
                    await conn.ExecuteAsync(@"
                        INSERT INTO strategy_decisions
                          (run_id, strategy, sleeve, symbol,
                           target_weight, signal, regime_pass, vol,
                           risk_class, detail, as_of_utc,
                           uploaded_at_utc, uploaded_by)
                        VALUES (@RunId, @Strategy, @Sleeve, @Symbol,
                                @TargetWeight, @Signal, @RegimePass, @Vol,
                                @RiskClass, @DetailJson::jsonb, @AsOfUtc,
                                NOW(), @UploadedBy)
                        ON CONFLICT (run_id, sleeve, symbol) DO UPDATE
                        SET target_weight = EXCLUDED.target_weight,
                            signal = EXCLUDED.signal,
                            regime_pass = EXCLUDED.regime_pass,
                            vol = EXCLUDED.vol,
                            risk_class = EXCLUDED.risk_class,
                            detail = EXCLUDED.detail,
                            as_of_utc = EXCLUDED.as_of_utc,
                            uploaded_at_utc = NOW(),
                            uploaded_by = EXCLUDED.uploaded_by;",
                        new
                        {
                            r.RunId, Strategy = strategy, r.Sleeve, r.Symbol,
                            r.TargetWeight, r.Signal, r.RegimePass, r.Vol,
                            r.RiskClass, r.DetailJson, r.AsOfUtc, r.UploadedBy,
                        },
                        transaction: tx);
                }

                await tx.CommitAsync();
            }
            catch (Exception ex)
            {
                await tx.RollbackAsync();
                throw new InvalidOperationException(
                    $"live-portfolio ingest failed: {ex.Message}", ex);
            }

            return Results.Ok(new
            {
                accepted = true,
                strategy, runId,
                nDecisions, nLong,
                asOfUtc = asOf,
            });
        });

        return app;
    }

    private static double _readDouble(JsonElement el, string key)
    {
        if (!el.TryGetProperty(key, out var v)) return 0.0;
        return v.ValueKind switch
        {
            JsonValueKind.Number => v.GetDouble(),
            JsonValueKind.True => 1.0,
            JsonValueKind.False => 0.0,
            _ => 0.0,
        };
    }

    private static double? _readNullableDouble(JsonElement el, string key)
    {
        if (!el.TryGetProperty(key, out var v)) return null;
        if (v.ValueKind == JsonValueKind.Null) return null;
        if (v.ValueKind != JsonValueKind.Number) return null;
        return v.GetDouble();
    }

    private sealed record RunRow(
        Guid run_id, string strategy, string mode, DateTime as_of_utc,
        int n_decisions, int n_long, string? regime_state,
        string? summary_text, DateTime uploaded_at_utc, string? uploaded_by);

    private sealed record DecisionRow(
        string sleeve, string symbol, double target_weight, double signal,
        bool regime_pass, double? vol, string? risk_class, string? detail_text);

    private sealed record DecisionInsert(
        Guid RunId, string Sleeve, string Symbol,
        double TargetWeight, double Signal, bool RegimePass, double? Vol,
        string? RiskClass, string DetailJson,
        DateTime AsOfUtc, string? UploadedBy);

    private sealed record BySymbolRow(
        string Sleeve, string Symbol, double TargetWeight, double Signal,
        bool RegimePass, double? Vol, string? RiskClass, string? DetailText,
        DateTime AsOfUtc, string? RegimeState);
}

using System.Text.Json;
using Dapper;
using Npgsql;
using TradePro.Api.Data.Stores;

namespace TradePro.Api.Endpoints;

/// <summary>
/// /api/sentiment/* — per-symbol sentiment surface for the LLM gate.
///
/// Source-of-truth scoring runs on the Mac (user runs a finance-tuned
/// LLM locally — e.g. Ollama+FinBERT-style). Mac service POSTs into
/// /api/ingest/sentiment; the LLM gate inside RiskGate reads
/// /api/sentiment/symbol/{X}/latest.
///
/// Today-only by default in /symbol/{X}: returns the LATEST score
/// only — older scores stay in the table for the audit/history
/// surface, but the gate sees just the current view.
/// </summary>
public static class SentimentEndpoints
{
    public static IEndpointRouteBuilder MapSentimentUserEndpoints(this IEndpointRouteBuilder app)
    {
        var group = app.MapGroup("/sentiment").WithTags("Sentiment");

        // GET /api/sentiment/symbol/{symbol}/latest
        // The RiskGate reads this. Returns the most-recent score
        // regardless of how stale; caller applies their own age
        // tolerance via the risk_sentiment_max_age_minutes setting.
        group.MapGet("/symbol/{symbol}/latest", async (
            string symbol, NpgsqlDataSource db) =>
        {
            var bare = NormaliseTicker(symbol);
            await using var conn = await db.OpenConnectionAsync();
            var row = await conn.QueryFirstOrDefaultAsync<SentimentRow>(@"
                SELECT symbol, source, score, classification,
                       n_articles AS NArticles,
                       rationale,
                       detail::text AS DetailText,
                       scored_at_utc AS ScoredAtUtc,
                       uploaded_at_utc AS UploadedAtUtc
                FROM sentiment_scores
                WHERE symbol = @bare OR symbol = @symbol
                ORDER BY scored_at_utc DESC
                LIMIT 1;",
                new { bare, symbol });
            if (row is null)
            {
                return Results.Ok(new
                {
                    symbol = bare, hasScore = false,
                });
            }
            return Results.Ok(new
            {
                symbol = bare, hasScore = true,
                source = row.Source,
                score = row.Score,
                classification = row.Classification,
                nArticles = row.NArticles,
                rationale = row.Rationale,
                scoredAtUtc = row.ScoredAtUtc,
                ageMinutes = (DateTime.UtcNow - row.ScoredAtUtc).TotalMinutes,
                detail = string.IsNullOrEmpty(row.DetailText)
                    ? null : (object)JsonbHelpers.FromJsonb(row.DetailText),
            });
        });

        // GET /api/sentiment/recent?limit=&since=
        // Operator surface (Risk page) — recent scores across all
        // symbols. Today-only by default; ?since= for history.
        group.MapGet("/recent", async (
            int? limit, DateTime? since, NpgsqlDataSource db) =>
        {
            var sinceTs = since ?? DateTime.UtcNow.Date;
            var lim = Math.Clamp(limit ?? 50, 1, 500);
            await using var conn = await db.OpenConnectionAsync();
            var rows = await conn.QueryAsync<SentimentRow>(@"
                SELECT symbol, source, score, classification,
                       n_articles AS NArticles,
                       rationale,
                       detail::text AS DetailText,
                       scored_at_utc AS ScoredAtUtc,
                       uploaded_at_utc AS UploadedAtUtc
                FROM sentiment_scores
                WHERE scored_at_utc >= @sinceTs
                ORDER BY scored_at_utc DESC
                LIMIT @lim;",
                new { sinceTs, lim });
            return Results.Ok(new
            {
                since = sinceTs,
                scores = rows.Select(r => new
                {
                    symbol = r.Symbol, source = r.Source,
                    score = r.Score, classification = r.Classification,
                    nArticles = r.NArticles, rationale = r.Rationale,
                    scoredAtUtc = r.ScoredAtUtc,
                }),
            });
        });

        return app;
    }

    public static IEndpointRouteBuilder MapSentimentIngestEndpoints(this IEndpointRouteBuilder app)
    {
        var group = app.MapGroup("/ingest")
            .WithTags("Sentiment/Ingest")
            .RequireAuthorization(Auth.IngestTokenAuth.Policy);

        // POST /api/ingest/sentiment
        // Body shape (single score):
        //   { symbol, source, score, classification, n_articles?,
        //     rationale?, detail?, scored_at_utc? }
        // OR batch:
        //   { scores: [ {...}, {...} ] }
        group.MapPost("/sentiment", async (
            JsonElement payload, NpgsqlDataSource db) =>
        {
            if (payload.ValueKind != JsonValueKind.Object)
                return Results.BadRequest(new { error = "payload must be a JSON object" });

            // Accept either single or batch.
            JsonElement[] items;
            if (payload.TryGetProperty("scores", out var batch)
                && batch.ValueKind == JsonValueKind.Array)
            {
                items = batch.EnumerateArray().ToArray();
            }
            else
            {
                items = new[] { payload };
            }

            int inserted = 0, skipped = 0;
            await using var conn = await db.OpenConnectionAsync();
            await using var tx = await conn.BeginTransactionAsync();
            try
            {
                foreach (var it in items)
                {
                    if (it.ValueKind != JsonValueKind.Object) { skipped++; continue; }
                    var symbol = JsonbHelpers.ReadString(it, "symbol");
                    var source = JsonbHelpers.ReadString(it, "source") ?? "unknown-llm";
                    var classification = JsonbHelpers.ReadString(it, "classification");
                    if (string.IsNullOrWhiteSpace(symbol)
                        || string.IsNullOrWhiteSpace(classification))
                    {
                        skipped++; continue;
                    }
                    double score = 0;
                    if (it.TryGetProperty("score", out var sEl)
                        && sEl.ValueKind == JsonValueKind.Number)
                    {
                        score = sEl.GetDouble();
                    }
                    int nArticles = 0;
                    if (it.TryGetProperty("n_articles", out var nEl)
                        && nEl.ValueKind == JsonValueKind.Number)
                    {
                        nArticles = nEl.GetInt32();
                    }
                    var rationale = JsonbHelpers.ReadString(it, "rationale");
                    string? detailJson = null;
                    if (it.TryGetProperty("detail", out var dEl)
                        && (dEl.ValueKind == JsonValueKind.Object
                            || dEl.ValueKind == JsonValueKind.Array))
                    {
                        detailJson = JsonbHelpers.ToJsonb(dEl);
                    }
                    DateTime scoredAt = DateTime.UtcNow;
                    if (it.TryGetProperty("scored_at_utc", out var aEl)
                        && aEl.ValueKind == JsonValueKind.String
                        && DateTime.TryParse(aEl.GetString(), out var parsed))
                    {
                        scoredAt = parsed.ToUniversalTime();
                    }

                    await conn.ExecuteAsync(@"
                        INSERT INTO sentiment_scores
                          (symbol, source, score, classification,
                           n_articles, rationale, detail, scored_at_utc)
                        VALUES (@sym, @source, @score, @cls,
                                @nArticles, @rationale,
                                CASE WHEN @detailJson IS NULL THEN NULL
                                     ELSE @detailJson::jsonb END,
                                @scoredAt)
                        ON CONFLICT (symbol, source, scored_at_utc) DO UPDATE
                        SET score = EXCLUDED.score,
                            classification = EXCLUDED.classification,
                            n_articles = EXCLUDED.n_articles,
                            rationale = EXCLUDED.rationale,
                            detail = EXCLUDED.detail,
                            uploaded_at_utc = NOW();",
                        new
                        {
                            sym = NormaliseTicker(symbol),
                            source, score, cls = classification,
                            nArticles, rationale, detailJson, scoredAt,
                        },
                        transaction: tx);
                    inserted++;
                }
                await tx.CommitAsync();
            }
            catch
            {
                await tx.RollbackAsync();
                throw;
            }
            return Results.Ok(new { inserted, skipped });
        });

        return app;
    }

    private static string NormaliseTicker(string ticker)
    {
        var t = ticker.Trim().ToUpperInvariant();
        var underscore = t.IndexOf('_');
        return underscore > 0 ? t[..underscore] : t;
    }

    private sealed record SentimentRow(
        string Symbol, string Source, double Score, string Classification,
        int NArticles, string? Rationale, string? DetailText,
        DateTime ScoredAtUtc, DateTime UploadedAtUtc);
}

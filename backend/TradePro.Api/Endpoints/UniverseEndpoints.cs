using System.Text.Json;
using Dapper;
using Npgsql;
using TradePro.Api.Auth;

namespace TradePro.Api.Endpoints;

/// <summary>
/// Symbol universes (S&amp;P 500, FTSE 100, …) ingested by the Mac
/// worker's tradepro-refresh-universes CLI and served to the frontend
/// universe picker on the Trigger forms.
///
/// Two route groups:
///   * <c>/api/universes</c> (frontend, Firebase-authed) — list + drill.
///   * <c>/api/ingest/universes</c> (worker, static Bearer token) — atomic
///     wipe + re-insert of one or more universes.
///
/// Schema: see <c>db/migrations/010_universes.sql</c>. The (universes,
/// universe_symbols, universe_overrides, broker_ticker_map) tables let
/// us cleanly layer Wikipedia source → trader curation → broker
/// translation without re-fetching anything on the request path.
/// </summary>
public static class UniverseEndpoints
{
    public static IEndpointRouteBuilder MapUniverseUserEndpoints(this IEndpointRouteBuilder app)
    {
        var group = app.MapGroup("/universes").WithTags("Universes");

        // List every ingested universe + the per-universe overrides count
        // so the picker can flag "S&P 500 — 503 symbols (12 excluded by you)".
        group.MapGet("/", async (NpgsqlDataSource db) =>
        {
            await using var conn = await db.OpenConnectionAsync();
            var rows = await conn.QueryAsync<UniverseSummary>(@"
                SELECT u.name             AS Name,
                       u.source_url       AS SourceUrl,
                       u.symbol_count     AS SymbolCount,
                       u.fetched_at_utc   AS FetchedAtUtc,
                       u.source           AS Source,
                       COALESCE(o.included_count, 0)::int AS IncludedOverrides,
                       COALESCE(o.excluded_count, 0)::int AS ExcludedOverrides
                FROM universes u
                LEFT JOIN (
                    SELECT universe_name,
                           SUM(CASE WHEN action = 'INCLUDE' THEN 1 ELSE 0 END)::int AS included_count,
                           SUM(CASE WHEN action = 'EXCLUDE' THEN 1 ELSE 0 END)::int AS excluded_count
                    FROM universe_overrides
                    GROUP BY universe_name
                ) o ON o.universe_name = u.name
                ORDER BY u.name ASC;");
            return Results.Ok(new { universes = rows });
        });

        // Single universe — symbols + per-symbol overrides flag. The
        // ``effective`` field is what the picker should use: TRUE means
        // the symbol participates in a scan (Wikipedia AND not EXCLUDED,
        // OR force-INCLUDED).
        group.MapGet("/{name}", async (string name, NpgsqlDataSource db) =>
        {
            await using var conn = await db.OpenConnectionAsync();
            // Select ALL columns the UniverseSummary ctor takes, even
            // the two with defaults — Dapper matches the positional
            // record ctor against the column set, so a 5-column SELECT
            // against a 7-param ctor blows up at materialisation with
            // "no parameterless / matching ctor". Constants for the
            // override counts (0 / 0) because the detail page doesn't
            // need them here — the symbols list below carries the
            // per-ticker override action separately.
            var header = await conn.QueryFirstOrDefaultAsync<UniverseSummary>(@"
                SELECT name AS Name, source_url AS SourceUrl,
                       symbol_count AS SymbolCount,
                       fetched_at_utc AS FetchedAtUtc, source AS Source,
                       0::int AS IncludedOverrides,
                       0::int AS ExcludedOverrides
                FROM universes WHERE name = @name;",
                new { name });
            if (header is null)
                return Results.NotFound(new { error = $"universe '{name}' not ingested yet" });

            // LEFT JOIN universe_overrides so an EXCLUDE/INCLUDE flag
            // travels in the same row. Trader's INCLUDE override forces
            // a symbol to appear even if it's not in the base universe;
            // that's modelled by selecting the union (UNION ALL +
            // de-duplicated).
            var rows = await conn.QueryAsync<UniverseSymbolRow>(@"
                WITH base AS (
                    SELECT s.ticker, s.name, s.sector, s.industry
                    FROM universe_symbols s
                    WHERE s.universe_name = @name
                ),
                forced AS (
                    SELECT o.ticker, NULL::text AS name, NULL::text AS sector, NULL::text AS industry
                    FROM universe_overrides o
                    WHERE o.universe_name = @name AND o.action = 'INCLUDE'
                      AND NOT EXISTS (SELECT 1 FROM base b WHERE b.ticker = o.ticker)
                ),
                all_rows AS (
                    SELECT * FROM base
                    UNION ALL
                    SELECT * FROM forced
                )
                SELECT a.ticker      AS Ticker,
                       a.name        AS Name,
                       a.sector      AS Sector,
                       a.industry    AS Industry,
                       ov.action     AS OverrideAction,
                       (ov.action IS NULL OR ov.action = 'INCLUDE') AS Effective
                FROM all_rows a
                LEFT JOIN universe_overrides ov
                  ON ov.universe_name = @name AND ov.ticker = a.ticker
                ORDER BY a.ticker ASC;",
                new { name });
            return Results.Ok(new
            {
                header,
                symbols = rows,
            });
        });

        // Override controls — trader can include/exclude a symbol from
        // the picker. PUT-style upsert; DELETE clears.
        group.MapPost("/{name}/overrides", async (
            string name, UniverseOverridePayload body, NpgsqlDataSource db, HttpContext ctx) =>
        {
            if (body is null || string.IsNullOrWhiteSpace(body.Ticker))
                return Results.BadRequest(new { error = "ticker required" });
            var action = (body.Action ?? "").ToUpperInvariant();
            if (action != "INCLUDE" && action != "EXCLUDE")
                return Results.BadRequest(new { error = "action must be INCLUDE or EXCLUDE" });
            var actor = ctx.User?.Identity?.Name ?? "ui";

            await using var conn = await db.OpenConnectionAsync();
            await conn.ExecuteAsync(@"
                INSERT INTO universe_overrides (universe_name, ticker, action, note, updated_by)
                VALUES (@universe, @ticker, @action, @note, @actor)
                ON CONFLICT (universe_name, ticker) DO UPDATE
                SET action = EXCLUDED.action,
                    note = EXCLUDED.note,
                    updated_at_utc = NOW(),
                    updated_by = EXCLUDED.updated_by;",
                new {
                    universe = name,
                    ticker = body.Ticker.Trim().ToUpperInvariant(),
                    action,
                    note = body.Note,
                    actor,
                });
            return Results.Ok(new { ok = true });
        });

        group.MapDelete("/{name}/overrides/{ticker}", async (
            string name, string ticker, NpgsqlDataSource db) =>
        {
            await using var conn = await db.OpenConnectionAsync();
            var n = await conn.ExecuteAsync(@"
                DELETE FROM universe_overrides
                WHERE universe_name = @name AND ticker = @ticker;",
                new { name, ticker = ticker.Trim().ToUpperInvariant() });
            return Results.Ok(new { cleared = n });
        });

        return app;
    }

    public static IEndpointRouteBuilder MapUniverseWorkerEndpoints(this IEndpointRouteBuilder app)
    {
        var group = app.MapGroup("/ingest")
            .WithTags("Universes/Ingest")
            .RequireAuthorization(IngestTokenAuth.Policy);

        // Atomic wipe + re-insert across every universe in the payload.
        // The Mac worker pushes the full batch (typically 8 universes
        // × 100-500 symbols each); FK CASCADE on universe_symbols
        // means deleting the universe row drops its symbols too. We
        // do not touch universe_overrides — the trader's INCLUDE /
        // EXCLUDE list survives a refresh.
        group.MapPost("/universes", async (
            UniverseIngestBatch batch, NpgsqlDataSource db, ILoggerFactory lf) =>
        {
            if (batch?.Universes is null || batch.Universes.Count == 0)
                return Results.BadRequest(new { error = "payload must include at least one universe" });
            var log = lf.CreateLogger("UniverseIngest");

            await using var conn = await db.OpenConnectionAsync();
            await using var tx = await conn.BeginTransactionAsync();
            int rowsIn = 0;
            try
            {
                foreach (var u in batch.Universes)
                {
                    if (string.IsNullOrWhiteSpace(u.Name))
                        return Results.BadRequest(new { error = "every universe must have a name" });
                    if (u.Symbols is null || u.Symbols.Count == 0)
                    {
                        // Skip silently — the scraper sets _errors[name]
                        // when a universe drops below the floor, so an
                        // empty payload here means the scraper opted
                        // out of pushing it. Don't blow away the prior
                        // good rows.
                        log.LogWarning(
                            "ingest: universe '{Name}' has 0 symbols — skipping (keeps last good ingest)",
                            u.Name);
                        continue;
                    }

                    await conn.ExecuteAsync(@"
                        DELETE FROM universes WHERE name = @name;",
                        new { name = u.Name }, transaction: tx);
                    await conn.ExecuteAsync(@"
                        INSERT INTO universes (name, source_url, fetched_at_utc, symbol_count, source)
                        VALUES (@name, @url, @fetched, @count, @source);",
                        new {
                            name = u.Name,
                            url = u.SourceUrl ?? "",
                            fetched = u.FetchedAtUtc ?? DateTime.UtcNow,
                            count = u.Symbols.Count,
                            source = string.IsNullOrWhiteSpace(u.Source) ? "wikipedia" : u.Source,
                        },
                        transaction: tx);
                    await conn.ExecuteAsync(@"
                        INSERT INTO universe_symbols (universe_name, ticker, name, sector, industry)
                        VALUES (@universe, @ticker, @name, @sector, @industry)
                        ON CONFLICT (universe_name, ticker) DO NOTHING;",
                        u.Symbols.Select(s => new {
                            universe = u.Name,
                            ticker = s.Ticker,
                            name = s.Name,
                            sector = s.Sector,
                            industry = s.Industry,
                        }),
                        transaction: tx);
                    rowsIn += u.Symbols.Count;
                }
                await tx.CommitAsync();
            }
            catch (Exception ex)
            {
                await tx.RollbackAsync();
                log.LogError(ex, "universe ingest failed");
                return Results.StatusCode(500);
            }
            return Results.Ok(new {
                accepted = true,
                universes = batch.Universes.Count,
                symbols = rowsIn,
            });
        });

        return app;
    }
}

public sealed record UniverseSummary(
    string Name,
    string SourceUrl,
    int SymbolCount,
    DateTime FetchedAtUtc,
    string Source,
    int IncludedOverrides = 0,
    int ExcludedOverrides = 0
);

public sealed record UniverseSymbolRow(
    string Ticker,
    string? Name,
    string? Sector,
    string? Industry,
    string? OverrideAction,
    bool Effective
);

public sealed record UniverseOverridePayload(
    string Ticker,
    string Action,         // "INCLUDE" | "EXCLUDE"
    string? Note = null
);

public sealed record UniverseIngestBatch(List<UniverseIngestEntry> Universes);

public sealed record UniverseIngestEntry(
    string Name,
    string? SourceUrl,
    DateTime? FetchedAtUtc,
    string? Source,
    List<UniverseIngestSymbol> Symbols
);

public sealed record UniverseIngestSymbol(
    string Ticker,
    string? Name = null,
    string? Sector = null,
    string? Industry = null
);

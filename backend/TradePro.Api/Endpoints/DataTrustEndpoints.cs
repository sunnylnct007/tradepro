using Dapper;
using Npgsql;

namespace TradePro.Api.Endpoints;

/// <summary>
/// /api/admin/data-trust/* — operator-facing visibility + editor for
/// the trustworthy-data-layer roadmap (CURRENT_BACKTEST_LIMITATIONS.md
/// + ROADMAP "Trustworthy data layer").
///
/// Three concerns, three sub-routes:
///   * /assumptions       — auditable list of data assumptions
///                           (HONEST / PARTIAL / OPTIMISTIC / FICTIONAL)
///   * /preferences       — operator-editable provider chain per
///                           (asset_class, resolution)
///   * /backfill          — Phase-A placeholder; real implementation
///                           lands in Phase C
///
/// Migration 029 creates data_source_preferences with a CHECK on
/// allowed provider names. Migration 030 creates data_assumptions
/// with seed rows. Both ship before this endpoint goes live.
/// </summary>
public static class DataTrustEndpoints
{
    // Allowed provider list. Must match the CHECK constraint in
    // migration 029. Update both in the same PR when a new provider
    // joins the chain.
    private static readonly string[] _validProviders = new[]
    {
        "yfinance", "ig", "finnhub", "t212",
        "polygon", "databento", "oanda", "binance",
    };

    public static IEndpointRouteBuilder MapDataTrustEndpoints(this IEndpointRouteBuilder app)
    {
        var g = app.MapGroup("/admin/data-trust").WithTags("Admin");

        // ── GET /api/admin/data-trust/assumptions ──────────────────
        // Returns every row in data_assumptions, sorted by severity
        // (CRITICAL → INFORMATIONAL) then id. The UI panel renders
        // these as a colour-coded accordion.
        g.MapGet("/assumptions", async (NpgsqlDataSource db) =>
        {
            await using var conn = await db.OpenConnectionAsync();
            var rows = await conn.QueryAsync(@"
                SELECT id, description, severity, status, affects,
                       consequence, remedy, mitigation,
                       last_reviewed_at_utc, last_reviewed_by
                FROM data_assumptions
                ORDER BY
                    CASE severity
                        WHEN 'CRITICAL' THEN 1
                        WHEN 'HIGH'     THEN 2
                        WHEN 'MEDIUM'   THEN 3
                        WHEN 'LOW'      THEN 4
                        WHEN 'INFORMATIONAL' THEN 5
                        ELSE 6
                    END,
                    id;");
            return Results.Ok(new { assumptions = rows.AsList() });
        });

        // ── GET /api/admin/data-trust/preferences ──────────────────
        // Returns every (asset_class, resolution) → provider_chain
        // row. The UI panel renders an editable grid.
        g.MapGet("/preferences", async (NpgsqlDataSource db) =>
        {
            await using var conn = await db.OpenConnectionAsync();
            var rows = await conn.QueryAsync(@"
                SELECT asset_class, resolution, provider_chain, notes,
                       updated_at_utc, updated_by
                FROM data_source_preferences
                ORDER BY asset_class, resolution;");
            return Results.Ok(new
            {
                validProviders = _validProviders,
                preferences = rows.AsList(),
            });
        });

        // ── PUT /api/admin/data-trust/preferences/{asset_class}/{resolution} ─
        // UPSERT a row. Body { providerChain: string[], notes?: string }.
        // Validates every provider in the chain against _validProviders
        // (friendlier error than the CHECK constraint round-trip).
        g.MapPut("/preferences/{asset_class}/{resolution}", async (
            string asset_class,
            string resolution,
            PreferencesPutBody body,
            HttpContext ctx,
            NpgsqlDataSource db) =>
        {
            if (string.IsNullOrWhiteSpace(asset_class))
                return Results.BadRequest(new { error = "asset_class required in path" });
            if (string.IsNullOrWhiteSpace(resolution))
                return Results.BadRequest(new { error = "resolution required in path" });
            if (body.ProviderChain is null)
                return Results.BadRequest(new { error = "providerChain required in body (may be empty array)" });

            // Unknown providers fail fast with a list of what's
            // allowed — the trader shouldn't have to read the
            // CHECK constraint to know.
            var unknown = body.ProviderChain
                .Where(p => !_validProviders.Contains(p, StringComparer.OrdinalIgnoreCase))
                .ToList();
            if (unknown.Count > 0)
                return Results.BadRequest(new
                {
                    error = "unknown provider(s) in chain",
                    detail = $"unknown={string.Join(",", unknown)} valid={string.Join(",", _validProviders)}",
                });

            var actor = ctx.User?.Identity?.Name ?? "ui";
            var normalised = body.ProviderChain
                .Select(p => p.ToLowerInvariant())
                .ToArray();

            await using var conn = await db.OpenConnectionAsync();
            await conn.ExecuteAsync(@"
                INSERT INTO data_source_preferences
                    (asset_class, resolution, provider_chain, notes,
                     updated_at_utc, updated_by)
                VALUES
                    (@asset_class, @resolution, @provider_chain, @notes,
                     NOW(), @actor)
                ON CONFLICT (asset_class, resolution) DO UPDATE
                SET provider_chain  = EXCLUDED.provider_chain,
                    notes           = EXCLUDED.notes,
                    updated_at_utc  = NOW(),
                    updated_by      = EXCLUDED.updated_by;",
                new
                {
                    asset_class,
                    resolution,
                    provider_chain = normalised,
                    notes = string.IsNullOrWhiteSpace(body.Notes) ? null : body.Notes,
                    actor,
                });

            var row = await conn.QuerySingleAsync(@"
                SELECT asset_class, resolution, provider_chain, notes,
                       updated_at_utc, updated_by
                FROM data_source_preferences
                WHERE asset_class = @asset_class AND resolution = @resolution;",
                new { asset_class, resolution });
            return Results.Ok(new { row });
        });

        // ── DELETE /api/admin/data-trust/preferences/{asset_class}/{resolution} ─
        // Removes the row so the (asset_class, resolution) reverts to
        // the data layer's hardcoded fallback once Phase B lands.
        g.MapDelete("/preferences/{asset_class}/{resolution}", async (
            string asset_class,
            string resolution,
            NpgsqlDataSource db) =>
        {
            await using var conn = await db.OpenConnectionAsync();
            var rows = await conn.ExecuteAsync(@"
                DELETE FROM data_source_preferences
                WHERE asset_class = @asset_class AND resolution = @resolution;",
                new { asset_class, resolution });
            if (rows == 0)
                return Results.NotFound(new
                {
                    error = $"no preference for '{asset_class}/{resolution}' to delete",
                });
            return Results.Ok(new { deleted = $"{asset_class}/{resolution}" });
        });

        // ── POST /api/admin/data-trust/backfill ────────────────────
        // Phase-A placeholder. Returns 501 with a clear roadmap
        // pointer so the UI can render a disabled button + tooltip.
        // Phase C makes this functional (enqueues a backfill job +
        // returns a job id for tracking).
        g.MapPost("/backfill", (BackfillRequestBody body) =>
        {
            return Results.Json(
                new
                {
                    error = "backfill not yet implemented",
                    detail =
                        "Phase A ships the visibility framework. " +
                        "Phase C wires the actual backfill behaviour " +
                        "(CLI: tradepro-backfill-bars; backend job " +
                        "queue + UI status). See ROADMAP " +
                        "'Trustworthy data layer' for the sequence.",
                    requested = body,
                },
                statusCode: 501);
        });

        return app;
    }

    public sealed record PreferencesPutBody(
        string[]? ProviderChain,
        string? Notes
    );

    public sealed record BackfillRequestBody(
        string? AssetClass,
        string? Symbol,
        string? Resolution,
        string? FromDate,
        string? ToDate
    );
}

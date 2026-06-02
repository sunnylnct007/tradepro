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

    // Phase G-1 — map the bar_cache_events.result enum into the coarse
    // cell-status the cockpit colour-codes. Two "complete" variants
    // collapse into "full"; everything broken collapses into "error"
    // unless it's worth flagging separately (rate_limited, no_provider).
    private static string MapResultToStatus(string result) => result switch
    {
        "complete"           => "full",
        "fetched_complete"   => "full",
        "fetched_partial"    => "partial",
        "manifest_violation" => "error",
        "provider_error"     => "error",
        "rate_limited"       => "rate_limited",
        "no_provider"        => "no_provider",
        _                    => "unknown",
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

        // ─── Bar cache telemetry ────────────────────────────────────
        // Phase B-2 closes the visibility loop. The Python BarStore
        // writes a structured event per fetch (cache hit / miss /
        // partial / error). Before this PR those events only landed
        // in a local JSONL file. Now the BackendTelemetrySink POSTs
        // each event to the endpoint below so they land in Postgres
        // and the cockpit's "Bar cache activity" subsection can read
        // them.
        //
        // GET endpoints are read-only operator surfaces. The
        // sink-POST endpoint accepts events from the worker side;
        // CHECK constraint on result column gives a friendly error
        // for an unknown enum value before the DB rejects it.

        // The set of result values that bar_cache_events.result allows
        // (mirrors the CHECK constraint in migration 031). When the
        // result enum is extended (new failure modes), update both
        // here and in the migration.
        var validResults = new[]
        {
            "complete",
            "fetched_complete",
            "fetched_partial",
            "manifest_violation",
            "provider_error",
            "rate_limited",
            "no_provider",
        };

        g.MapPost("/bar-cache/events", async (
            BarCacheEventBody body,
            NpgsqlDataSource db) =>
        {
            if (string.IsNullOrWhiteSpace(body.Canonical))
                return Results.BadRequest(new { error = "canonical required" });
            if (string.IsNullOrWhiteSpace(body.AssetClass))
                return Results.BadRequest(new { error = "asset_class required" });
            if (string.IsNullOrWhiteSpace(body.Resolution))
                return Results.BadRequest(new { error = "resolution required" });
            if (string.IsNullOrWhiteSpace(body.Result))
                return Results.BadRequest(new { error = "result required" });
            if (!validResults.Contains(body.Result, StringComparer.OrdinalIgnoreCase))
                return Results.BadRequest(new
                {
                    error = "invalid result",
                    detail = $"result must be one of: {string.Join(", ", validResults)}",
                });

            // provider_versions arrives as a JSON object; we serialize
            // to a jsonb column. Dapper passes the string through and
            // Postgres casts via ::jsonb.
            var providerVersionsJson =
                body.ProviderVersions is null
                    ? "{}"
                    : System.Text.Json.JsonSerializer.Serialize(body.ProviderVersions);

            await using var conn = await db.OpenConnectionAsync();
            var id = await conn.ExecuteScalarAsync<long>(@"
                INSERT INTO bar_cache_events (
                    canonical, asset_class, resolution,
                    range_start_utc, range_end_utc,
                    result, source_chain, provider_used,
                    provider_versions, rows_expected, rows_returned,
                    gaps_detected_count, schema_version, latency_ms,
                    error_class, error_provider, error_message, retry_strategy
                ) VALUES (
                    @canonical, @asset_class, @resolution,
                    @range_start_utc, @range_end_utc,
                    @result, @source_chain, @provider_used,
                    @provider_versions::jsonb, @rows_expected, @rows_returned,
                    @gaps_detected_count, @schema_version, @latency_ms,
                    @error_class, @error_provider, @error_message, @retry_strategy
                )
                RETURNING id;",
                new
                {
                    canonical = body.Canonical,
                    asset_class = body.AssetClass,
                    resolution = body.Resolution,
                    range_start_utc = body.RangeStartUtc,
                    range_end_utc = body.RangeEndUtc,
                    result = body.Result.ToLowerInvariant(),
                    source_chain = body.SourceChain ?? Array.Empty<string>(),
                    provider_used = body.ProviderUsed,
                    provider_versions = providerVersionsJson,
                    rows_expected = body.RowsExpected,
                    rows_returned = body.RowsReturned,
                    gaps_detected_count = body.GapsDetectedCount ?? 0,
                    schema_version = body.SchemaVersion ?? "",
                    latency_ms = body.LatencyMs ?? 0,
                    error_class = body.ErrorClass,
                    error_provider = body.ErrorProvider,
                    error_message = body.ErrorMessage,
                    retry_strategy = body.RetryStrategy,
                });

            return Results.Ok(new { id, accepted = true });
        });

        g.MapGet("/bar-cache/events", async (
            NpgsqlDataSource db,
            string? canonical,
            string? asset_class,
            string? result,
            int? limit) =>
        {
            await using var conn = await db.OpenConnectionAsync();
            var rows = await conn.QueryAsync(@"
                SELECT id, occurred_at_utc, canonical, asset_class, resolution,
                       range_start_utc, range_end_utc,
                       result, source_chain, provider_used,
                       provider_versions::text AS provider_versions_text,
                       rows_expected, rows_returned, gaps_detected_count,
                       schema_version, latency_ms,
                       error_class, error_provider, error_message, retry_strategy
                FROM bar_cache_events
                WHERE (@canonical   IS NULL OR canonical   = @canonical)
                  AND (@asset_class IS NULL OR asset_class = @asset_class)
                  AND (@result      IS NULL OR result      = @result)
                ORDER BY occurred_at_utc DESC
                LIMIT @limit;",
                new
                {
                    canonical,
                    asset_class,
                    result,
                    limit = Math.Min(limit ?? 200, 1000),
                });
            return Results.Ok(new { events = rows.AsList() });
        });

        // ── IG /prices bridge (Phase B-4) ───────────────────────────
        // Python IGProvider proxies through this endpoint so it reuses
        // the .NET-side IG session + auth (the same session the
        // OMS dispatch uses). Saves duplicating IG REST auth in
        // Python; consistent with how the T212 admin endpoints work.
        //
        // Maps the BarStore's canonical resolutions to IG's strings:
        //   1m  → MINUTE     1d → DAY
        //   5m  → MINUTE_5   1wk → WEEK
        //   15m → MINUTE_15
        //   30m → MINUTE_30
        //   1h  → HOUR
        //
        // ``from`` and ``to`` are ISO 8601 (the Python provider sends
        // them in UTC). Returns the bars + allowance metadata so the
        // Python side can log how close it is to the weekly cap and
        // back off if necessary.
        g.MapGet("/ig/prices", async (
            string epic,
            string resolution,
            string from,
            string to,
            int? max,
            TradePro.Api.Providers.IG.IGClient ig,
            CancellationToken ct) =>
        {
            if (string.IsNullOrWhiteSpace(epic))
                return Results.BadRequest(new { error = "epic required" });
            if (string.IsNullOrWhiteSpace(resolution))
                return Results.BadRequest(new { error = "resolution required (MINUTE / HOUR / DAY / ...)" });
            if (string.IsNullOrWhiteSpace(from))
                return Results.BadRequest(new { error = "from required (ISO 8601)" });
            if (string.IsNullOrWhiteSpace(to))
                return Results.BadRequest(new { error = "to required (ISO 8601)" });
            if (!ig.IsEnabled)
                return Results.BadRequest(new
                {
                    error = "IG disabled",
                    detail = "IG broker not configured; see IGOptions.Mode.",
                });

            var result = await ig.GetPricesAsync(
                epic: epic,
                resolution: resolution,
                from: from,
                to: to,
                max: max ?? 5000,
                ct: ct);

            if (!string.IsNullOrEmpty(result.Error))
            {
                return Results.Json(
                    new
                    {
                        epic, resolution, from, to,
                        error = result.Error,
                        httpStatus = result.HttpStatus,
                        allowanceRemaining = result.AllowanceRemaining,
                    },
                    statusCode: result.HttpStatus == 0 ? 502 : result.HttpStatus);
            }

            // Normalise bar shape for the Python provider. Wire format
            // is JSON-friendly: timestamp as ISO string, prices as
            // floats. Keeps the wrapping layer thin so a future
            // provider swap doesn't break the Python side.
            return Results.Ok(new
            {
                epic, resolution,
                allowanceRemaining = result.AllowanceRemaining,
                allowanceTotal = result.AllowanceTotal,
                count = result.Bars.Count,
                bars = result.Bars.Select(b => new
                {
                    timestamp = b.SnapshotTime,
                    open = (double)b.Open,
                    high = (double)b.High,
                    low = (double)b.Low,
                    close = (double)b.Close,
                    volume = b.Volume,
                }),
            });
        });

        // Phase G-1 — Coverage matrix.
        //
        // Per-(canonical × month) status grid derived from bar_cache_events.
        // For each (canonical, year-month) tuple, we pick the most recent
        // fetch event whose range_start_utc falls inside that month and
        // map its `result` onto a coarse status:
        //
        //   complete | fetched_complete             → "full"
        //   fetched_partial                          → "partial"
        //   manifest_violation | provider_error      → "error"
        //   rate_limited                             → "rate_limited"
        //   no_provider                              → "no_provider"
        //   (no event in this month)                 → "unknown"
        //
        // Frontend renders a grid: rows = (canonical, asset_class),
        // columns = the last N months. Click → drill into a single cell
        // (G-2). Today the matrix is a visibility primitive only — it
        // tells the operator which months to backfill / reload.
        //
        // Cheap to compute: one DISTINCT ON over bar_cache_events filtered
        // by asset_class + resolution + the rolling window. Postgres uses
        // bar_cache_events_canonical_occurred for the inner scan.
        g.MapGet("/bar-cache/coverage-matrix", async (
            NpgsqlDataSource db,
            string? asset_class,
            string? resolution,
            int? months) =>
        {
            // Bound the window: default 12 months, clamp [1, 36] so a
            // misbehaved caller can't sweep five years of telemetry.
            int monthCount = Math.Clamp(months ?? 12, 1, 36);
            var nowUtc = DateTime.UtcNow;
            var windowStart = new DateTime(nowUtc.Year, nowUtc.Month, 1,
                0, 0, 0, DateTimeKind.Utc).AddMonths(-(monthCount - 1));

            // Build the ordered month label list ("YYYY-MM", oldest first).
            var monthLabels = new List<string>(monthCount);
            for (int i = 0; i < monthCount; i++)
            {
                var d = windowStart.AddMonths(i);
                monthLabels.Add($"{d:yyyy-MM}");
            }

            await using var conn = await db.OpenConnectionAsync();
            // DISTINCT ON picks the most recent event per (canonical,
            // asset_class, partition-month). The month bucket comes
            // from date_trunc('month', range_start_utc) so we don't
            // need to materialise the column on the table.
            var rows = (await conn.QueryAsync<(
                string canonical, string asset_class, string month,
                string result, string? provider_used,
                int? rows_returned, int? rows_expected,
                DateTime occurred_at_utc)>(@"
                SELECT DISTINCT ON (canonical, asset_class, month_bucket)
                       canonical, asset_class,
                       to_char(date_trunc('month', range_start_utc), 'YYYY-MM') AS month,
                       result, provider_used,
                       rows_returned, rows_expected,
                       occurred_at_utc
                FROM (
                    SELECT canonical, asset_class, resolution,
                           range_start_utc, result, provider_used,
                           rows_returned, rows_expected, occurred_at_utc,
                           date_trunc('month', range_start_utc) AS month_bucket
                    FROM bar_cache_events
                    WHERE range_start_utc >= @windowStart
                      AND (@asset_class IS NULL OR asset_class = @asset_class)
                      AND (@resolution  IS NULL OR resolution  = @resolution)
                ) AS scoped
                ORDER BY canonical, asset_class, month_bucket,
                         occurred_at_utc DESC;",
                new { windowStart, asset_class, resolution })).ToList();

            // Pivot: group by (canonical, asset_class) → cells keyed by month.
            // The Dictionary preserves only the latest per month (DISTINCT ON
            // already did the work; the dict is just lookup-shaped output).
            var grouped = rows
                .GroupBy(r => (r.canonical, r.asset_class))
                .Select(g => new
                {
                    canonical = g.Key.canonical,
                    asset_class = g.Key.asset_class,
                    cells = g.ToDictionary(
                        r => r.month,
                        r => new
                        {
                            status = MapResultToStatus(r.result),
                            last_result = r.result,
                            last_provider = r.provider_used,
                            rows_returned = r.rows_returned,
                            rows_expected = r.rows_expected,
                            occurred_at_utc = r.occurred_at_utc,
                        }),
                })
                .OrderBy(x => x.canonical)
                .ThenBy(x => x.asset_class)
                .ToList();

            return Results.Ok(new
            {
                months = monthLabels,
                asset_class = asset_class,
                resolution = resolution,
                rows = grouped,
            });
        });

        g.MapGet("/bar-cache/health", async (
            NpgsqlDataSource db,
            string? canonical,
            string? asset_class) =>
        {
            await using var conn = await db.OpenConnectionAsync();
            var rows = await conn.QueryAsync(@"
                SELECT canonical, asset_class,
                       last_fetched_at_utc, last_fetched_result,
                       last_fetched_provider, last_fetched_resolution,
                       coverage_start_date, coverage_end_date,
                       coverage_partitions, missing_days_count,
                       schema_version, manifest_violations_last_30d,
                       last_corp_action_at_utc, last_corp_action_type,
                       updated_at_utc
                FROM bar_cache_health
                WHERE (@canonical   IS NULL OR canonical   = @canonical)
                  AND (@asset_class IS NULL OR asset_class = @asset_class)
                ORDER BY canonical, asset_class;",
                new { canonical, asset_class });
            return Results.Ok(new { health = rows.AsList() });
        });

        return app;
    }

    public sealed record BarCacheEventBody(
        string Canonical,
        string AssetClass,
        string Resolution,
        DateTimeOffset RangeStartUtc,
        DateTimeOffset RangeEndUtc,
        string Result,
        string[]? SourceChain,
        string? ProviderUsed,
        System.Text.Json.JsonElement? ProviderVersions,
        int? RowsExpected,
        int? RowsReturned,
        int? GapsDetectedCount,
        string? SchemaVersion,
        int? LatencyMs,
        string? ErrorClass,
        string? ErrorProvider,
        string? ErrorMessage,
        string? RetryStrategy
    );

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

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
        "ibkr",     // IBKR ib_insync (local Gateway) — deep 1m, but hangs on contention
        "ibkr_web", // IBKR OAuth Web API via the central backend endpoint (Option B) —
                    // 1d/1h, no Gateway to babysit; the working IBKR-GOOD source
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

    // ── Freshness reference: last COMPLETED US regular session ──────
    //
    // A daily bar for a session only exists AFTER that session closes
    // (NYSE 16:00 America/New_York). So during (and just after the open
    // of) a live session the freshest COMPLETE daily bar is the PRIOR
    // trading day — that is not "stale", it is the latest bar that can
    // exist. The data-quality gate must measure "days behind" against
    // THIS, not the wall-clock calendar day, otherwise a normal intraday
    // 1-day lag reads as -1d / STALE and every symbol looks behind.
    //
    // Limitation (honest, not silent): US market holidays are not
    // modelled — on a holiday this returns the holiday date as if a
    // session ran, which can over-report freshness by one day. The
    // staleAfter slack (default 4) absorbs that without a holiday
    // calendar; revisit if we ever tighten staleAfter below 2.
    private static readonly TimeZoneInfo _easternTz =
        TimeZoneInfo.FindSystemTimeZoneById(
            OperatingSystem.IsWindows() ? "Eastern Standard Time" : "America/New_York");

    // 16:00 ET close + a settle buffer so a bar isn't called "complete"
    // the instant the bell rings (vendors publish the daily bar shortly
    // after the close, not at 16:00:00 sharp).
    private static readonly TimeSpan _usSessionCompleteEt = new(16, 15, 0);

    public static DateOnly LastCompletedUsSession(DateTime utcNow)
    {
        var et = TimeZoneInfo.ConvertTimeFromUtc(
            DateTime.SpecifyKind(utcNow, DateTimeKind.Utc), _easternTz);
        var d = DateOnly.FromDateTime(et.Date);
        bool todayComplete =
            d.DayOfWeek is not (DayOfWeek.Saturday or DayOfWeek.Sunday)
            && et.TimeOfDay >= _usSessionCompleteEt;
        if (!todayComplete) d = d.AddDays(-1);
        while (d.DayOfWeek is DayOfWeek.Saturday or DayOfWeek.Sunday)
            d = d.AddDays(-1);
        return d;
    }

    /// <summary>Trading sessions (weekdays) between a coverage-end date and the
    /// last completed session, counting days strictly AFTER coverageEnd up to and
    /// including lastSession. Weekend-safe (a Friday bar is 0 behind on the
    /// following Monday); holidays are not modelled (over-counts by at most the
    /// rare holiday, absorbed by the 1-session grace). 0 when coverage is at/after
    /// the last session.</summary>
    private static int SessionsBehind(DateTime coverageEnd, DateTime lastSession)
    {
        var c = coverageEnd.Date; var l = lastSession.Date;
        if (c >= l) return 0;
        int n = 0;
        for (var day = c.AddDays(1); day <= l; day = day.AddDays(1))
            if (day.DayOfWeek is not (DayOfWeek.Saturday or DayOfWeek.Sunday)) n++;
        return n;
    }

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

        // Ingest a per-symbol health record from the harvester (UPSERT on
        // canonical+asset_class). Bridges the "two disconnected caches" gap —
        // the harvest fills the local bar cache, then POSTs its coverage here
        // so the cockpit Harvest/Data-Health screen renders the real state.
        // Delete a health row — clears stale/test entries from the Harvest screen.
        g.MapDelete("/bar-cache/health/{canonical}", async (
            string canonical, NpgsqlDataSource db) =>
        {
            await using var conn = await db.OpenConnectionAsync();
            var n = await conn.ExecuteAsync(
                "DELETE FROM bar_cache_health WHERE canonical = @canonical;",
                new { canonical });
            return Results.Ok(new { deleted = n, canonical });
        });

        g.MapPost("/bar-cache/health", async (
            BarCacheHealthBody body, NpgsqlDataSource db) =>
        {
            if (string.IsNullOrWhiteSpace(body.Canonical) || string.IsNullOrWhiteSpace(body.AssetClass))
                return Results.BadRequest(new { error = "canonical + asset_class required" });
            await using var conn = await db.OpenConnectionAsync();
            // Resolution is part of the KEY (migration 064). Before that, the
            // 1d/5m/1m harvests overwrote each other's row for a symbol and the
            // table could only hold one lane's health — which is how a panel
            // titled "Daily bar-cache" ended up rendering 1m numbers.
            await conn.ExecuteAsync(@"
                INSERT INTO bar_cache_health
                    (canonical, asset_class, resolution,
                     last_fetched_at_utc, last_fetched_result,
                     last_fetched_provider, last_fetched_resolution,
                     coverage_start_date, coverage_end_date, coverage_partitions,
                     missing_days_count, schema_version, updated_at_utc)
                VALUES
                    (@Canonical, @AssetClass,
                     COALESCE(NULLIF(TRIM(@LastFetchedResolution), ''), 'unknown'),
                     COALESCE(@LastFetchedAtUtc, NOW()), @LastFetchedResult,
                     @LastFetchedProvider, @LastFetchedResolution,
                     @CoverageStartDate::date, @CoverageEndDate::date, @CoveragePartitions,
                     @MissingDaysCount, @SchemaVersion, NOW())
                ON CONFLICT (canonical, asset_class, resolution) DO UPDATE SET
                    last_fetched_at_utc     = COALESCE(EXCLUDED.last_fetched_at_utc, bar_cache_health.last_fetched_at_utc),
                    last_fetched_result     = EXCLUDED.last_fetched_result,
                    last_fetched_provider   = EXCLUDED.last_fetched_provider,
                    last_fetched_resolution = EXCLUDED.last_fetched_resolution,
                    coverage_start_date     = EXCLUDED.coverage_start_date,
                    coverage_end_date       = EXCLUDED.coverage_end_date,
                    coverage_partitions     = EXCLUDED.coverage_partitions,
                    missing_days_count      = EXCLUDED.missing_days_count,
                    schema_version          = EXCLUDED.schema_version,
                    updated_at_utc          = NOW();",
                body);
            return Results.Ok(new { ok = true, canonical = body.Canonical });
        });

        g.MapGet("/bar-cache/health", async (
            NpgsqlDataSource db,
            string? canonical,
            string? asset_class,
            string? resolution) =>
        {
            // Since 064 there is one row per (symbol, asset class, RESOLUTION).
            // Callers must say which lane they mean or they get a table that
            // silently interleaves daily and 1-minute health — the exact
            // confusion this key change exists to end. Default 1d: every
            // backtest, regime and "good for today" question is a daily one.
            var res = string.IsNullOrWhiteSpace(resolution) ? "1d" : resolution.Trim();
            await using var conn = await db.OpenConnectionAsync();
            var rows = await conn.QueryAsync(@"
                SELECT canonical, asset_class, resolution,
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
                  AND (@res = 'all' OR resolution = @res)
                ORDER BY canonical, asset_class, resolution;",
                new { canonical, asset_class, res });
            // Report which resolutions EXIST so the UI can offer them and can
            // distinguish "this lane has not run since migration 064" from
            // "this lane is unhealthy".
            var available = (await conn.QueryAsync<string>(
                "SELECT DISTINCT resolution FROM bar_cache_health ORDER BY resolution;")).AsList();
            return Results.Ok(new { health = rows.AsList(), resolution = res, available });
        });

        // Per-symbol DATA-QUALITY score — "is this symbol's data good enough to
        // decide on TODAY?" Rolls bar_cache_health into one honest verdict so the
        // Decide page + a Data-Health dashboard can show it, and so a stale/missing
        // symbol can be flagged (no false positives). Tiers MIRROR the Python
        // scorer (bar_cache/quality.py) — keep them in sync:
        //   MISSING < STALE (pending N days) < PARTIAL (gaps) < BRONZE (yfinance,
        //   flagged) < GOOD (fresh+complete+IBKR). good_for_today = GOOD|BRONZE.
        g.MapGet("/bar-cache/quality", async (
            NpgsqlDataSource db,
            string? asset_class,
            int? stale_after_days,
            string? resolution) =>
        {
            // Default 1 SESSION of grace (a bar can be one session behind between
            // a close and the nightly harvest). ≥2 sessions behind = STALE (a
            // real missed harvest), so a daily 2 sessions stale can't read
            // "fresh" through a gap day. Was 4 (calendar-day era) — too loose now
            // that dB is session-counted.
            int staleAfter = Math.Clamp(stale_after_days ?? 1, 1, 30);
            var today = DateTime.UtcNow.Date;
            // Freshness is measured against the last COMPLETED US session,
            // not the calendar day — so having yesterday's close mid-session
            // reads as 0-behind (the latest bar that can exist), not -1d.
            var lastSession = LastCompletedUsSession(DateTime.UtcNow);
            var lastSessionDate = lastSession.ToDateTime(TimeOnly.MinValue).Date;
            // ibkr_web IS IBKR data (the OAuth Web API, Option B) — trust it the
            // same as the local-gateway "ibkr". Otherwise every ibkr_web symbol
            // is wrongly capped to BRONZE.
            var trusted = new HashSet<string>(StringComparer.OrdinalIgnoreCase) { "ibkr", "ibkr_web", "ig" };
            // Today's session bar is incomplete during market hours and counts as
            // a "missing day"; a symbol otherwise fresh + deep should not be
            // demoted to "not good for today" for that (or one trivial old gap).
            const int minorGapTolerance = 2;

            await using var conn = await db.OpenConnectionAsync();
            // ONE lane only (064): without this filter every symbol would be
            // counted once per resolution, so a 251-symbol universe would
            // report 500+ and the GOOD/BRONZE/PARTIAL tallies would be the sum
            // of three different questions. 1d is the right default — this
            // endpoint answers "good enough to DECIDE on today", and every
            // decision path (regime, Ichimoku, HV, backtests) is daily.
            var qres = string.IsNullOrWhiteSpace(resolution) ? "1d" : resolution.Trim();
            var rows = (await conn.QueryAsync<(string Canonical, string AssetClass,
                    DateTime? CoverageEnd, int? MissingDays, string? Provider)>(@"
                SELECT canonical, asset_class, coverage_end_date,
                       missing_days_count, last_fetched_provider
                FROM bar_cache_health
                WHERE (@asset_class IS NULL OR asset_class = @asset_class)
                  AND resolution = @qres
                ORDER BY canonical;",
                new { asset_class, qres })).AsList();

            int good = 0, bronze = 0, partial = 0, stale = 0, missing = 0;
            var symbols = new List<object>();
            foreach (var r in rows)
            {
                var prov = (r.Provider ?? "").Trim().ToLowerInvariant();
                int miss = r.MissingDays ?? 0;
                string score; bool gft; int? daysBehind = null; string reason;
                if (r.CoverageEnd is null)
                {
                    score = "MISSING"; gft = false; missing++;
                    reason = "No cached bars — cannot decide on this symbol today.";
                }
                else
                {
                    // TRADING SESSIONS (weekdays) behind the last COMPLETED US
                    // session — NOT calendar days. Calendar-days over-counted
                    // weekends (a Fri bar read 3 behind on Monday) which forced a
                    // loose staleAfter=4, and THAT let a genuinely 2-session-stale
                    // daily (e.g. GOOGL two sessions behind through a -7% gap) read
                    // "fresh". Session-counting lets us tighten honestly. A symbol
                    // holding the last completed session's bar is 0-behind (fresh).
                    int dB = SessionsBehind(r.CoverageEnd.Value.Date, lastSessionDate);
                    daysBehind = dB;
                    if (dB > staleAfter)
                    {
                        score = "STALE"; gft = false; stale++;
                        reason = $"Last bar {dB} session(s) behind (> {staleAfter}) — data pending; NOT good for today's decision.";
                    }
                    else if (miss > minorGapTolerance)
                    {
                        score = "PARTIAL"; gft = false; partial++;
                        reason = $"{miss} session(s) missing inside coverage — gappy; not high-conviction.";
                    }
                    else if (!trusted.Contains(prov))
                    {
                        score = "BRONZE"; gft = true; bronze++;
                        reason = $"Fresh + complete but {(prov.Length == 0 ? "unknown" : prov)}-sourced (not IBKR) — ok for a first pass, not credible-backtest grade.";
                    }
                    else
                    {
                        score = "GOOD"; gft = true; good++;
                        reason = dB == 0
                            ? $"Current (has the last completed session) + complete + {prov.ToUpperInvariant()} — good for today."
                            : $"{dB} session(s) behind + complete + {prov.ToUpperInvariant()} — good for today.";
                    }
                }
                symbols.Add(new
                {
                    canonical = r.Canonical, asset_class = r.AssetClass,
                    score, good_for_today = gft, days_behind = daysBehind,
                    provider = prov, missing_days = miss,
                    coverage_end = r.CoverageEnd, reason,
                });
            }
            return Results.Ok(new
            {
                as_of = today,
                // The session freshness is measured against — clients can
                // show "data as of <session>" instead of implying today.
                last_completed_session = lastSession.ToString("yyyy-MM-dd"),
                stale_after_days = staleAfter,
                summary = new
                {
                    total = symbols.Count, good, bronze, partial, stale, missing,
                    good_for_today = good + bronze,
                },
                symbols,
            });
        });

        // Phase F-3 — Fill-quality analytics.
        //
        // Reads oms_fills WHERE mid_at_fill IS NOT NULL (the rows F-2
        // started capturing) and joins to oms_orders for side. Produces
        // two views:
        //
        //   1. ``recent_fills`` — last N fills with realised_bps. Sign
        //      convention: positive = paid more than mid (BUY filled
        //      above mid / SELL filled below mid). Negative = price
        //      improvement. Same convention regardless of side so the
        //      operator can sort + compare across buys and sells.
        //
        //   2. ``per_symbol_aggregates`` — n_fills + avg/median/p95
        //      bps per symbol, ordered by absolute avg descending so
        //      the widest-spread symbols surface first. The trader
        //      uses this to decide which symbols deserve a custom
        //      slippage_bps in backtests (vs the default 5bp).
        //
        // Empty until F-2 captures land in production. Frontend renders
        // an honest "no L1 data yet" empty state — preserves the
        // trust-before-breadth principle in the project memory.
        g.MapGet("/fill-quality", async (
            NpgsqlDataSource db,
            int? limit,
            string? broker,
            int? sinceDays) =>
        {
            int row_limit = Math.Clamp(limit ?? 50, 1, 500);
            int days = Math.Clamp(sinceDays ?? 30, 1, 365);
            await using var conn = await db.OpenConnectionAsync();

            // Recent fills — newest first. The bps math runs in SQL so
            // we don't ship 500 rows back to compute it in C#. The
            // CASE flips the sign so positive always = "worse than mid"
            // regardless of side.
            var recent = (await conn.QueryAsync<(
                long id, Guid order_id, string broker, string strategy_id,
                string symbol, string side,
                decimal qty, decimal price, decimal? bid_at_fill,
                decimal? ask_at_fill, decimal? mid_at_fill,
                string? snapshot_source,
                DateTime fill_at_utc, DateTime? snapshot_at_utc,
                decimal? realised_bps)>(@"
                SELECT
                    f.id, f.order_id,
                    o.broker, o.strategy_id, o.symbol, o.side,
                    f.qty, f.price,
                    f.bid_at_fill, f.ask_at_fill, f.mid_at_fill,
                    f.snapshot_source,
                    f.fill_at_utc, f.snapshot_at_utc,
                    CASE
                        WHEN f.mid_at_fill IS NULL OR f.mid_at_fill = 0 THEN NULL
                        WHEN upper(o.side) = 'BUY'
                            THEN (f.price - f.mid_at_fill) / f.mid_at_fill * 10000
                        ELSE
                            (f.mid_at_fill - f.price) / f.mid_at_fill * 10000
                    END AS realised_bps
                FROM oms_fills f
                JOIN oms_orders o ON o.id = f.order_id
                WHERE f.mid_at_fill IS NOT NULL
                  AND f.fill_at_utc >= NOW() - (@days || ' days')::INTERVAL
                  AND (@broker IS NULL OR o.broker = @broker)
                ORDER BY f.fill_at_utc DESC
                LIMIT @row_limit;",
                new { row_limit, days = days.ToString(), broker })).ToList();

            // Per-symbol aggregates — only over the same window.
            // Postgres percentile_cont is the cleanest median + p95.
            // Worth noting: this is the raw "what the broker gave us"
            // distribution; Phase F-3.1 will add an outlier filter
            // (drop top 5% of bps) once we have enough data to know
            // what's outlier vs structural.
            var per_symbol = (await conn.QueryAsync<(
                string broker, string symbol, int n_fills,
                decimal? avg_bps, decimal? median_bps, decimal? p95_bps,
                decimal? min_bps, decimal? max_bps)>(@"
                WITH base AS (
                    SELECT
                        o.broker, o.symbol,
                        CASE
                            WHEN upper(o.side) = 'BUY'
                                THEN (f.price - f.mid_at_fill) / f.mid_at_fill * 10000
                            ELSE
                                (f.mid_at_fill - f.price) / f.mid_at_fill * 10000
                        END AS bps
                    FROM oms_fills f
                    JOIN oms_orders o ON o.id = f.order_id
                    WHERE f.mid_at_fill IS NOT NULL
                      AND f.mid_at_fill <> 0
                      AND f.fill_at_utc >= NOW() - (@days || ' days')::INTERVAL
                      AND (@broker IS NULL OR o.broker = @broker)
                )
                SELECT broker, symbol,
                       COUNT(*)::int                                       AS n_fills,
                       AVG(bps)                                            AS avg_bps,
                       percentile_cont(0.5) WITHIN GROUP (ORDER BY bps)    AS median_bps,
                       percentile_cont(0.95) WITHIN GROUP (ORDER BY bps)   AS p95_bps,
                       MIN(bps)                                            AS min_bps,
                       MAX(bps)                                            AS max_bps
                FROM base
                GROUP BY broker, symbol
                ORDER BY ABS(AVG(bps)) DESC NULLS LAST, symbol
                LIMIT 100;",
                new { days = days.ToString(), broker })).ToList();

            return Results.Ok(new
            {
                window_days = days,
                broker = broker,
                recent_fills = recent,
                per_symbol_aggregates = per_symbol,
                empty_state = recent.Count == 0,
            });
        });

        // Phase P2.1 — Harvester runtime status for operational
        // visibility. Reads from the in-process singleton (no DB
        // hit) so the cockpit panel renders sub-ms. Support team's
        // "is the harvester alive + how stale is the last tick"
        // question answers here.
        g.MapGet("/ig-snapshots/status", (
            TradePro.Api.Providers.IG.IGHarvesterStatus status) =>
        {
            var now = DateTime.UtcNow;
            int? secondsSinceLastTick = status.LastTickAtUtc is DateTime t
                ? (int)Math.Max(0, (now - t).TotalSeconds)
                : null;
            int? secondsUntilNextTick = status.NextTickEtaUtc is DateTime n
                ? (int)Math.Max(0, (n - now).TotalSeconds)
                : null;
            // Health verdict — what colour the cockpit chip lights.
            //   disabled — config off (yellow)
            //   never_ticked — running but no tick landed yet (yellow)
            //   healthy — tick recent + no error (green)
            //   stale — last tick > 3 intervals ago (red)
            //   error — last tick threw + LastError populated (red)
            string verdict;
            if (!status.Enabled) verdict = "disabled";
            else if (status.LastTickAtUtc is null) verdict = "never_ticked";
            else if (!string.IsNullOrEmpty(status.LastError)) verdict = "error";
            else if (secondsSinceLastTick > status.IntervalSeconds * 3) verdict = "stale";
            else verdict = "healthy";

            return Results.Ok(new
            {
                verdict,
                enabled = status.Enabled,
                interval_seconds = status.IntervalSeconds,
                configured_epic_count = status.ConfiguredEpicCount,
                last_tick_at_utc = status.LastTickAtUtc,
                next_tick_eta_utc = status.NextTickEtaUtc,
                seconds_since_last_tick = secondsSinceLastTick,
                seconds_until_next_tick = secondsUntilNextTick,
                last_tick_captured = status.LastTickCaptured,
                last_tick_failed = status.LastTickFailed,
                started_at_utc = status.StartedAtUtc,
                last_error = status.LastError,
                server_time_utc = now,
            });
        });

        // Phase P2 — IG L1 snapshot lake. The harvester writes one
        // row per epic per poll into ig_l1_snapshots; this surface
        // backs the cockpit panel + the future replay engine.
        //
        // Two views in one endpoint:
        //   * `recent_snapshots` — last N rows for a symbol filter,
        //     newest first. Drives the "harvester activity" panel.
        //   * `per_symbol_aggregates` — count + min/max/avg spread
        //     over the window, ordered by lowest avg spread first
        //     (that's the cleanest book). Drives the "where is our
        //     IG quote quality best" panel.
        g.MapGet("/ig-snapshots/recent", async (
            NpgsqlDataSource db,
            string? symbol,
            int? limit,
            int? sinceHours) =>
        {
            int row_limit = Math.Clamp(limit ?? 100, 1, 1000);
            int hours = Math.Clamp(sinceHours ?? 24, 1, 24 * 30);
            await using var conn = await db.OpenConnectionAsync();

            var recent = (await conn.QueryAsync<(
                long id, string symbol, string epic,
                decimal? bid, decimal? ask, decimal? mid, decimal? spread_bps,
                string? market_status, string? update_time,
                DateTime captured_at_utc, string source, string? error)>(@"
                SELECT id, symbol, epic, bid, ask, mid, spread_bps,
                       market_status, update_time, captured_at_utc, source, error
                FROM ig_l1_snapshots
                WHERE captured_at_utc >= NOW() - (@hours || ' hours')::INTERVAL
                  AND (@symbol IS NULL OR symbol = @symbol)
                ORDER BY captured_at_utc DESC
                LIMIT @row_limit;",
                new { row_limit, hours = hours.ToString(), symbol })).ToList();

            // Per-symbol aggregate over the same window. Min/max/avg
            // spread_bps + count of polls vs count with usable quote
            // (bid AND ask non-null). The "fill rate" tells you how
            // well IG actually served us — a symbol with 100 polls but
            // 30% empty bid/ask is information.
            var per_symbol = (await conn.QueryAsync<(
                string symbol, int n_polls, int n_quotes,
                decimal? avg_spread_bps, decimal? min_spread_bps,
                decimal? max_spread_bps,
                DateTime? last_seen_utc)>(@"
                SELECT
                    symbol,
                    COUNT(*)::int                                        AS n_polls,
                    SUM(CASE WHEN bid IS NOT NULL AND ask IS NOT NULL THEN 1 ELSE 0 END)::int
                                                                          AS n_quotes,
                    AVG(spread_bps)                                       AS avg_spread_bps,
                    MIN(spread_bps)                                       AS min_spread_bps,
                    MAX(spread_bps)                                       AS max_spread_bps,
                    MAX(captured_at_utc)                                  AS last_seen_utc
                FROM ig_l1_snapshots
                WHERE captured_at_utc >= NOW() - (@hours || ' hours')::INTERVAL
                  AND (@symbol IS NULL OR symbol = @symbol)
                GROUP BY symbol
                ORDER BY AVG(spread_bps) NULLS LAST, symbol
                LIMIT 200;",
                new { hours = hours.ToString(), symbol })).ToList();

            return Results.Ok(new
            {
                window_hours = hours,
                symbol_filter = symbol,
                recent_snapshots = recent,
                per_symbol_aggregates = per_symbol,
                empty_state = recent.Count == 0,
            });
        });

        // ── IBKR bar-cache visibility ───────────────────────────────
        // GET  /api/admin/data-trust/ibkr/status
        //   Returns: last N bar_cache_events where provider_used = 'ibkr',
        //   plus a stub connection_status field (static "configured" for now
        //   — a live TWS ping would require a sidecar process the operator
        //   may not want always-on). The UI uses this to render the IBKR
        //   harvester panel without having to filter the generic events table.
        //
        // POST /api/admin/data-trust/ibkr/fetch-bars
        //   Triggers an immediate data_backfill op scoped to the IBKR
        //   provider (prepends 'ibkr' to the effective chain for this
        //   request). Delegates to /api/ops/run-data-backfill; this
        //   endpoint is the IBKR-specific UI entry point that hides the
        //   provider-chain wiring from the operator.

        g.MapGet("/ibkr/status", async (NpgsqlDataSource db,
            int limit = 50) =>
        {
            limit = Math.Max(1, Math.Min(limit, 500));
            await using var conn = await db.OpenConnectionAsync();

            // Recent events where the IBKR provider was used (or attempted).
            var events = (await conn.QueryAsync(@"
                SELECT
                    id, occurred_at_utc, canonical, asset_class,
                    resolution, range_start_utc, range_end_utc,
                    result, provider_used,
                    rows_returned, rows_expected, latency_ms,
                    error_class, error_message
                FROM bar_cache_events
                WHERE provider_used = 'ibkr'
                   OR error_provider = 'ibkr'
                ORDER BY occurred_at_utc DESC
                LIMIT @limit",
                new { limit })).ToList();

            // Aggregate: how many symbols have at least one successful IBKR fetch?
            var successCount = events.Count(e =>
                e.result is "complete" or "fetched_complete");
            var lastFetchAt  = events.Count > 0
                ? (object)events[0].occurred_at_utc : null;

            return Results.Json(new
            {
                // "configured" = provider is registered + gateway env vars set;
                // a live TCP ping is deferred (operator may not run TWS 24/7).
                connection_status = "configured",
                connection_hint   = "TWS paper port 7497 (env: TRADEPRO_IBKR_HOST/PORT). " +
                                    "Start TWS or IB Gateway before fetching bars.",
                last_fetch_at_utc = lastFetchAt,
                success_count_last_n = successCount,
                event_count      = events.Count,
                events           = events,
                valid_resolutions = new[] { "1m","2m","5m","15m","30m","1h","1d","1wk","1mo" },
                depth_summary    = new Dictionary<string, string>
                {
                    ["1m"]  = "≈1 year (vs yfinance 7 days)",
                    ["5m"]  = "≈3 years",
                    ["15m"] = "≈3.5 years",
                    ["1h"]  = "≈3.5 years",
                    ["1d"]  = "decades",
                },
            });
        });

        g.MapPost("/ibkr/fetch-bars", async (
            IbkrFetchBarsBody body,
            HttpClient http,
            IConfiguration config) =>
        {
            // Validate inputs before forwarding.
            if (string.IsNullOrWhiteSpace(body.Canonical))
                return Results.BadRequest(new { error = "canonical required" });
            if (string.IsNullOrWhiteSpace(body.AssetClass))
                return Results.BadRequest(new { error = "asset_class required" });
            if (string.IsNullOrWhiteSpace(body.Resolution))
                return Results.BadRequest(new { error = "resolution required" });
            if (string.IsNullOrWhiteSpace(body.From))
                return Results.BadRequest(new { error = "from date required (YYYY-MM-DD)" });

            var validRes = new[]
                { "1m","2m","5m","15m","30m","1h","1d","1wk","1mo" };
            if (!validRes.Contains(body.Resolution))
                return Results.BadRequest(new
                {
                    error   = "unsupported resolution",
                    allowed = validRes,
                });

            // Delegate to the ops backfill endpoint so the same job queue,
            // state machine, and worker pick-up logic handle it. The
            // provider_hint is advisory — the data worker prepends "ibkr"
            // to the configured chain when it sees it.
            var opsBase = config["TradePro:OpsBaseUrl"]
                ?? "http://localhost:5000";
            var payload = System.Text.Json.JsonSerializer.Serialize(new
            {
                canonical     = body.Canonical,
                asset_class   = body.AssetClass,
                resolution    = body.Resolution,
                from          = body.From,
                to            = body.To ?? DateOnly.FromDateTime(DateTime.UtcNow).ToString("yyyy-MM-dd"),
                allow_partial = body.AllowPartial ?? true,
                provider_hint = "ibkr",   // worker reads this; prepends ibkr to chain
            });
            try
            {
                var resp = await http.PostAsync(
                    $"{opsBase}/api/ops/run-data-backfill",
                    new StringContent(payload, System.Text.Encoding.UTF8, "application/json"));
                var body2 = await resp.Content.ReadAsStringAsync();
                if (!resp.IsSuccessStatusCode)
                    return Results.Json(
                        System.Text.Json.JsonSerializer.Deserialize<object>(body2),
                        statusCode: (int)resp.StatusCode);
                return Results.Json(
                    System.Text.Json.JsonSerializer.Deserialize<object>(body2));
            }
            catch (Exception ex)
            {
                return Results.Problem(
                    detail: ex.Message,
                    title:  "Failed to forward IBKR fetch request to ops queue",
                    statusCode: 502);
            }
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

    public sealed record IbkrFetchBarsBody(
        string Canonical,
        string AssetClass,
        string Resolution,
        string From,
        string? To           = null,
        bool?  AllowPartial  = null
    );

    public sealed record BarCacheHealthBody(
        string  Canonical,
        string  AssetClass,
        DateTime? LastFetchedAtUtc      = null,
        string?   LastFetchedResult     = null,
        string?   LastFetchedProvider   = null,
        string?   LastFetchedResolution = null,
        string?   CoverageStartDate     = null,
        string?   CoverageEndDate       = null,
        int       CoveragePartitions    = 0,
        int       MissingDaysCount      = 0,
        string?   SchemaVersion         = null
    );
}

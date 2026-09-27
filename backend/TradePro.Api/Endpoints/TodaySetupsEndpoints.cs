using Dapper;
using Npgsql;
using TradePro.Api.Data.Stores;

namespace TradePro.Api.Endpoints;

/// <summary>
/// /api/today-setups/* — read API for the "Today's Setups" scanner artifact
/// produced by `tradepro-today-setups` on the Mac (screens a universe →
/// per-symbol Ichimoku signal + range/risk → ranked by ENTRY QUALITY:
/// ⭐ consider / ⚠ extended / excluded). Powers the dashboard scanner card —
/// the curated, risk-aware replacement for the original flat BUY-light board.
///
/// Mac worker pushes the artifact via /api/ingest/today-setups (IngestToken).
/// Keyed by (universe, label) — same store pattern as equity-pipeline / fill-replay.
/// </summary>
public static class TodaySetupsEndpoints
{
    public static IEndpointRouteBuilder MapTodaySetupsUserEndpoints(this IEndpointRouteBuilder app)
    {
        var group = app.MapGroup("/today-setups").WithTags("TodaySetups");

        // GET /api/today-setups/{universe}/latest
        group.MapGet("/{universe}/latest", async (
            string universe, string? label, NpgsqlDataSource db) =>
        {
            var l = string.IsNullOrWhiteSpace(label) ? "latest" : label;
            await using var conn = await db.OpenConnectionAsync();
            var row = await conn.QueryFirstOrDefaultAsync<SetupsRow>(@"
                SELECT artifact::text AS artifact_text,
                       as_of_utc, uploaded_at_utc, uploaded_by, note
                FROM today_setups_results
                WHERE universe = @universe AND label = @label
                LIMIT 1;",
                new { universe, label = l });
            if (row is null)
            {
                return Results.NotFound(new
                {
                    error = $"no today-setups artifact for {universe} (label={l})",
                    hint = "run `tradepro-today-setups --push` on the worker host",
                });
            }
            return Results.Ok(new
            {
                universe,
                label = l,
                asOfUtc = row.as_of_utc,
                uploadedAtUtc = row.uploaded_at_utc,
                uploadedBy = row.uploaded_by,
                note = row.note,
                artifact = JsonbHelpers.FromJsonb(row.artifact_text),
            });
        });

        // ── THE ARCHIVE, READABLE (migration 082) ─────────────────────────
        //
        // REGISTERED ON THE READ GROUP, and that is the whole point of this
        // note. These first went on the /ingest group a few lines below,
        // because both groups live in this one file and the variable is
        // called `group` in both. The routes deployed fine and answered 401
        // on /api/ingest/... — an authenticated WRITE path — while the read
        // path 404'd. I spent a deploy cycle blaming a build race before
        // reading the file. Two groups, one variable name, in one scope.
        //
        // An archive nothing can read is a slower way of losing the data. Two
        // shapes, because there are two questions:
        //
        //   /{strategy}/history          what did this strategy say, day by day
        //   /{strategy}/on/{date}        what did it say on THAT day
        //
        // `history` returns dates and row counts by default, not artifacts —
        // five weeks of full boards is megabytes, and the caller almost always
        // wants to pick a date first. `full=true` returns the artifacts.
        group.MapGet("/{strategy}/history", async (
            string strategy, int? limit, bool? full, NpgsqlDataSource db) =>
        {
            var n = Math.Clamp(limit ?? 90, 1, 400);
            await using var conn = await db.OpenConnectionAsync();
            var rows = (await conn.QueryAsync<ArchiveRow>(@"
                SELECT as_of_date, as_of_utc, archived_at_utc,
                       artifact::text AS artifact_text,
                       COALESCE(jsonb_array_length(artifact->'candidates_v2'), 0) AS rows_published
                  FROM signal_archive
                 WHERE strategy = @strategy
                 ORDER BY as_of_date DESC
                 LIMIT @n;", new { strategy, n })).ToList();

            return Results.Ok(new
            {
                strategy,
                days = rows.Count,
                // Say the RANGE, so a caller can tell "no history" from
                // "history starts later than you assumed".
                firstDate = rows.Count > 0 ? rows[^1].as_of_date : (DateTime?)null,
                lastDate = rows.Count > 0 ? rows[0].as_of_date : (DateTime?)null,
                entries = rows.Select(r => new
                {
                    date = r.as_of_date,
                    asOfUtc = r.as_of_utc,
                    archivedAtUtc = r.archived_at_utc,
                    rowsPublished = r.rows_published,
                    artifact = (full ?? false)
                        ? System.Text.Json.JsonDocument.Parse(r.artifact_text).RootElement
                        : (System.Text.Json.JsonElement?)null,
                }),
            });
        });

        group.MapGet("/{strategy}/on/{date}", async (
            string strategy, string date, NpgsqlDataSource db) =>
        {
            if (!DateTime.TryParse(date, out var d))
                return Results.BadRequest(new { error = "date must be YYYY-MM-DD" });
            await using var conn = await db.OpenConnectionAsync();
            var row = await conn.QueryFirstOrDefaultAsync<ArchiveRow>(@"
                SELECT as_of_date, as_of_utc, archived_at_utc,
                       artifact::text AS artifact_text, 0 AS rows_published
                  FROM signal_archive
                 WHERE strategy = @strategy AND as_of_date = @d;",
                new { strategy, d = d.Date });

            // A day with no row is NOT an empty board — it is a day we did not
            // record. Those are different facts and the caller must be able to
            // tell them apart.
            if (row is null)
                return Results.NotFound(new
                {
                    strategy,
                    date = d.Date,
                    error = "no signals archived for this strategy on this date — "
                          + "this means NOT RECORDED, not 'no candidates'",
                });

            return Results.Ok(new
            {
                strategy,
                date = row.as_of_date,
                asOfUtc = row.as_of_utc,
                archivedAtUtc = row.archived_at_utc,
                artifact = System.Text.Json.JsonDocument.Parse(row.artifact_text).RootElement,
            });
        });

        return app;
    }
    public static IEndpointRouteBuilder MapTodaySetupsIngestEndpoints(this IEndpointRouteBuilder app)
    {
        var group = app.MapGroup("/ingest")
            .WithTags("TodaySetups/Ingest")
            .RequireAuthorization(Auth.IngestTokenAuth.Policy);

        // POST /api/ingest/today-setups
        // Body: { "universe": "large_50", "label": "latest", "uploaded_by": "...",
        //         "note": "...", "artifact": { ...CLI emit... } }
        // ── THE ARCHIVE, READABLE (migration 082) ─────────────────────────
        //
        // An archive nothing can read is a slower way of losing the data. Two
        // shapes, because there are two questions:
        //
        //   /{strategy}/history          what did this strategy say, day by day
        //   /{strategy}/on/{date}        what did it say on THAT day
        //
        // `history` returns dates and row counts by default, not artifacts —
        // five weeks of full boards is megabytes, and the caller almost always
        // wants to pick a date first. `full=true` returns the artifacts.
        group.MapGet("/{strategy}/history", async (
            string strategy, int? limit, bool? full, NpgsqlDataSource db) =>
        {
            var n = Math.Clamp(limit ?? 90, 1, 400);
            await using var conn = await db.OpenConnectionAsync();
            var rows = (await conn.QueryAsync<ArchiveRow>(@"
                SELECT as_of_date, as_of_utc, archived_at_utc,
                       artifact::text AS artifact_text,
                       COALESCE(jsonb_array_length(artifact->'candidates_v2'), 0) AS rows_published
                  FROM signal_archive
                 WHERE strategy = @strategy
                 ORDER BY as_of_date DESC
                 LIMIT @n;", new { strategy, n })).ToList();

            return Results.Ok(new
            {
                strategy,
                days = rows.Count,
                // Say the RANGE, so a caller can tell "no history" from
                // "history starts later than you assumed".
                firstDate = rows.Count > 0 ? rows[^1].as_of_date : (DateTime?)null,
                lastDate = rows.Count > 0 ? rows[0].as_of_date : (DateTime?)null,
                entries = rows.Select(r => new
                {
                    date = r.as_of_date,
                    asOfUtc = r.as_of_utc,
                    archivedAtUtc = r.archived_at_utc,
                    rowsPublished = r.rows_published,
                    artifact = (full ?? false)
                        ? System.Text.Json.JsonDocument.Parse(r.artifact_text).RootElement
                        : (System.Text.Json.JsonElement?)null,
                }),
            });
        });

        group.MapGet("/{strategy}/on/{date}", async (
            string strategy, string date, NpgsqlDataSource db) =>
        {
            if (!DateTime.TryParse(date, out var d))
                return Results.BadRequest(new { error = "date must be YYYY-MM-DD" });
            await using var conn = await db.OpenConnectionAsync();
            var row = await conn.QueryFirstOrDefaultAsync<ArchiveRow>(@"
                SELECT as_of_date, as_of_utc, archived_at_utc,
                       artifact::text AS artifact_text, 0 AS rows_published
                  FROM signal_archive
                 WHERE strategy = @strategy AND as_of_date = @d;",
                new { strategy, d = d.Date });

            // A day with no row is NOT an empty board — it is a day we did not
            // record. Those are different facts and the caller must be able to
            // tell them apart.
            if (row is null)
                return Results.NotFound(new
                {
                    strategy,
                    date = d.Date,
                    error = "no signals archived for this strategy on this date — "
                          + "this means NOT RECORDED, not 'no candidates'",
                });

            return Results.Ok(new
            {
                strategy,
                date = row.as_of_date,
                asOfUtc = row.as_of_utc,
                archivedAtUtc = row.archived_at_utc,
                artifact = System.Text.Json.JsonDocument.Parse(row.artifact_text).RootElement,
            });
        });

        group.MapPost("/today-setups", async (
            System.Text.Json.JsonElement payload, NpgsqlDataSource db,
            ILogger<Program> log) =>
        {
            if (payload.ValueKind != System.Text.Json.JsonValueKind.Object)
                return Results.BadRequest(new { error = "payload must be a JSON object" });
            var universe = JsonbHelpers.ReadString(payload, "universe");
            if (string.IsNullOrWhiteSpace(universe))
                return Results.BadRequest(new { error = "universe is required" });
            var label = JsonbHelpers.ReadString(payload, "label") ?? "latest";
            var uploadedBy = JsonbHelpers.ReadString(payload, "uploaded_by");
            var note = JsonbHelpers.ReadString(payload, "note");

            if (!payload.TryGetProperty("artifact", out var artifact)
                || artifact.ValueKind != System.Text.Json.JsonValueKind.Object)
            {
                return Results.BadRequest(new { error = "artifact must be a JSON object" });
            }

            DateTime asOf = DateTime.UtcNow;
            if (artifact.TryGetProperty("as_of_utc", out var asOfEl)
                && asOfEl.ValueKind == System.Text.Json.JsonValueKind.String
                && DateTime.TryParse(asOfEl.GetString(), out var parsed))
            {
                asOf = parsed.ToUniversalTime();
            }

            var artifactJson = JsonbHelpers.ToJsonb(artifact);

            await using var conn = await db.OpenConnectionAsync();
            await conn.ExecuteAsync(@"
                INSERT INTO today_setups_results
                  (universe, label, artifact, as_of_utc, uploaded_at_utc,
                   uploaded_by, note)
                VALUES (@universe, @label, @artifactJson::jsonb,
                        @asOf, NOW(), @uploadedBy, @note)
                ON CONFLICT (universe, label) DO UPDATE
                SET artifact = EXCLUDED.artifact,
                    as_of_utc = EXCLUDED.as_of_utc,
                    uploaded_at_utc = NOW(),
                    uploaded_by = EXCLUDED.uploaded_by,
                    note = EXCLUDED.note;",
                new { universe, label, artifactJson, asOf, uploadedBy, note });

            // ── ARCHIVE THE DAY (migration 082, 26 Sep 2026) ──────────────
            //
            // The upsert above keeps ONE row per (universe,label) — today's.
            // Every publish overwrote the previous one, so five weeks of daily
            // swing signals were discarded a morning at a time, and live
            // evaluation now rests on eight completed round trips because the
            // signals themselves are gone.
            //
            // This keeps one row per strategy PER DAY. Re-publishing today
            // replaces today; yesterday is never touched. That is the property
            // that matters — a run can correct itself and can never erase
            // history.
            //
            // Archiving is SECONDARY: it runs after the live upsert has
            // committed, and a failure here is logged and swallowed. Losing a
            // day of archive is bad; failing the publish and leaving the desk
            // with no board at all is worse, and the board is what someone is
            // about to trade from.
            var archived = false;
            string? archiveError = null;
            try
            {
                await conn.ExecuteAsync(@"
                    INSERT INTO signal_archive
                      (strategy, as_of_date, artifact, as_of_utc, archived_at_utc)
                    VALUES (@universe, (@asOf AT TIME ZONE 'UTC')::date,
                            @artifactJson::jsonb, @asOf, NOW())
                    ON CONFLICT (strategy, as_of_date) DO UPDATE
                    SET artifact = EXCLUDED.artifact,
                        as_of_utc = EXCLUDED.as_of_utc,
                        archived_at_utc = NOW();",
                    new { universe, artifactJson, asOf });
                archived = true;
            }
            catch (Exception ex)
            {
                // Named, never silent: an archive that quietly stops is
                // indistinguishable from one that never ran, which is the
                // whole reason this table exists.
                archiveError = ex.Message;
                log.LogError(ex,
                    "signal_archive write FAILED for {Universe} {AsOf} — the board "
                    + "published but this day will be MISSING from the archive",
                    universe, asOf);
            }

            return Results.Ok(new
            {
                accepted = true, universe, label, asOfUtc = asOf,
                archived, archiveError,
            });
        });

        return app;
    }

    private sealed record ArchiveRow(
        DateTime as_of_date,
        DateTime as_of_utc,
        DateTime archived_at_utc,
        string artifact_text,
        int rows_published);

    private sealed record SetupsRow(
        string artifact_text,
        DateTime as_of_utc,
        DateTime uploaded_at_utc,
        string? uploaded_by,
        string? note);
}

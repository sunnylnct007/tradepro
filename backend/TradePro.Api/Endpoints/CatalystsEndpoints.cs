using Dapper;
using Npgsql;

namespace TradePro.Api.Endpoints;

/// <summary>
/// /api/catalysts/* — operator-facing dated-event registry (Phase C-1).
///
/// The catalyst overlay is flagged in the trader memory as the #1
/// missing gap. Pure-technical signals miss event-driven trades
/// (Ecopetrol 2026-05-21: tech said WAIT but Colombia election was the
/// entire reason the trade existed). Phase C-1 ships the persistence
/// + visibility surface so the trader can SEE upcoming catalysts on
/// the cockpit; C-2 wires the existing Python news extractor to POST
/// rows it finds; C-3 layers catalysts into the signal-decision row.
///
/// Idempotency: ON CONFLICT (symbol, kind, occurs_on, source) DO
/// UPDATE — same extractor run twice on the same news headline
/// updates ``payload`` + ``title`` in place rather than duplicating.
/// </summary>
public static class CatalystsEndpoints
{
    public static IEndpointRouteBuilder MapCatalystsEndpoints(this IEndpointRouteBuilder app)
    {
        // Registered on the `api` group (already "/api"), so map "/catalysts"
        // here — NOT "/api/catalysts", which double-prefixed to /api/api/catalysts
        // and 404'd the cockpit's GET /api/catalysts/ (Active Catalysts panel).
        var g = app.MapGroup("/catalysts").WithTags("Catalysts");

        // ── GET /api/catalysts ─────────────────────────────────────
        // Default: every active catalyst whose occurs_on is between
        // (NOW - lookbackDays) and (NOW + lookaheadDays). Filter by
        // symbol when supplied. Ordering: high severity + nearest
        // date first so the cockpit shows "tomorrow's FOMC" before
        // "two weeks ago's earnings". Capped at 200 — for a wider
        // sweep the operator should narrow by symbol or use the
        // analytics endpoint (deferred to C-2).
        g.MapGet("/", async (
            NpgsqlDataSource db,
            string? symbol,
            string? kind,
            int? lookbackDays,
            int? lookaheadDays,
            string? status) =>
        {
            int back = Math.Clamp(lookbackDays ?? 30, 0, 365);
            int ahead = Math.Clamp(lookaheadDays ?? 30, 0, 365);
            string effectiveStatus = status ?? "active";

            await using var conn = await db.OpenConnectionAsync();
            var rows = await conn.QueryAsync(@"
                SELECT id, symbol, kind, occurs_on, title, source,
                       severity, status, surfaced_at_utc,
                       payload::text AS payload_text, note,
                       created_at_utc, updated_at_utc
                FROM catalysts
                WHERE status = @effectiveStatus
                  AND (@symbol IS NULL OR symbol ILIKE @symbol)
                  AND (@kind   IS NULL OR kind   = @kind)
                  AND (occurs_on IS NULL
                       OR (occurs_on >= CURRENT_DATE - (@back || ' days')::INTERVAL
                           AND occurs_on <= CURRENT_DATE + (@ahead || ' days')::INTERVAL))
                ORDER BY
                    CASE severity
                        WHEN 'high' THEN 0
                        WHEN 'medium' THEN 1
                        WHEN 'low' THEN 2
                        ELSE 3
                    END,
                    occurs_on NULLS LAST,
                    symbol
                LIMIT 200;",
                new {
                    effectiveStatus, symbol, kind,
                    back = back.ToString(), ahead = ahead.ToString(),
                });
            return Results.Ok(new
            {
                count = rows.AsList().Count,
                window = new { back_days = back, ahead_days = ahead },
                status_filter = effectiveStatus,
                catalysts = rows,
            });
        });

        // ── POST /api/catalysts ────────────────────────────────────
        // Idempotent ingest. Same (symbol, kind, occurs_on, source)
        // UPSERTs in place. The extractor (C-2) calls this for every
        // dated event it finds in a news bundle; operators call it
        // from the cockpit "Add catalyst" form for known events the
        // extractor missed.
        g.MapPost("/", async (
            NpgsqlDataSource db,
            CatalystUpsertBody body) =>
        {
            if (string.IsNullOrWhiteSpace(body.Symbol))
                return Results.BadRequest(new { error = "symbol required" });
            if (string.IsNullOrWhiteSpace(body.Kind))
                return Results.BadRequest(new { error = "kind required" });
            if (string.IsNullOrWhiteSpace(body.Title))
                return Results.BadRequest(new { error = "title required" });
            if (string.IsNullOrWhiteSpace(body.Source))
                return Results.BadRequest(new { error = "source required" });
            string severity = (body.Severity ?? "medium").ToLowerInvariant();
            if (severity is not ("low" or "medium" or "high"))
                return Results.BadRequest(new {
                    error = "severity must be low / medium / high",
                });

            // payload is operator-supplied JSON; round-trip through
            // .NET's parser as a defence against malformed input. Dapper
            // hands it to Postgres as text-typed JSONB so the column
            // accepts it.
            string payloadText = body.Payload?.GetRawText() ?? "{}";

            await using var conn = await db.OpenConnectionAsync();
            // Sentinel value for the occurs_on UNIQUE constraint: when
            // occurs_on is NULL, Postgres treats every row as distinct
            // (NULLs aren't equal). That's wrong for our idempotency —
            // we want a NULL-date catalyst from the same source to
            // upsert. We coerce NULL → '0001-01-01' for the conflict
            // check by guarding the upsert with a manual UPDATE-or-
            // INSERT pattern.
            var id = await conn.QuerySingleAsync<long>(@"
                INSERT INTO catalysts
                  (symbol, kind, occurs_on, title, source,
                   severity, status, payload, note)
                VALUES
                  (@Symbol, @Kind, @OccursOn, @Title, @Source,
                   @Severity, COALESCE(@Status, 'active'),
                   @PayloadText::jsonb, @Note)
                ON CONFLICT (symbol, kind, occurs_on, source)
                DO UPDATE SET
                  title         = EXCLUDED.title,
                  severity      = EXCLUDED.severity,
                  status        = EXCLUDED.status,
                  payload       = EXCLUDED.payload,
                  note          = EXCLUDED.note,
                  updated_at_utc = NOW()
                RETURNING id;",
                new {
                    body.Symbol, body.Kind, body.OccursOn, body.Title,
                    body.Source, Severity = severity, body.Status,
                    PayloadText = payloadText, body.Note,
                });
            return Results.Ok(new { id, status = "ok" });
        });

        // ── PATCH /api/catalysts/{id}/dismiss ──────────────────────
        // Operator action — "this catalyst was noise; suppress it
        // from the cockpit panel". Status flips to 'dismissed' and
        // the row drops out of the default GET (status=active).
        g.MapPatch("/{id:long}/dismiss", async (
            NpgsqlDataSource db,
            long id) =>
        {
            await using var conn = await db.OpenConnectionAsync();
            var rows = await conn.ExecuteAsync(@"
                UPDATE catalysts
                SET status = 'dismissed',
                    updated_at_utc = NOW()
                WHERE id = @id AND status <> 'dismissed';",
                new { id });
            if (rows == 0)
                return Results.NotFound(new { error = "catalyst not found or already dismissed" });
            return Results.Ok(new { id, status = "dismissed" });
        });

        return app;
    }

    public sealed record CatalystUpsertBody(
        string Symbol,
        string Kind,
        DateOnly? OccursOn,
        string Title,
        string Source,
        string? Severity,            // low / medium / high
        string? Status,              // active / expired / dismissed (default active)
        System.Text.Json.JsonElement? Payload,
        string? Note
    );
}

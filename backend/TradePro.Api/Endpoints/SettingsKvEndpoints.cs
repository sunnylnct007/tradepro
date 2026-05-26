using System.Text.Json;
using Dapper;
using Npgsql;

namespace TradePro.Api.Endpoints;

/// <summary>
/// Free-form key-value settings (see <c>db/migrations/011_settings_kv.sql</c>).
/// Complement to the existing single-row AppSettings JSONB blob —
/// this surface lets the UI add / edit operator-tunable knobs
/// without a code change.
///
/// Routes are intentionally small + boring:
///   * GET    /api/settings-kv/             — list (grouped client-side by category)
///   * GET    /api/settings-kv/{key}        — single key
///   * PUT    /api/settings-kv/{key}        — upsert value (does NOT change metadata)
///   * POST   /api/settings-kv/             — admin create (sets full row incl. metadata)
///   * DELETE /api/settings-kv/{key}        — drop the key
///
/// Auth: the user routes inherit the api/Firebase group; only signed-
/// in allow-listed users can write. We don't add a worker-token
/// surface because settings ingestion is human-initiated.
/// </summary>
public static class SettingsKvEndpoints
{
    public static IEndpointRouteBuilder MapSettingsKvEndpoints(this IEndpointRouteBuilder app)
    {
        var group = app.MapGroup("/settings-kv").WithTags("SettingsKv");

        group.MapGet("/", async (NpgsqlDataSource db) =>
        {
            await using var conn = await db.OpenConnectionAsync();
            var rows = await conn.QueryAsync<SettingRow>(@"
                SELECT
                    key            AS Key,
                    value::text    AS ValueJson,
                    value_type     AS ValueType,
                    label          AS Label,
                    description    AS Description,
                    category       AS Category,
                    min_value      AS MinValue,
                    max_value      AS MaxValue,
                    allowed_values::text AS AllowedValuesJson,
                    updated_at_utc AS UpdatedAtUtc,
                    updated_by     AS UpdatedBy
                FROM app_settings_kv
                ORDER BY category, key;");
            return Results.Ok(new { settings = rows.Select(ToDto) });
        });

        group.MapGet("/{key}", async (string key, NpgsqlDataSource db) =>
        {
            await using var conn = await db.OpenConnectionAsync();
            var row = await conn.QueryFirstOrDefaultAsync<SettingRow>(@"
                SELECT
                    key            AS Key,
                    value::text    AS ValueJson,
                    value_type     AS ValueType,
                    label          AS Label,
                    description    AS Description,
                    category       AS Category,
                    min_value      AS MinValue,
                    max_value      AS MaxValue,
                    allowed_values::text AS AllowedValuesJson,
                    updated_at_utc AS UpdatedAtUtc,
                    updated_by     AS UpdatedBy
                FROM app_settings_kv WHERE key = @key;",
                new { key });
            if (row is null) return Results.NotFound(new { error = $"setting '{key}' not found" });
            return Results.Ok(ToDto(row));
        });

        // PUT — value-only update. Metadata (label, description, range)
        // is set via the create POST or migration; PUT keeps the
        // operator's editing surface narrow + boring.
        group.MapPut("/{key}", async (
            string key, JsonElement body, NpgsqlDataSource db, HttpContext ctx) =>
        {
            if (body.ValueKind == JsonValueKind.Undefined)
                return Results.BadRequest(new { error = "body required" });
            var actor = ctx.User?.Identity?.Name ?? "ui";
            await using var conn = await db.OpenConnectionAsync();
            var rows = await conn.ExecuteAsync(@"
                UPDATE app_settings_kv
                SET value          = @value::jsonb,
                    updated_at_utc = NOW(),
                    updated_by     = @actor
                WHERE key = @key;",
                new { key, value = body.GetRawText(), actor });
            if (rows == 0)
                return Results.NotFound(new { error = $"setting '{key}' not found — POST to create with metadata first" });
            return Results.Ok(new { ok = true });
        });

        // POST — admin-only creation with full metadata. Idempotent
        // via ON CONFLICT DO UPDATE on (key) so a migration script's
        // INSERT and a later admin POST both work.
        group.MapPost("/", async (SettingCreatePayload body, NpgsqlDataSource db, HttpContext ctx) =>
        {
            if (string.IsNullOrWhiteSpace(body.Key))
                return Results.BadRequest(new { error = "key required" });
            var actor = ctx.User?.Identity?.Name ?? "ui";
            await using var conn = await db.OpenConnectionAsync();
            await conn.ExecuteAsync(@"
                INSERT INTO app_settings_kv
                    (key, value, value_type, label, description, category,
                     min_value, max_value, allowed_values, updated_by)
                VALUES
                    (@Key, @ValueJson::jsonb, @ValueType, @Label, @Description, @Category,
                     @MinValue, @MaxValue, @AllowedValuesJson::jsonb, @Actor)
                ON CONFLICT (key) DO UPDATE SET
                    value          = EXCLUDED.value,
                    value_type     = EXCLUDED.value_type,
                    label          = EXCLUDED.label,
                    description    = EXCLUDED.description,
                    category       = EXCLUDED.category,
                    min_value      = EXCLUDED.min_value,
                    max_value      = EXCLUDED.max_value,
                    allowed_values = EXCLUDED.allowed_values,
                    updated_at_utc = NOW(),
                    updated_by     = EXCLUDED.updated_by;",
                new {
                    body.Key,
                    ValueJson = body.Value?.GetRawText() ?? "null",
                    ValueType = string.IsNullOrWhiteSpace(body.ValueType) ? "json" : body.ValueType,
                    body.Label,
                    body.Description,
                    Category = string.IsNullOrWhiteSpace(body.Category) ? "General" : body.Category,
                    body.MinValue,
                    body.MaxValue,
                    AllowedValuesJson = body.AllowedValues?.GetRawText(),
                    Actor = actor,
                });
            return Results.Ok(new { ok = true });
        });

        group.MapDelete("/{key}", async (string key, NpgsqlDataSource db) =>
        {
            await using var conn = await db.OpenConnectionAsync();
            var n = await conn.ExecuteAsync(
                "DELETE FROM app_settings_kv WHERE key = @key;", new { key });
            return Results.Ok(new { cleared = n });
        });

        return app;
    }

    private static object ToDto(SettingRow r) => new
    {
        key = r.Key,
        value = ParseOrNull(r.ValueJson),
        valueType = r.ValueType,
        label = r.Label,
        description = r.Description,
        category = r.Category,
        minValue = r.MinValue,
        maxValue = r.MaxValue,
        allowedValues = ParseOrNull(r.AllowedValuesJson),
        updatedAtUtc = r.UpdatedAtUtc,
        updatedBy = r.UpdatedBy,
    };

    // JSON helper: produces a JsonElement when text is parseable,
    // null otherwise. Pulled out of the dto so the conditional has
    // a single return type the compiler can infer.
    private static JsonElement? ParseOrNull(string? text)
    {
        if (string.IsNullOrEmpty(text)) return null;
        try { return JsonDocument.Parse(text).RootElement; }
        catch { return null; }
    }
}

internal sealed record SettingRow(
    string Key,
    string ValueJson,
    string ValueType,
    string? Label,
    string? Description,
    string Category,
    double? MinValue,
    double? MaxValue,
    string? AllowedValuesJson,
    DateTime UpdatedAtUtc,
    string UpdatedBy
);

public sealed record SettingCreatePayload(
    string Key,
    JsonElement? Value,
    string? ValueType,
    string? Label,
    string? Description,
    string? Category,
    double? MinValue,
    double? MaxValue,
    JsonElement? AllowedValues
);

using System.Text.Json;
using Dapper;
using Npgsql;
using TradePro.Api.Models;
using TradePro.Api.Simulation;

namespace TradePro.Api.Data.Stores;

/// <summary>
/// AppSettings persisted as a single JSONB blob. Settings rarely
/// change (a user tweaks thresholds maybe once a week) so we don't
/// bother breaking the record into columns — the JSONB lets us
/// evolve AppSettings without a migration every time we add a field.
/// </summary>
public sealed class PostgresSettingsStore : ISettingsStore
{
    private static readonly JsonSerializerOptions JsonOpts = new()
    {
        WriteIndented = false,
        PropertyNamingPolicy = JsonNamingPolicy.CamelCase,
    };

    private readonly NpgsqlDataSource _db;
    private readonly ILogger<PostgresSettingsStore> _log;

    public PostgresSettingsStore(NpgsqlDataSource db, ILogger<PostgresSettingsStore> log)
    {
        _db = db;
        _log = log;
        EnsureSeeded();
    }

    public AppSettings Get()
    {
        using var conn = _db.OpenConnection();
        var text = conn.QueryFirstOrDefault<string>(
            "SELECT payload::text FROM settings WHERE id = 'singleton'");
        if (text is null) return AppSettingsDefaults.Build();
        try
        {
            return JsonSerializer.Deserialize<AppSettings>(text, JsonOpts)
                ?? AppSettingsDefaults.Build();
        }
        catch (Exception ex)
        {
            _log.LogWarning(ex, "settings row failed to deserialize — falling back to defaults");
            return AppSettingsDefaults.Build();
        }
    }

    public AppSettings Update(AppSettings incoming)
    {
        var stamped = incoming with { UpdatedAtUtc = DateTime.UtcNow };
        var json = JsonSerializer.Serialize(stamped, JsonOpts);
        using var conn = _db.OpenConnection();
        conn.Execute(@"
            INSERT INTO settings (id, payload, updated_at)
            VALUES ('singleton', @payload::jsonb, NOW())
            ON CONFLICT (id) DO UPDATE
                SET payload = EXCLUDED.payload,
                    updated_at = NOW();",
            new { payload = json });
        return stamped;
    }

    private void EnsureSeeded()
    {
        // Seed defaults if the row doesn't exist. Done in the
        // constructor (not the migration) because AppSettingsDefaults
        // changes more often than the schema and we don't want to
        // bake stale defaults into a migration file.
        using var conn = _db.OpenConnection();
        var exists = conn.ExecuteScalar<bool>(
            "SELECT EXISTS(SELECT 1 FROM settings WHERE id = 'singleton')");
        if (exists) return;
        var defaults = AppSettingsDefaults.Build();
        var json = JsonSerializer.Serialize(defaults, JsonOpts);
        conn.Execute(@"
            INSERT INTO settings (id, payload, updated_at)
            VALUES ('singleton', @payload::jsonb, NOW())
            ON CONFLICT (id) DO NOTHING;",
            new { payload = json });
    }
}

using Dapper;
using Microsoft.Extensions.Logging;
using Npgsql;

namespace TradePro.Api.Oms;

/// <summary>
/// Persistent OMS mode store backed by app_settings_kv. Survives
/// container restarts so the operator's chosen mode isn't silently
/// reset to Manual on every redeploy (which was the in-memory impl's
/// failure mode — user kept switching to Auto, every deploy flipped
/// it back, they thought the toggle was broken).
///
/// Key: oms_mode. Value: "Auto" or "Manual" (jsonb string). Mirrors
/// the other settings_kv keys (default_broker, llm_model, etc.) so the
/// /settings page can show + change it through the same plumbing.
///
/// First-read seeds the row to "Manual" so a fresh DB doesn't error
/// when the cockpit fetches the current mode before anyone has set
/// it. Same cancel-on-Auto→Manual contract as the previous impl.
/// </summary>
public sealed class PostgresOmsModeService : IOmsModeService
{
    private readonly NpgsqlDataSource _db;
    private readonly IOmsService _oms;
    private readonly ILogger<PostgresOmsModeService> _log;
    private readonly object _gate = new();
    private OmsMode? _cached;

    public PostgresOmsModeService(
        NpgsqlDataSource db, IOmsService oms,
        ILogger<PostgresOmsModeService> log)
    {
        _db = db;
        _oms = oms;
        _log = log;
    }

    public OmsMode Current
    {
        get
        {
            // Cheap synchronous getter — the rest of the surface area
            // expects this and it gets hit on every order enqueue.
            // We cache after first read; SetAsync invalidates.
            lock (_gate)
            {
                if (_cached is { } c) return c;
            }
            // Cold cache — block briefly on a synchronous read. The
            // alternative (defaulting Manual on miss) is the bug we
            // are trying to fix.
            var loaded = LoadAsync().GetAwaiter().GetResult();
            lock (_gate) { _cached = loaded; }
            return loaded;
        }
    }

    public async Task<OmsMode> SetAsync(OmsMode target, string actor)
    {
        OmsMode prior;
        lock (_gate)
        {
            prior = _cached ?? OmsMode.Manual;
        }

        // Persist BEFORE running the cancel side-effect so a transient
        // failure on cancellation doesn't leave the DB out of sync.
        await using var conn = await _db.OpenConnectionAsync();
        await conn.ExecuteAsync(@"
            INSERT INTO app_settings_kv
                (key, value, value_type, label, description, category)
            VALUES
                ('oms_mode', to_jsonb(@value::text), 'string',
                 'OMS execution mode',
                 'Auto = strategy orders bypass approval and route '
              || 'straight to broker. Manual = every order waits for '
              || 'human/LLM approval on the /oms page.',
                 'OMS')
            ON CONFLICT (key) DO UPDATE
            SET value = to_jsonb(@value::text),
                updated_at_utc = NOW();",
            new { value = target.ToString() });

        lock (_gate) { _cached = target; }

        if (prior == OmsMode.Auto && target == OmsMode.Manual)
        {
            var cancelled = await _oms.CancelAllOpenAsync(actor, "MODE_FLIP_AUTO_TO_MANUAL");
            _log.LogInformation(
                "OMS mode flip Auto→Manual by {actor}: cancelled {count} open order(s) ({ids})",
                actor, cancelled.Count, string.Join(",", cancelled));
        }
        else
        {
            _log.LogInformation("OMS mode persisted as {target} by {actor} (prior: {prior})",
                target, actor, prior);
        }
        return target;
    }

    private async Task<OmsMode> LoadAsync()
    {
        try
        {
            await using var conn = await _db.OpenConnectionAsync();
            var raw = await conn.ExecuteScalarAsync<string?>(@"
                SELECT trim(both '""' from value::text)
                FROM app_settings_kv WHERE key = 'oms_mode';");
            if (string.IsNullOrWhiteSpace(raw)) return OmsMode.Manual;
            if (Enum.TryParse<OmsMode>(raw, ignoreCase: true, out var m)) return m;
            return OmsMode.Manual;
        }
        catch (Exception ex)
        {
            _log.LogWarning(ex, "OMS mode load failed — defaulting to Manual");
            return OmsMode.Manual;
        }
    }
}

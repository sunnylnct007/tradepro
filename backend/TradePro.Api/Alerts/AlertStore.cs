using System.Text.Json;
using Dapper;
using Npgsql;

namespace TradePro.Api.Alerts;

/// <summary>One operational alert as stored / returned to the UI.</summary>
public sealed record AlertRow(
    Guid Id,
    string Source,
    string Severity,
    string Code,
    string Title,
    string Detail,
    string? StrategyId,
    string? Broker,
    string[] Symbols,
    string? DedupKey,
    int Occurrences,
    DateTime FirstSeenUtc,
    DateTime LastSeenUtc,
    DateTime? ResolvedAtUtc,
    string? ResolvedBy);

/// <summary>Caller-supplied fields when raising an alert. Anything not
/// set falls back to a sensible default in the store.</summary>
public sealed record AlertInput(
    string Source,
    string Severity,
    string Code,
    string Title,
    string Detail,
    string? StrategyId,
    string? Broker,
    string[] Symbols,
    string? DedupKey);

public interface IAlertStore
{
    /// Raise an alert. If an OPEN alert with the same dedup_key exists it
    /// is refreshed (occurrences++, last_seen bumped) and its id returned;
    /// otherwise a new row is inserted. Returns the alert id.
    Guid Raise(AlertInput input);

    /// Active (unresolved) alerts, newest activity first.
    IReadOnlyList<AlertRow> ListActive(int limit = 50);

    /// Mark an alert resolved. Returns false if it didn't exist / was
    /// already resolved.
    bool Resolve(Guid id, string resolvedBy);
}

/// <summary>
/// Postgres-backed alert store (table: system_alerts, migration 027).
/// Dedup is two-step (UPDATE open row by dedup_key, else INSERT) so we
/// don't depend on ON CONFLICT inference over a partial index; the
/// partial unique index still guards against a concurrent double-insert,
/// which we catch and fold into the update.
/// </summary>
public sealed class PostgresAlertStore : IAlertStore
{
    private const string UniqueViolation = "23505";
    private readonly NpgsqlDataSource _db;

    public PostgresAlertStore(NpgsqlDataSource db) => _db = db;

    public Guid Raise(AlertInput input)
    {
        var severity = input.Severity is "info" or "warn" or "critical"
            ? input.Severity : "warn";
        var symbolsJson = JsonSerializer.Serialize(input.Symbols ?? Array.Empty<string>());
        var p = new
        {
            input.Source,
            Severity = severity,
            input.Code,
            input.Title,
            input.Detail,
            input.StrategyId,
            input.Broker,
            Symbols = symbolsJson,
            input.DedupKey,
        };

        using var conn = _db.OpenConnection();

        if (!string.IsNullOrWhiteSpace(input.DedupKey))
        {
            var existing = RefreshOpen(conn, p);
            if (existing.HasValue) return existing.Value;
        }

        try
        {
            return Insert(conn, p);
        }
        catch (PostgresException ex) when (ex.SqlState == UniqueViolation
                                           && !string.IsNullOrWhiteSpace(input.DedupKey))
        {
            // Lost a race to another producer with the same dedup_key —
            // fold into the now-existing open row.
            var existing = RefreshOpen(conn, p);
            if (existing.HasValue) return existing.Value;
            throw;
        }
    }

    private static Guid? RefreshOpen(NpgsqlConnection conn, object p) =>
        conn.QueryFirstOrDefault<Guid?>(@"
            UPDATE system_alerts
               SET occurrences   = occurrences + 1,
                   last_seen_utc = NOW(),
                   severity      = @Severity,
                   title         = @Title,
                   detail        = @Detail,
                   symbols       = @Symbols::jsonb,
                   source        = @Source,
                   code          = @Code,
                   strategy_id   = @StrategyId,
                   broker        = @Broker
             WHERE dedup_key = @DedupKey
               AND resolved_at_utc IS NULL
         RETURNING id;", p);

    private static Guid Insert(NpgsqlConnection conn, object p) =>
        conn.QuerySingle<Guid>(@"
            INSERT INTO system_alerts
                (source, severity, code, title, detail,
                 strategy_id, broker, symbols, dedup_key)
            VALUES
                (@Source, @Severity, @Code, @Title, @Detail,
                 @StrategyId, @Broker, @Symbols::jsonb, @DedupKey)
            RETURNING id;", p);

    public IReadOnlyList<AlertRow> ListActive(int limit = 50)
    {
        using var conn = _db.OpenConnection();
        var rows = conn.Query<(Guid Id, string Source, string Severity, string Code,
            string Title, string Detail, string? StrategyId, string? Broker,
            string Symbols, string? DedupKey, int Occurrences, DateTime FirstSeenUtc,
            DateTime LastSeenUtc, DateTime? ResolvedAtUtc, string? ResolvedBy)>(@"
            SELECT id, source, severity, code, title, detail,
                   strategy_id AS StrategyId, broker,
                   symbols::text AS Symbols, dedup_key AS DedupKey,
                   occurrences, first_seen_utc AS FirstSeenUtc,
                   last_seen_utc AS LastSeenUtc, resolved_at_utc AS ResolvedAtUtc,
                   resolved_by AS ResolvedBy
              FROM system_alerts
             WHERE resolved_at_utc IS NULL
             ORDER BY (severity = 'critical') DESC, last_seen_utc DESC
             LIMIT @limit;", new { limit });

        return rows.Select(r => new AlertRow(
            r.Id, r.Source, r.Severity, r.Code, r.Title, r.Detail,
            r.StrategyId, r.Broker, ParseSymbols(r.Symbols), r.DedupKey,
            r.Occurrences, r.FirstSeenUtc, r.LastSeenUtc, r.ResolvedAtUtc,
            r.ResolvedBy)).ToList();
    }

    public bool Resolve(Guid id, string resolvedBy)
    {
        using var conn = _db.OpenConnection();
        var n = conn.Execute(@"
            UPDATE system_alerts
               SET resolved_at_utc = NOW(), resolved_by = @resolvedBy
             WHERE id = @id AND resolved_at_utc IS NULL;",
            new { id, resolvedBy });
        return n > 0;
    }

    private static string[] ParseSymbols(string? json)
    {
        if (string.IsNullOrWhiteSpace(json)) return Array.Empty<string>();
        try
        {
            return JsonSerializer.Deserialize<string[]>(json) ?? Array.Empty<string>();
        }
        catch (JsonException)
        {
            return Array.Empty<string>();
        }
    }
}

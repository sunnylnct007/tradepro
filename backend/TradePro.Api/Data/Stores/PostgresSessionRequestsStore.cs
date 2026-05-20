using System.Text.Json;
using Dapper;
using Npgsql;
using TradePro.Api.Simulation;

namespace TradePro.Api.Data.Stores;

/// <summary>
/// Postgres-backed ops trigger queue. Claim is atomic via
/// UPDATE ... RETURNING with a CTE so two pollers can't pick the
/// same row. Terminal rows (Completed / Failed / Cancelled) stay
/// for audit until the table exceeds <c>MaxRows</c>, at which point
/// the oldest terminal row is evicted (Pending rows are never
/// evicted — losing one means losing user intent).
/// </summary>
public sealed class PostgresSessionRequestsStore : ISessionRequestsStore
{
    private const int MaxRows = 500;
    private readonly NpgsqlDataSource _db;

    public PostgresSessionRequestsStore(NpgsqlDataSource db) { _db = db; }

    public SessionRequest Put(string kind, JsonElement? params_)
    {
        var requestId = Guid.NewGuid().ToString("N");
        var paramsJson = params_.HasValue
            ? JsonbHelpers.ToJsonb(params_.Value)
            : "{}";

        using var conn = _db.OpenConnection();
        using var tx = conn.BeginTransaction();
        conn.Execute(@"
            INSERT INTO session_requests (request_id, kind, params)
            VALUES (@requestId, @kind, @paramsJson::jsonb);",
            new { requestId, kind, paramsJson },
            transaction: tx);
        EvictIfFull(conn, tx);
        tx.Commit();
        return ReadOne(conn, requestId)!;
    }

    public SessionRequest? Get(string requestId)
    {
        using var conn = _db.OpenConnection();
        return ReadOne(conn, requestId);
    }

    public SessionRequest? Claim(string kind, string claimedBy)
    {
        // FOR UPDATE SKIP LOCKED gives concurrent pollers wait-free
        // contention: two workers polling the same instant each get
        // a different Pending row (or null) instead of blocking.
        using var conn = _db.OpenConnection();
        var row = conn.QueryFirstOrDefault<SessionRequestRow>(@"
            WITH next AS (
                SELECT request_id FROM session_requests
                WHERE kind = @kind AND state = 'Pending'
                ORDER BY requested_at_utc ASC
                LIMIT 1
                FOR UPDATE SKIP LOCKED
            )
            UPDATE session_requests s
            SET state = 'Claimed',
                claimed_at_utc = NOW(),
                claimed_by = @claimedBy
            FROM next
            WHERE s.request_id = next.request_id
            RETURNING s.request_id, s.kind, s.params::text AS params_text, s.state,
                     s.requested_at_utc, s.claimed_at_utc, s.claimed_by,
                     s.completed_at_utc, s.result_summary::text AS result_summary_text, s.error;",
            new { kind, claimedBy });
        return row is null ? null : ToRequest(row);
    }

    public SessionRequest? MarkCompleted(string requestId, JsonElement? resultSummary)
    {
        var summaryJson = resultSummary.HasValue
            ? JsonbHelpers.ToJsonb(resultSummary.Value)
            : null;
        using var conn = _db.OpenConnection();
        conn.Execute(@"
            UPDATE session_requests SET
                state = 'Completed',
                completed_at_utc = NOW(),
                result_summary = COALESCE(@summaryJson::jsonb, result_summary),
                error = NULL
            WHERE request_id = @requestId;",
            new { requestId, summaryJson });
        return ReadOne(conn, requestId);
    }

    public SessionRequest? MarkFailed(string requestId, string error)
    {
        using var conn = _db.OpenConnection();
        conn.Execute(@"
            UPDATE session_requests SET
                state = 'Failed',
                completed_at_utc = NOW(),
                error = @error
            WHERE request_id = @requestId;",
            new { requestId, error });
        return ReadOne(conn, requestId);
    }

    public SessionRequest? Cancel(string requestId)
    {
        // Only Pending rows can be cancelled — once Claimed the work
        // is in flight on the Mac and cancel is moot.
        using var conn = _db.OpenConnection();
        conn.Execute(@"
            UPDATE session_requests SET state = 'Cancelled', completed_at_utc = NOW()
            WHERE request_id = @requestId AND state = 'Pending';",
            new { requestId });
        return ReadOne(conn, requestId);
    }

    public IReadOnlyList<SessionRequest> List(string? kind, int limit = 100)
    {
        using var conn = _db.OpenConnection();
        var rows = conn.Query<SessionRequestRow>(@"
            SELECT request_id, kind, params::text AS params_text, state,
                   requested_at_utc, claimed_at_utc, claimed_by,
                   completed_at_utc, result_summary::text AS result_summary_text, error
            FROM session_requests
            WHERE (@kind IS NULL OR kind = @kind)
            ORDER BY (CASE WHEN state = 'Pending' THEN 0
                           WHEN state = 'Claimed' THEN 1 ELSE 2 END),
                     requested_at_utc DESC
            LIMIT @limit;",
            new { kind, limit }).ToList();
        return rows.Select(ToRequest).ToArray();
    }

    private static SessionRequest? ReadOne(NpgsqlConnection conn, string requestId)
    {
        var row = conn.QueryFirstOrDefault<SessionRequestRow>(@"
            SELECT request_id, kind, params::text AS params_text, state,
                   requested_at_utc, claimed_at_utc, claimed_by,
                   completed_at_utc, result_summary::text AS result_summary_text, error
            FROM session_requests WHERE request_id = @requestId;",
            new { requestId });
        return row is null ? null : ToRequest(row);
    }

    private static void EvictIfFull(NpgsqlConnection conn, NpgsqlTransaction tx)
    {
        conn.Execute(@"
            DELETE FROM session_requests WHERE request_id IN (
                SELECT request_id FROM session_requests
                WHERE state IN ('Completed', 'Failed', 'Cancelled')
                ORDER BY COALESCE(completed_at_utc, requested_at_utc) ASC
                OFFSET @keep
            );",
            new { keep = MaxRows }, transaction: tx);
    }

    private static SessionRequest ToRequest(SessionRequestRow r) => new(
        RequestId: r.request_id,
        Kind: r.kind,
        Params: JsonbHelpers.FromJsonb(r.params_text ?? "{}"),
        State: Enum.Parse<SessionRequestState>(r.state),
        RequestedAtUtc: r.requested_at_utc,
        ClaimedAtUtc: r.claimed_at_utc,
        ClaimedBy: r.claimed_by,
        CompletedAtUtc: r.completed_at_utc,
        ResultSummary: string.IsNullOrEmpty(r.result_summary_text)
            ? (JsonElement?)null
            : JsonbHelpers.FromJsonb(r.result_summary_text),
        Error: r.error);

    private sealed record SessionRequestRow(
        string request_id, string kind, string? params_text, string state,
        DateTime requested_at_utc, DateTime? claimed_at_utc, string? claimed_by,
        DateTime? completed_at_utc, string? result_summary_text, string? error);
}

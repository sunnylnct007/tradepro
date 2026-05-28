using System.Runtime.CompilerServices;
using Dapper;
using Npgsql;
using TradePro.Api.Data.Stores;

namespace TradePro.Api.Data;

/// <summary>
/// Long-lived async iterator that yields <see cref="EventRecord"/>
/// rows the moment they're inserted, using Postgres LISTEN/NOTIFY.
///
/// Each call opens its own dedicated <see cref="NpgsqlConnection"/>
/// — LISTEN attaches to the connection, so a pooled connection would
/// either leak the listener back into the pool or fail to receive
/// notifications fired on a different connection. Dedicated
/// connection lifetime is tied to the iteration; the connection
/// closes when the caller stops iterating (or the CancellationToken
/// fires).
///
/// Behaviour:
/// <list type="number">
///   <item>If <c>sinceSeq</c> is provided, emit every event with
///   <c>seq &gt; sinceSeq</c> first (catch-up so a client that
///   reconnected doesn't miss anything).</item>
///   <item>Then enter LISTEN mode and yield each new row as its
///   NOTIFY arrives.</item>
///   <item>A periodic empty yield (returns <c>null</c>) lets the
///   SSE handler send a keepalive comment so reverse proxies
///   don't close the connection mid-stream.</item>
/// </list>
/// </summary>
public sealed class EventStream
{
    private readonly NpgsqlDataSource _db;
    private readonly ILogger<EventStream> _log;

    public EventStream(NpgsqlDataSource db, ILogger<EventStream> log)
    {
        _db = db;
        _log = log;
    }

    /// <summary>Stream events. <c>null</c> values in the sequence are
    /// keepalive ticks — the SSE handler should send a comment frame.</summary>
    public async IAsyncEnumerable<EventRecord?> StreamAsync(
        long? sinceSeq,
        string? eventTypeFilter,
        [EnumeratorCancellation] CancellationToken ct)
    {
        // Catch-up phase. Pooled connection, normal Dapper.
        if (sinceSeq.HasValue)
        {
            await using var catchupConn = await _db.OpenConnectionAsync(ct);
            var catchupSql = @"
                SELECT seq, event_type AS EventType, aggregate_id AS AggregateId,
                       payload::text AS PayloadText, occurred_at AS OccurredAt
                FROM events
                WHERE seq > @sinceSeq
                  AND (@eventType IS NULL OR event_type = @eventType)
                ORDER BY seq ASC;";
            var rows = await catchupConn.QueryAsync<EventRow>(
                catchupSql, new { sinceSeq = sinceSeq.Value, eventType = eventTypeFilter });
            foreach (var r in rows)
            {
                ct.ThrowIfCancellationRequested();
                yield return ToRecord(r);
                sinceSeq = r.seq;
            }
        }

        // LISTEN phase. Dedicated connection.
        await using var listenConn = await _db.OpenConnectionAsync(ct);
        await using (var listenCmd = new NpgsqlCommand("LISTEN tradepro_events", listenConn))
        {
            await listenCmd.ExecuteNonQueryAsync(ct);
        }

        // Channel-buffered queue of incoming notification seqs.
        // Using a simple queue + semaphore because the alternatives
        // (Channel<T>, BlockingCollection) bring more ceremony for
        // the same shape.
        var queue = new System.Collections.Concurrent.ConcurrentQueue<long>();
        var signal = new SemaphoreSlim(0, int.MaxValue);

        void OnNotification(object _, NpgsqlNotificationEventArgs args)
        {
            if (long.TryParse(args.Payload, out var seq))
            {
                queue.Enqueue(seq);
                signal.Release();
            }
        }
        listenConn.Notification += OnNotification;
        try
        {
            // Background wait so Npgsql actually delivers notifications.
            // Without WaitAsync the driver only checks the wire on
            // explicit command boundaries.
            _ = Task.Run(async () =>
            {
                try
                {
                    while (!ct.IsCancellationRequested)
                    {
                        await listenConn.WaitAsync(TimeSpan.FromSeconds(15), ct);
                    }
                }
                catch (Exception ex) when (ex is OperationCanceledException or TaskCanceledException)
                {
                    // Normal shutdown.
                }
                catch (Exception ex)
                {
                    _log.LogWarning(ex, "Npgsql.WaitAsync errored — listener stopping");
                }
            }, ct);

            while (!ct.IsCancellationRequested)
            {
                // Wait at most 20s for a new event so we can emit a
                // keepalive even when the system is idle. SSE clients
                // and reverse proxies usually close the connection at
                // 30-60s of silence.
                var got = await signal.WaitAsync(TimeSpan.FromSeconds(20), ct);
                if (!got)
                {
                    yield return null; // keepalive tick
                    continue;
                }
                while (queue.TryDequeue(out var seq))
                {
                    ct.ThrowIfCancellationRequested();
                    var row = await FetchOne(listenConn, seq, ct);
                    if (row is null) continue;
                    if (eventTypeFilter is not null && row.EventType != eventTypeFilter)
                        continue;
                    yield return row;
                }
            }
        }
        finally
        {
            listenConn.Notification -= OnNotification;
        }
    }

    private static async Task<EventRecord?> FetchOne(NpgsqlConnection conn, long seq, CancellationToken ct)
    {
        var row = await conn.QueryFirstOrDefaultAsync<EventRow>(@"
            SELECT seq, event_type AS EventType, aggregate_id AS AggregateId,
                   payload::text AS PayloadText, occurred_at AS OccurredAt
            FROM events WHERE seq = @seq;",
            new { seq });
        return row is null ? null : ToRecord(row);
    }

    private static EventRecord ToRecord(EventRow r) => new(
        Seq: r.seq,
        EventType: r.EventType,
        AggregateId: r.AggregateId,
        Payload: JsonbHelpers.FromJsonb(r.PayloadText),
        OccurredAt: r.OccurredAt);

    private sealed record EventRow(
        long seq, string EventType, string? AggregateId,
        string PayloadText, DateTime OccurredAt);
}

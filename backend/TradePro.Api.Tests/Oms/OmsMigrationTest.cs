using Dapper;
using TradePro.Api.Tests.Infrastructure;
using Xunit;

namespace TradePro.Api.Tests.Oms;

/// <summary>
/// Pins migration 009: the three OMS tables exist with the columns
/// the service layer expects, the state CHECK constraints reject
/// unknown values, and the partial index lives. Fails fast on a
/// schema drift before the OmsService is even instantiated.
/// </summary>
[Collection("postgres")]
public sealed class OmsMigrationTest
{
    private readonly PostgresFixture _fx;
    public OmsMigrationTest(PostgresFixture fx) => _fx = fx;

    [Fact]
    public async Task Oms_tables_exist()
    {
        await using var conn = await _fx.Db.OpenConnectionAsync();
        var tables = (await conn.QueryAsync<string>(@"
            SELECT table_name FROM information_schema.tables
            WHERE table_schema = 'public'
              AND table_name IN ('oms_orders', 'oms_order_events', 'oms_fills');"))
            .ToList();
        Assert.Equal(3, tables.Count);
    }

    [Fact]
    public async Task Oms_orders_state_check_rejects_garbage()
    {
        await using var conn = await _fx.Db.OpenConnectionAsync();
        var ex = await Record.ExceptionAsync(async () =>
        {
            await conn.ExecuteAsync(@"
                INSERT INTO oms_orders
                  (client_order_id, broker, symbol, side, qty, order_type, state)
                VALUES
                  (gen_random_uuid(), 'PAPER', 'AAPL', 'BUY', 1, 'MKT', 'WHATEVER');");
        });
        Assert.NotNull(ex);
        Assert.Contains("oms_orders_state_check", ex!.Message);
    }

    [Fact]
    public async Task Oms_orders_default_state_is_pending_approval()
    {
        await using var conn = await _fx.Db.OpenConnectionAsync();
        var clientId = Guid.NewGuid();
        await conn.ExecuteAsync(@"
            INSERT INTO oms_orders
              (client_order_id, broker, symbol, side, qty, order_type)
            VALUES
              (@cid, 'PAPER', 'AAPL', 'BUY', 1, 'MKT');",
            new { cid = clientId });
        var state = await conn.ExecuteScalarAsync<string>(
            "SELECT state FROM oms_orders WHERE client_order_id = @cid;",
            new { cid = clientId });
        Assert.Equal("PENDING_APPROVAL", state);
    }

    [Fact]
    public async Task Open_state_partial_index_present()
    {
        await using var conn = await _fx.Db.OpenConnectionAsync();
        var idx = await conn.ExecuteScalarAsync<int>(@"
            SELECT COUNT(*) FROM pg_indexes
            WHERE schemaname = 'public'
              AND tablename = 'oms_orders'
              AND indexname = 'oms_orders_open_state_idx';");
        Assert.Equal(1, idx);
    }

    [Fact]
    public async Task Order_event_cascades_on_order_delete()
    {
        await using var conn = await _fx.Db.OpenConnectionAsync();
        var clientId = Guid.NewGuid();
        var orderId = await conn.ExecuteScalarAsync<Guid>(@"
            INSERT INTO oms_orders
              (client_order_id, broker, symbol, side, qty, order_type)
            VALUES
              (@cid, 'PAPER', 'AAPL', 'BUY', 1, 'MKT')
            RETURNING id;",
            new { cid = clientId });
        await conn.ExecuteAsync(@"
            INSERT INTO oms_order_events (order_id, event_type, new_state, actor)
            VALUES (@oid, 'ENQUEUED', 'PENDING_APPROVAL', 'test');",
            new { oid = orderId });

        var eventsBefore = await conn.ExecuteScalarAsync<int>(
            "SELECT COUNT(*) FROM oms_order_events WHERE order_id = @oid;",
            new { oid = orderId });
        Assert.Equal(1, eventsBefore);

        await conn.ExecuteAsync(
            "DELETE FROM oms_orders WHERE id = @oid;",
            new { oid = orderId });

        var eventsAfter = await conn.ExecuteScalarAsync<int>(
            "SELECT COUNT(*) FROM oms_order_events WHERE order_id = @oid;",
            new { oid = orderId });
        Assert.Equal(0, eventsAfter);
    }
}

using Dapper;
using Microsoft.Extensions.Logging.Abstractions;
using TradePro.Api.Oms;
using TradePro.Api.Tests.Infrastructure;
using Xunit;

namespace TradePro.Api.Tests.Oms;

/// <summary>
/// State-machine tests for PostgresOmsService + InMemoryOmsModeService.
/// Every transition runs against a real ephemeral Postgres via the
/// shared PostgresFixture so a CHECK-constraint regression in the
/// migration file fails here, not in production.
/// </summary>
[Collection("postgres")]
public sealed class OmsServiceTest
{
    private readonly PostgresFixture _fx;
    public OmsServiceTest(PostgresFixture fx)
    {
        _fx = fx;
        // Shared fixture across the OMS test classes — wipe the OMS
        // tables before each test so CancelAllOpen-style assertions
        // see a deterministic state. CASCADE drops dependent events
        // + fills via the FK. The other (paper_*, session_*) tables
        // stay intact so the cleanup is OMS-scoped only.
        using var conn = _fx.Db.OpenConnection();
        conn.Execute("TRUNCATE oms_orders CASCADE;");
    }

    private PostgresOmsService NewService() => new(_fx.Db);

    private static OrderIntent SampleIntent(string symbol = "AAPL") =>
        new(
            ClientOrderId: Guid.NewGuid(),
            Broker: "PAPER",
            Symbol: symbol,
            Side: "BUY",
            Qty: 10m,
            OrderType: "MKT",
            StrategyId: "ichimoku_fx_mr");

    [Fact]
    public async Task Enqueue_creates_PENDING_APPROVAL_row_and_event()
    {
        var oms = NewService();
        var intent = SampleIntent();
        var row = await oms.EnqueueAsync(intent, "test");

        Assert.Equal(OmsState.PendingApproval, row.State);
        Assert.Equal(intent.ClientOrderId, row.ClientOrderId);
        Assert.Equal(intent.Symbol, row.Symbol);
        Assert.Equal(intent.Qty, row.Qty);

        var events = await oms.ListEventsAsync(row.Id);
        Assert.Single(events);
        Assert.Equal("ENQUEUED", events[0].EventType);
        Assert.Null(events[0].PriorState);
        Assert.Equal(OmsState.PendingApproval, events[0].NewState);
    }

    [Fact]
    public async Task Enqueue_is_idempotent_on_client_order_id()
    {
        var oms = NewService();
        var intent = SampleIntent();
        var first = await oms.EnqueueAsync(intent, "test");
        var second = await oms.EnqueueAsync(intent, "test");
        Assert.Equal(first.Id, second.Id);

        // One row only, one event only — replay didn't duplicate.
        var events = await oms.ListEventsAsync(first.Id);
        Assert.Single(events);
    }

    [Fact]
    public async Task Approve_moves_pending_to_submitted()
    {
        var oms = NewService();
        var row = await oms.EnqueueAsync(SampleIntent(), "test");
        var approved = await oms.ApproveAsync(row.Id, "operator");
        Assert.Equal(OmsState.Submitted, approved.State);

        var events = await oms.ListEventsAsync(row.Id);
        Assert.Equal(new[] { "ENQUEUED", "APPROVED" }, events.Select(e => e.EventType));
        Assert.Equal(OmsState.PendingApproval, events[1].PriorState);
        Assert.Equal("operator", events[1].Actor);
    }

    [Fact]
    public async Task Reject_moves_pending_to_rejected()
    {
        var oms = NewService();
        var row = await oms.EnqueueAsync(SampleIntent(), "test");
        var rejected = await oms.RejectAsync(row.Id, "operator", "too risky");
        Assert.Equal(OmsState.Rejected, rejected.State);

        var events = await oms.ListEventsAsync(row.Id);
        Assert.Equal("REJECTED", events.Last().EventType);
    }

    [Fact]
    public async Task Cannot_approve_a_rejected_order()
    {
        var oms = NewService();
        var row = await oms.EnqueueAsync(SampleIntent(), "test");
        await oms.RejectAsync(row.Id, "operator", "nope");

        var ex = await Assert.ThrowsAsync<InvalidOperationException>(
            () => oms.ApproveAsync(row.Id, "operator"));
        Assert.Contains("cannot approve", ex.Message);
    }

    [Fact]
    public async Task Cancel_records_reason_and_transitions_to_cancelled()
    {
        var oms = NewService();
        var row = await oms.EnqueueAsync(SampleIntent(), "test");
        await oms.ApproveAsync(row.Id, "operator");
        var cancelled = await oms.CancelAsync(row.Id, "operator", "USER_KILL");

        Assert.Equal(OmsState.Cancelled, cancelled.State);
        Assert.Equal("USER_KILL", cancelled.CancelledReason);
    }

    [Fact]
    public async Task RecordFill_partial_then_full_transitions_correctly()
    {
        var oms = NewService();
        var row = await oms.EnqueueAsync(SampleIntent(), "test");
        await oms.ApproveAsync(row.Id, "operator");

        var afterPartial = await oms.RecordFillAsync(
            row.Id, qty: 4m, price: 100m, fee: 0.50m, currency: "USD",
            brokerFillId: "f1", actor: "broker");
        Assert.Equal(OmsState.PartiallyFilled, afterPartial.State);
        Assert.Equal(4m, afterPartial.FilledQty);
        Assert.Equal(100m, afterPartial.AvgFillPrice);

        var afterFull = await oms.RecordFillAsync(
            row.Id, qty: 6m, price: 102m, fee: 0.75m, currency: "USD",
            brokerFillId: "f2", actor: "broker");
        Assert.Equal(OmsState.Filled, afterFull.State);
        Assert.Equal(10m, afterFull.FilledQty);
        // Weighted: (100*4 + 102*6) / 10 = 101.2
        Assert.Equal(101.2m, afterFull.AvgFillPrice);
    }

    [Fact]
    public async Task CancelAllOpen_only_touches_open_state_orders()
    {
        var oms = NewService();
        var openA = await oms.EnqueueAsync(SampleIntent("AAPL"), "test");
        var openB = await oms.EnqueueAsync(SampleIntent("MSFT"), "test");
        var rejected = await oms.EnqueueAsync(SampleIntent("NVDA"), "test");
        await oms.RejectAsync(rejected.Id, "operator", "won't trade");

        var cancelledIds = await oms.CancelAllOpenAsync("system", "MODE_FLIP");
        Assert.Equal(2, cancelledIds.Count);
        Assert.Contains(openA.Id, cancelledIds);
        Assert.Contains(openB.Id, cancelledIds);
        Assert.DoesNotContain(rejected.Id, cancelledIds);

        var afterA = await oms.GetAsync(openA.Id);
        Assert.Equal(OmsState.Cancelled, afterA!.State);
    }

    [Fact]
    public async Task List_filters_by_state()
    {
        var oms = NewService();
        var pending = await oms.EnqueueAsync(SampleIntent("AAPL"), "test");
        var submitted = await oms.EnqueueAsync(SampleIntent("MSFT"), "test");
        await oms.ApproveAsync(submitted.Id, "op");

        var allPending = await oms.ListAsync(new[] { OmsState.PendingApproval }, 100);
        Assert.Contains(allPending, o => o.Id == pending.Id);
        Assert.DoesNotContain(allPending, o => o.Id == submitted.Id);
    }

    [Fact]
    public async Task ModeService_AutoToManual_cancels_open_orders()
    {
        var oms = NewService();
        var mode = new InMemoryOmsModeService(oms, NullLogger<InMemoryOmsModeService>.Instance);

        // Start by entering Auto so the next flip is the "real" one.
        await mode.SetAsync(OmsMode.Auto, "test");

        var open = await oms.EnqueueAsync(SampleIntent("AAPL"), "strategy");

        await mode.SetAsync(OmsMode.Manual, "test");
        Assert.Equal(OmsMode.Manual, mode.Current);

        var after = await oms.GetAsync(open.Id);
        Assert.Equal(OmsState.Cancelled, after!.State);
        Assert.Equal("MODE_FLIP_AUTO_TO_MANUAL", after.CancelledReason);
    }

    [Fact]
    public async Task ModeService_ManualToAuto_leaves_orders_alone()
    {
        var oms = NewService();
        var mode = new InMemoryOmsModeService(oms, NullLogger<InMemoryOmsModeService>.Instance);

        var open = await oms.EnqueueAsync(SampleIntent("AAPL"), "operator");
        await mode.SetAsync(OmsMode.Auto, "test");
        Assert.Equal(OmsMode.Auto, mode.Current);

        var after = await oms.GetAsync(open.Id);
        Assert.Equal(OmsState.PendingApproval, after!.State);
    }
}

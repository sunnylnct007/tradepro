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
    public async Task Enqueue_after_a_dead_attempt_places_a_FRESH_order_not_the_corpse()
    {
        // Regression: equity stuck at 0 fills because its client_order_id is a
        // deterministic daily hash. Pre-open orders died (market_closed →
        // stale-swept), then every post-open re-emit deduped to the CANCELLED
        // corpse (which can never fill). A re-emit after a terminal attempt
        // must place a brand-new, live order.
        var oms = NewService();
        var intent = SampleIntent();
        var first = await oms.EnqueueAsync(intent, "test");
        await oms.CancelAsync(first.Id, "risk_monitor", "stale_pending_auto_clean");

        var second = await oms.EnqueueAsync(intent, "test");   // same client_order_id
        Assert.NotEqual(first.Id, second.Id);                  // NOT the corpse
        Assert.Equal(OmsState.PendingApproval, second.State);  // a live, placeable order
    }

    [Fact]
    public async Task Enqueue_still_dedupes_to_a_FILLED_order_no_double_fill()
    {
        // The flip side: a FILLED order with this client_id IS a true
        // duplicate — re-emitting must return it, never place a second fill.
        var oms = NewService();
        var intent = SampleIntent();
        var first = await oms.EnqueueAsync(intent, "test");
        await oms.ApproveAsync(first.Id, "operator");
        await oms.RecordFillAsync(first.Id, intent.Qty, 100m, 0m, "USD", "fill-1", "broker");

        var second = await oms.EnqueueAsync(intent, "test");
        Assert.Equal(first.Id, second.Id);                     // same order, no double-fill
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

    // ── Phase F-2: L1 snapshot capture on RecordFillAsync ─────────

    [Fact]
    public async Task RecordFill_with_snapshot_persists_bid_ask_mid()
    {
        var oms = NewService();
        var row = await oms.EnqueueAsync(SampleIntent(), "test");
        await oms.ApproveAsync(row.Id, "operator");

        var snap = new FillSnapshot(
            Bid: 99.95m, Ask: 100.05m,
            SnapshotAtUtc: DateTime.UtcNow,
            Source: "ig_markets");
        await oms.RecordFillAsync(
            row.Id, qty: 10m, price: 100m, fee: 0m, currency: "USD",
            brokerFillId: "f-snap", actor: "broker", snapshot: snap);

        await using var conn = await _fx.Db.OpenConnectionAsync();
        var (bid, ask, mid, source) = await conn.QuerySingleAsync<(
            decimal? bid, decimal? ask, decimal? mid, string? source)>(@"
            SELECT bid_at_fill, ask_at_fill, mid_at_fill, snapshot_source
            FROM oms_fills WHERE broker_fill_id = 'f-snap';");
        Assert.Equal(99.95m, bid);
        Assert.Equal(100.05m, ask);
        Assert.Equal(100.00m, mid);          // (99.95 + 100.05) / 2
        Assert.Equal("ig_markets", source);
    }

    [Fact]
    public async Task RecordFill_without_snapshot_leaves_l1_columns_null()
    {
        var oms = NewService();
        var row = await oms.EnqueueAsync(SampleIntent(), "test");
        await oms.ApproveAsync(row.Id, "operator");

        await oms.RecordFillAsync(
            row.Id, qty: 10m, price: 100m, fee: 0m, currency: "USD",
            brokerFillId: "f-nosnap", actor: "broker");

        await using var conn = await _fx.Db.OpenConnectionAsync();
        var (bid, ask, mid) = await conn.QuerySingleAsync<(
            decimal? bid, decimal? ask, decimal? mid)>(@"
            SELECT bid_at_fill, ask_at_fill, mid_at_fill
            FROM oms_fills WHERE broker_fill_id = 'f-nosnap';");
        Assert.Null(bid);
        Assert.Null(ask);
        Assert.Null(mid);
    }

    [Fact]
    public async Task RecordFill_partial_with_snapshot_only_stamps_that_chunk()
    {
        var oms = NewService();
        var row = await oms.EnqueueAsync(SampleIntent(), "test");
        await oms.ApproveAsync(row.Id, "operator");

        // First chunk: no snapshot. Second chunk: snapshot. Both rows
        // exist on oms_fills; only the second carries L1.
        await oms.RecordFillAsync(
            row.Id, qty: 4m, price: 100m, fee: 0m, currency: "USD",
            brokerFillId: "f-partial-1", actor: "broker");
        var snap = new FillSnapshot(
            Bid: 102m, Ask: 102.1m,
            SnapshotAtUtc: DateTime.UtcNow,
            Source: "ig_markets");
        await oms.RecordFillAsync(
            row.Id, qty: 6m, price: 102m, fee: 0m, currency: "USD",
            brokerFillId: "f-partial-2", actor: "broker", snapshot: snap);

        await using var conn = await _fx.Db.OpenConnectionAsync();
        var rows = (await conn.QueryAsync<(string brokerFillId, decimal? mid)>(@"
            SELECT broker_fill_id, mid_at_fill
            FROM oms_fills
            WHERE broker_fill_id IN ('f-partial-1', 'f-partial-2')
            ORDER BY broker_fill_id;")).AsList();
        Assert.Equal(2, rows.Count);
        Assert.Null(rows.Single(r => r.brokerFillId == "f-partial-1").mid);
        Assert.Equal(102.05m, rows.Single(r => r.brokerFillId == "f-partial-2").mid);
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

    // ── ExpireUnaccepted: clean up dispatch-failure zombies ─────────────

    private static OrderIntent IgIntent(string epic) =>
        new(
            ClientOrderId: Guid.NewGuid(),
            Broker: "IG_DEMO",
            Symbol: epic,
            Side: "BUY",
            Qty: 1m,
            OrderType: "MKT",
            StrategyId: "ichimoku_fx_mr");

    private void Backdate(Guid id, TimeSpan ago)
    {
        using var conn = _fx.Db.OpenConnection();
        conn.Execute(
            "UPDATE oms_orders SET last_state_change_at_utc = @t WHERE id = @id;",
            new { t = DateTime.UtcNow - ago, id });
    }

    [Fact]
    public async Task ExpireUnaccepted_cancels_stale_placement_order_with_no_broker_id()
    {
        var oms = NewService();   // _services null → ResolveIG()/T212 null → no placement,
                                  // so an IG_DEMO approve lands in SUBMITTED with broker_order_id NULL.

        // Zombie: IG_DEMO, SUBMITTED, no broker id, stale.
        var zombie = await oms.EnqueueAsync(IgIntent("CS.D.EURUSD.MINI.IP"), "test");
        await oms.ApproveAsync(zombie.Id, "operator");
        Assert.Equal(OmsState.Submitted, (await oms.GetAsync(zombie.Id))!.State);
        Backdate(zombie.Id, TimeSpan.FromMinutes(10));

        // PAPER, SUBMITTED, no broker id, stale — must NOT be swept (engine
        // fills it; a null broker id is normal, not a failed dispatch).
        var paper = await oms.EnqueueAsync(SampleIntent("AAPL"), "test");
        await oms.ApproveAsync(paper.Id, "operator");
        Backdate(paper.Id, TimeSpan.FromMinutes(10));

        // FRESH IG_DEMO (within grace) — must NOT be swept yet.
        var fresh = await oms.EnqueueAsync(IgIntent("CS.D.GBPUSD.MINI.IP"), "test");
        await oms.ApproveAsync(fresh.Id, "operator");

        var expired = await oms.ExpireUnacceptedAsync(TimeSpan.FromMinutes(5), "poller:reconcile");

        Assert.Equal(new[] { zombie.Id }, expired);
        Assert.Equal(OmsState.Cancelled, (await oms.GetAsync(zombie.Id))!.State);
        Assert.Equal("expired_no_broker_ack", (await oms.GetAsync(zombie.Id))!.CancelledReason);
        Assert.Equal(OmsState.Submitted, (await oms.GetAsync(paper.Id))!.State);   // PAPER protected
        Assert.Equal(OmsState.Submitted, (await oms.GetAsync(fresh.Id))!.State);   // within grace

        Assert.Equal("CANCELLED", (await oms.ListEventsAsync(zombie.Id)).Last().EventType);
    }

    [Fact]
    public async Task ExpireUnaccepted_leaves_orders_that_have_a_broker_id()
    {
        var oms = NewService();
        var row = await oms.EnqueueAsync(IgIntent("CS.D.EURUSD.MINI.IP"), "test");
        await oms.ApproveAsync(row.Id, "operator");
        // Simulate a successful dispatch having stamped a broker deal ref.
        using (var conn = _fx.Db.OpenConnection())
            conn.Execute(
                "UPDATE oms_orders SET broker_order_id = 'DEALREF123' WHERE id = @id;",
                new { id = row.Id });
        Backdate(row.Id, TimeSpan.FromMinutes(10));

        var expired = await oms.ExpireUnacceptedAsync(TimeSpan.FromMinutes(5), "poller:reconcile");

        Assert.Empty(expired);
        Assert.Equal(OmsState.Submitted, (await oms.GetAsync(row.Id))!.State);
    }

    [Fact]
    public async Task Backfill_sets_price_on_a_FILLED_order_recorded_at_zero()
    {
        var oms = NewService();
        var row = await oms.EnqueueAsync(SampleIntent(), "test");
        await oms.ApproveAsync(row.Id, "operator");
        // Full fill recorded at price 0 — T212 aged the order out before the
        // poller could capture the price.
        var filled = await oms.RecordFillAsync(
            row.Id, 10m, 0m, 0m, "USD", "fill-0", "poller:T212_DEMO");
        Assert.Equal(OmsState.Filled, filled.State);
        Assert.Equal(0m, filled.AvgFillPrice);

        var ok = await oms.BackfillFillPriceAsync(row.Id, 187.5m, "poller:T212_DEMO:backfill");

        Assert.True(ok);
        var after = (await oms.GetAsync(row.Id))!;
        Assert.Equal(187.5m, after.AvgFillPrice);
        Assert.Equal(OmsState.Filled, after.State);        // state untouched
        Assert.Equal(filled.FilledQty, after.FilledQty);   // qty untouched
        var events = await oms.ListEventsAsync(row.Id);
        Assert.Contains(events, e => e.EventType == "FILL_PRICE_BACKFILL");
    }

    [Fact]
    public async Task Backfill_never_overwrites_a_real_fill_price()
    {
        var oms = NewService();
        var row = await oms.EnqueueAsync(SampleIntent(), "test");
        await oms.ApproveAsync(row.Id, "operator");
        await oms.RecordFillAsync(row.Id, 10m, 100m, 0m, "USD", "fill-1", "broker");

        var ok = await oms.BackfillFillPriceAsync(row.Id, 999m, "poller:T212_DEMO:backfill");

        Assert.False(ok);
        Assert.Equal(100m, (await oms.GetAsync(row.Id))!.AvgFillPrice);
    }

    [Fact]
    public async Task Backfill_is_a_noop_for_a_non_terminal_order()
    {
        var oms = NewService();
        var row = await oms.EnqueueAsync(SampleIntent(), "test");
        await oms.ApproveAsync(row.Id, "operator");   // SUBMITTED, not filled

        var ok = await oms.BackfillFillPriceAsync(row.Id, 50m, "poller:T212_DEMO:backfill");

        Assert.False(ok);
        Assert.Null((await oms.GetAsync(row.Id))!.AvgFillPrice);
    }
}

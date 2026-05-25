using Dapper;
using TradePro.Api.Oms;
using TradePro.Api.Tests.Infrastructure;
using Xunit;

namespace TradePro.Api.Tests.Oms;

/// <summary>
/// Net-position derivation for the position service (task #28).
/// Strategies seed _fx_positions from this view so reruns don't
/// double up on intents. Pin the BUY+/SELL-, weighted-avg, zero-net-
/// excluded contracts.
/// </summary>
[Collection("postgres")]
public sealed class OmsPositionsTest
{
    private readonly PostgresFixture _fx;
    public OmsPositionsTest(PostgresFixture fx)
    {
        _fx = fx;
        using var conn = _fx.Db.OpenConnection();
        conn.Execute("TRUNCATE oms_orders CASCADE;");
    }

    private PostgresOmsService NewService() => new(_fx.Db);

    private async Task<Guid> EnqueueAndFillAsync(
        PostgresOmsService oms,
        string strategyId,
        string symbol,
        string side,
        decimal qty,
        decimal price)
    {
        var intent = new OrderIntent(
            ClientOrderId: Guid.NewGuid(),
            Broker: "PAPER",
            Symbol: symbol,
            Side: side,
            Qty: qty,
            OrderType: "MKT",
            StrategyId: strategyId);
        var row = await oms.EnqueueAsync(intent, "test");
        await oms.ApproveAsync(row.Id, "test");
        await oms.RecordFillAsync(
            row.Id, qty, price, fee: 0, currency: "USD",
            brokerFillId: null, actor: "test");
        return row.Id;
    }

    [Fact]
    public async Task Single_buy_fill_produces_signed_positive_position()
    {
        var oms = NewService();
        await EnqueueAndFillAsync(oms, "ichi_fx", "EURUSD", "BUY", qty: 100m, price: 1.10m);

        var positions = await oms.ListPositionsAsync(strategyId: null);
        var p = Assert.Single(positions);
        Assert.Equal("EURUSD", p.Symbol);
        Assert.Equal(100m, p.Quantity);
        Assert.Equal(1.10m, p.AvgPrice);
    }

    [Fact]
    public async Task Sell_offsets_buy_partially_leaving_net_position()
    {
        var oms = NewService();
        await EnqueueAndFillAsync(oms, "ichi_fx", "EURUSD", "BUY", 100m, 1.10m);
        await EnqueueAndFillAsync(oms, "ichi_fx", "EURUSD", "SELL", 30m, 1.12m);

        var positions = await oms.ListPositionsAsync(strategyId: null);
        var p = Assert.Single(positions);
        Assert.Equal(70m, p.Quantity);
        // Weighted avg over all fills:
        //   (100 * 1.10 + 30 * 1.12) / 130 = (110 + 33.6) / 130 ≈ 1.10462
        Assert.NotNull(p.AvgPrice);
        Assert.Equal(1.10462m, Math.Round(p.AvgPrice!.Value, 5));
    }

    [Fact]
    public async Task Zero_net_position_is_omitted_from_results()
    {
        var oms = NewService();
        await EnqueueAndFillAsync(oms, "ichi_fx", "EURUSD", "BUY", 50m, 1.10m);
        await EnqueueAndFillAsync(oms, "ichi_fx", "EURUSD", "SELL", 50m, 1.12m);

        var positions = await oms.ListPositionsAsync(strategyId: null);
        Assert.Empty(positions);
    }

    [Fact]
    public async Task Positions_grouped_per_strategy_and_symbol()
    {
        var oms = NewService();
        await EnqueueAndFillAsync(oms, "ichi_fx", "EURUSD", "BUY", 10m, 1.10m);
        await EnqueueAndFillAsync(oms, "ichi_fx", "GBPUSD", "BUY", 20m, 1.30m);
        await EnqueueAndFillAsync(oms, "compass", "AAPL", "BUY", 5m, 250m);

        var all = await oms.ListPositionsAsync(strategyId: null);
        Assert.Equal(3, all.Count);

        var fxOnly = await oms.ListPositionsAsync(strategyId: "ichi_fx");
        Assert.Equal(2, fxOnly.Count);
        Assert.All(fxOnly, p => Assert.Equal("ichi_fx", p.StrategyId));
    }

    [Fact]
    public async Task Cancelled_orders_do_not_contribute()
    {
        var oms = NewService();
        // First a real fill we want to keep.
        await EnqueueAndFillAsync(oms, "ichi_fx", "EURUSD", "BUY", 100m, 1.10m);
        // Now an order we enqueue + cancel BEFORE any fill.
        var cancelMe = new OrderIntent(
            ClientOrderId: Guid.NewGuid(),
            Broker: "PAPER", Symbol: "EURUSD", Side: "BUY", Qty: 50m,
            OrderType: "MKT", StrategyId: "ichi_fx");
        var row = await oms.EnqueueAsync(cancelMe, "test");
        await oms.CancelAsync(row.Id, "test", "test_cancel");

        var positions = await oms.ListPositionsAsync(strategyId: "ichi_fx");
        var p = Assert.Single(positions);
        Assert.Equal(100m, p.Quantity);  // cancelled row contributes nothing
    }
}

using TradePro.Api.Oms;
using Xunit;

namespace TradePro.Api.Tests.Oms;

/// <summary>18 Nov 2026: 12 of 22 rule-driven IBKR exits sat FILLED at price
/// 0 — the position reconciler settled them before the fill pass saw IBKR's
/// execution. The confirmation pass prices them from the executions.</summary>
public class IBKRAssumedFillConfirmationTest
{
    private static readonly Guid A = Guid.NewGuid(), B = Guid.NewGuid(), C = Guid.NewGuid();

    [Fact]
    public void An_assumed_fill_is_priced_from_its_executions_volume_weighted()
    {
        var fills = new Dictionary<string, (decimal Qty, decimal Notional, string? ExecId)>
        {
            ["111"] = (30m, 10m * 100m + 20m * 103m, "exec-9"),   // two executions: 100 x10, 103 x20
        };
        var got = IBKRFillReconciler.ConfirmationsFor(new[] { (A, "111") }, fills).ToList();
        var one = Assert.Single(got);
        Assert.Equal(A, one.OrderId);
        Assert.Equal(102m, one.Price);
        Assert.Equal("exec-9", one.ExecId);
    }

    [Fact]
    public void An_order_with_no_matching_execution_is_left_alone()
    {
        var fills = new Dictionary<string, (decimal Qty, decimal Notional, string? ExecId)>
        {
            ["111"] = (5m, 500m, "e1"),
        };
        var got = IBKRFillReconciler.ConfirmationsFor(new[] { (B, "222"), (C, "") }, fills).ToList();
        Assert.Empty(got);
    }

    [Fact]
    public void A_zero_price_execution_never_confirms()
    {
        var fills = new Dictionary<string, (decimal Qty, decimal Notional, string? ExecId)>
        {
            ["333"] = (5m, 0m, null),
        };
        Assert.Empty(IBKRFillReconciler.ConfirmationsFor(new[] { (A, "333") }, fills));
    }
}

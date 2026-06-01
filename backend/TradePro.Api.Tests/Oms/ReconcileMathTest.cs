using TradePro.Api.Oms;
using Xunit;

namespace TradePro.Api.Tests.Oms;

/// <summary>
/// Regression coverage for the sync-from-broker netting bug: a symbol with
/// BOTH a strategy bucket and a null reconcile bucket must net by their SUM,
/// not by the first bucket. This is the scenario the old endpoint code
/// (GroupBy.First) got wrong, which had no test because the logic lived in an
/// endpoint lambda.
/// </summary>
public class ReconcileMathTest
{
    static ReconcileMath.Pos P(string sym, decimal qty, decimal? avg = 1m)
        => new(sym, qty, avg);

    [Fact]
    public void Sums_strategy_and_reconcile_buckets_when_flattening()
    {
        // AAPL held in two OMS buckets (strategy +1.31, reconcile +21.96).
        // Broker is flat → must SELL the full net 23.27, not just 1.31.
        var oms = new[]
        {
            P("AAPL_US_EQ", 1.31m),
            P("AAPL_US_EQ", 21.96m),
        };
        var plan = ReconcileMath.ComputeAdjustments(oms, brokerActuals: []);

        var adj = Assert.Single(plan);
        Assert.Equal("SELL", adj.Side);
        Assert.Equal(23.27m, adj.Qty);
    }

    [Fact]
    public void Is_idempotent_once_oms_matches_broker()
    {
        // Net already flat (strategy +1.31, reconcile -1.31) and broker flat
        // → no adjustment. (Proves repeated syncs converge and stop.)
        var oms = new[] { P("AAPL_US_EQ", 1.31m), P("AAPL_US_EQ", -1.31m) };
        Assert.Empty(ReconcileMath.ComputeAdjustments(oms, brokerActuals: []));
    }

    [Fact]
    public void Adopts_broker_position_when_oms_is_flat()
    {
        // OMS empty, broker holds 5 → BUY 5 to adopt.
        var plan = ReconcileMath.ComputeAdjustments(
            omsPositions: [], brokerActuals: [P("MSFT_US_EQ", 5m)]);
        var adj = Assert.Single(plan);
        Assert.Equal("BUY", adj.Side);
        Assert.Equal(5m, adj.Qty);
    }

    [Fact]
    public void Nets_partial_drift_to_broker_target()
    {
        // OMS net 3 (2 + 1 buckets), broker says 5 → BUY 2.
        var oms = new[] { P("NVDA_US_EQ", 2m), P("NVDA_US_EQ", 1m) };
        var plan = ReconcileMath.ComputeAdjustments(oms, [P("NVDA_US_EQ", 5m)]);
        var adj = Assert.Single(plan);
        Assert.Equal("BUY", adj.Side);
        Assert.Equal(2m, adj.Qty);
    }

    [Fact]
    public void Matches_ig_epic_and_t212_ticker_to_same_bare_key()
    {
        // IG spot-FX epic + a bare pair must reconcile as one symbol.
        Assert.Equal("EURUSD", ReconcileMath.Bare("CS.D.EURUSD.MINI.IP"));
        Assert.Equal("AAPL", ReconcileMath.Bare("AAPL_US_EQ"));
    }

    [Fact]
    public void Skips_null_or_blank_symbols()
    {
        var oms = new[] { P("", 5m), P("   ", 3m) };
        Assert.Empty(ReconcileMath.ComputeAdjustments(oms, brokerActuals: []));
    }
}

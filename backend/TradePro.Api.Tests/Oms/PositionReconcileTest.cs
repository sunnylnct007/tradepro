using TradePro.Api.Oms;
using Xunit;

namespace TradePro.Api.Tests.Oms;

/// <summary>
/// Pure decision logic for the unified golden-source reconciler. This is where
/// the no-false-positive guarantee lives: we settle a stuck SELL as executed
/// ONLY when the broker's CLEAN position read shows the symbol netted flat, and
/// we canonicalise corporate-action renames so a broker still reporting LB
/// can't make a BBWI sell look "not held".
/// </summary>
public sealed class PositionReconcileTest
{
    [Theory]
    [InlineData("AMAT_US_EQ", "AMAT")]
    [InlineData("AMAT", "AMAT")]
    [InlineData("LB_US_EQ", "BBWI")]   // corporate-action rename
    [InlineData("FB_US_EQ", "META")]
    [InlineData("UA.D.AVGO.CASH.IP", "AVGO")]
    public void Canonical_strips_and_renames(string input, string expected)
    {
        Assert.Equal(expected, PositionReconcile.Canonical(input));
    }

    [Fact]
    public void SellExecuted_true_when_symbol_not_held()
    {
        // T212 holds FB/LB/WBD — no AMAT. A stuck AMAT sell has executed.
        var held = PositionReconcile.HeldByCanonical(new[]
        {
            ("FB_US_EQ", 1m), ("LB_US_EQ", 34m), ("WBD_US_EQ", 24m),
        });
        Assert.True(PositionReconcile.SellExecuted(held, "AMAT_US_EQ"));
        Assert.True(PositionReconcile.SellExecuted(held, "TSLA_US_EQ"));
    }

    [Fact]
    public void SellExecuted_false_when_symbol_still_held_under_old_ticker()
    {
        // Broker still holds BBWI *under LB*. A stuck BBWI sell must NOT be
        // settled — the position is still there (this is the false-fill trap
        // the canonicalisation closes).
        var held = PositionReconcile.HeldByCanonical(new[] { ("LB_US_EQ", 34m) });
        Assert.False(PositionReconcile.SellExecuted(held, "BBWI_US_EQ"));
        Assert.False(PositionReconcile.SellExecuted(held, "LB_US_EQ"));
    }

    [Fact]
    public void SellExecuted_false_when_short_position_held()
    {
        // The IBKR clone opened a short (negative qty). A sell that executed
        // INTO a short is still "held" (net non-zero) → not a clean flat exit,
        // must NOT auto-settle (surfaces as drift instead).
        var held = PositionReconcile.HeldByCanonical(new[] { ("MRK", -63m) });
        Assert.False(PositionReconcile.SellExecuted(held, "MRK"));
    }

    [Fact]
    public void SellExecuted_true_when_netted_flat()
    {
        // Long + equal short across rows nets to ~0 → flat → executed.
        var held = PositionReconcile.HeldByCanonical(new[] { ("KO", 224m), ("KO", -224m) });
        Assert.True(PositionReconcile.SellExecuted(held, "KO"));
    }

    [Fact]
    public void HeldByCanonical_merges_old_and_new_ticker_rows()
    {
        var held = PositionReconcile.HeldByCanonical(new[] { ("LB_US_EQ", 10m), ("BBWI_US_EQ", 24m) });
        Assert.Equal(34m, held["BBWI"]);
        Assert.False(held.ContainsKey("LB"));
    }
}

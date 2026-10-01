using TradePro.Api.Risk;
using Xunit;

namespace TradePro.Api.Tests.Risk;

/// <summary>
/// A long-only sleeve must never be able to sell itself short.
///
/// THE DAMAGE, 1 Oct 2026. mean_reversion_swing_ibkr sold ESNT twenty-three
/// times between 00:15 and 06:43, 118 shares every time, turning a +118 long
/// into a -2,596 SHORT. momentum_pullback did the same to CLF nine times:
/// +261 -> -2,088. Every order logged identically, all night:
///
///     IBKR_PAPER AUTO-OMS · SELL ESNT qty=118 tag=swing exit stop held=2
///
/// The exit rule was not wrong. Fill recording had been broken since 29 Sep,
/// so the OMS never learned the sells had filled; the strategy kept reading
/// "ESNT 118 open" and re-sold the full size every cycle.
///
/// These tests exercise the DECISION, not the source text. The sibling
/// market-hours gate is covered by a source-slice test, and this repo has
/// already been bitten by that approach — a retry once made placement
/// unreachable for two sessions while every source-slice assertion passed.
/// A test that reads code cannot see a branch that never runs, so the rule
/// was extracted into a pure function precisely so it could be called.
/// </summary>
public class OversellGuardTest
{
    private const string Sym = "ESNT_US_EQ";
    private const string Sid = "mean_reversion_swing_ibkr";
    private const string Brk = "IBKR_PAPER";

    /// The first sell is legitimate: 118 held, 118 sold.
    [Fact]
    public void SellingExactlyWhatIsHeldIsAllowed()
    {
        Assert.Null(RiskGate.OversellVerdict(118m, 118m, Sym, Sid, Brk));
    }

    [Fact]
    public void PartialExitIsAllowed()
    {
        Assert.Null(RiskGate.OversellVerdict(50m, 118m, Sym, Sid, Brk));
    }

    /// THE INCIDENT. Once the first sell is in the ledger the net is 0, and
    /// every one of the twenty-two repeats must be refused.
    [Fact]
    public void TheSecondSellOfTheSamePositionIsRefused()
    {
        var v = RiskGate.OversellVerdict(118m, 0m, Sym, Sid, Brk);
        Assert.NotNull(v);
        Assert.Equal("oversell", v!.Gate);
    }

    /// Replay the real sequence. Starting flat, buy 118, then issue the 23
    /// sells that actually went out. Exactly ONE may survive the gate, and
    /// the book must never go short.
    [Fact]
    public void TheWholeRunawayIsCappedAtOneOrder()
    {
        var net = 0m;
        net += 118m;                       // the entry

        var allowed = 0;
        for (var i = 0; i < 23; i++)       // the 23 sells, as they were sent
        {
            if (RiskGate.OversellVerdict(118m, net, Sym, Sid, Brk) is null)
            {
                allowed++;
                net -= 118m;
            }
        }

        Assert.Equal(1, allowed);
        Assert.Equal(0m, net);
        Assert.True(net >= 0m, "a long-only sleeve ended the sequence SHORT");
    }

    /// CLF, same shape, different size: +261 then nine sells of 261.
    [Fact]
    public void MomentumClfSequenceIsAlsoCapped()
    {
        var net = 261m;
        var allowed = 0;
        for (var i = 0; i < 9; i++)
        {
            if (RiskGate.OversellVerdict(261m, net, Sym, "momentum_pullback_ibkr", Brk) is null)
            {
                allowed++;
                net -= 261m;
            }
        }
        Assert.Equal(1, allowed);
        Assert.Equal(0m, net);
    }

    /// Selling into an already-short book is the state the account is in NOW
    /// (ESNT -2,596). Nothing may add to it.
    [Fact]
    public void SellingWhileAlreadyShortIsRefused()
    {
        Assert.NotNull(RiskGate.OversellVerdict(118m, -2596m, Sym, Sid, Brk));
    }

    /// Overselling by any margin is refused, not silently clamped. Clamping
    /// would hide the broken fill feed that causes this.
    [Fact]
    public void OversizedExitIsRefusedNotTrimmed()
    {
        var v = RiskGate.OversellVerdict(119m, 118m, Sym, Sid, Brk);
        Assert.NotNull(v);
        Assert.Contains("118", v!.Reason);
    }

    /// The refusal has to say what to fix. A guard that fires without naming
    /// the real cause gets relaxed by the next person who hits it.
    [Fact]
    public void TheRefusalNamesTheRealCause()
    {
        var v = RiskGate.OversellVerdict(118m, 0m, Sym, Sid, Brk);
        Assert.NotNull(v);
        Assert.Contains("fill feed", v!.Reason);
    }
}

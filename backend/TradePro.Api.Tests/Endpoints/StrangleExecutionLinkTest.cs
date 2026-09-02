using Xunit;

namespace TradePro.Api.Tests.Endpoints;

/// <summary>
/// Linking a strangle DECISION to what actually executed.
///
/// Owner, 31 Aug 2026, asking the plainest possible question — "f the strangell
/// worked or not" — which the platform could not answer from its own records.
/// The decision log stopped at the decision: nothing said whether the order was
/// placed, what we were FILLED at, or what it cost to close. Both of that day's
/// numbers had to be reconstructed from the broker by hand.
/// </summary>
public class StrangleExecutionLinkTest
{
    private static string Src(string file)
    {
        var d = new DirectoryInfo(AppContext.BaseDirectory);
        while (d is not null
               && !Directory.Exists(Path.Combine(d.FullName, ".git"))
               && !File.Exists(Path.Combine(d.FullName, ".git")))
            d = d.Parent;
        Assert.NotNull(d);
        return File.ReadAllText(Path.Combine(d!.FullName, file));
    }

    private const string Endpoint =
        "backend/TradePro.Api/Endpoints/StrangleDecisionLogEndpoints.cs";

    [Fact]
    public void AnExecutionWithNoDecisionIsRefusedNotInserted()
    {
        // A fill with no recorded reasoning is the row that makes a forward
        // test unauditable — worse than no row at all. It must 404, never
        // silently create a decision-less execution.
        var s = Src(Endpoint);
        Assert.Contains("UPDATE strangle_decision_log", s);
        Assert.Contains("cannot be attached to a", s);
        Assert.Contains("statusCode: 404", s);
        // An UPDATE keyed on the decision — never an INSERT into this path.
        var exec = s[s.IndexOf("/execution", StringComparison.Ordinal)..];
        var upsert = exec[..exec.IndexOf("g.MapGet", StringComparison.Ordinal)];
        Assert.DoesNotContain("INSERT INTO strangle_decision_log", upsert);
    }

    [Fact]
    public void ExecutionIsKeyedOnTheSameTripleAsTheDecision()
    {
        // Any other key would let an execution attach to the wrong decision —
        // or, as on 2 Sep 2026, to NO decision at all.
        //
        // This originally pinned `as_of`. Migration 073 then moved the decision
        // key to the TRADED session (exchange_date) and this endpoint was left
        // behind, so four legs closed and not one exit was recorded: the write
        // found no row and 404'd. The two keys MUST be the same expression.
        var s = Src(Endpoint);
        Assert.Contains("WHERE market = @Market", s);
        Assert.Contains("AND COALESCE(exchange_date, as_of) = @AsOf", s);
        Assert.DoesNotContain("AND as_of  = @AsOf", s);
        Assert.Contains("COALESCE(expiry_kind, '') = COALESCE(@ExpiryKind, '')", s);

        // The decision upsert must key the same way, or they drift again.
        Assert.Contains(
            "ON CONFLICT (market, COALESCE(exchange_date, as_of), COALESCE(expiry_kind, ''))", s);
    }

    [Fact]
    public void PartialAndShadowAreRecordedSeparatelyFromAPlainFill()
    {
        // A one-legged fill is a NAKED short, not a strangle. A shadow
        // placement is one the gate REFUSED. Averaging either into the gated
        // two-leg population corrupts the win rate the gate is judged on.
        var s = Src(Endpoint);
        Assert.Contains("partial          = COALESCE(@Partial, partial)", s);
        Assert.Contains("shadow           = COALESCE(@Shadow, shadow)", s);
    }

    [Fact]
    public void EveryExecutionColumnIsNullable()
    {
        // A decision is written when MADE; placement follows, and the exit
        // hours later. Grading a row before its session closes is the same
        // lookahead this strategy has already been corrected for, so "not yet
        // known" must be representable.
        var m = Src("backend/TradePro.Api/db/migrations/072_strangle_execution_link.sql");
        foreach (var col in new[]
                 {
                     "placed", "partial", "shadow", "broker_order_ids",
                     "credit_actual", "exit_cost_actual", "realised_pnl",
                 })
            Assert.Contains($"ADD COLUMN IF NOT EXISTS {col}", m);
        Assert.DoesNotContain("NOT NULL", m);
    }

    [Fact]
    public void TheMigrationCarriesNoStatementLevelTransaction()
    {
        // The runner rejects these — migration 064's stray COMMIT; masked
        // every error behind it and cost a session.
        var m = Src("backend/TradePro.Api/db/migrations/072_strangle_execution_link.sql");
        Assert.DoesNotContain("BEGIN;", m);
        Assert.DoesNotContain("COMMIT;", m);
    }
}

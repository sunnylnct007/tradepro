using Xunit;

namespace TradePro.Api.Tests.Endpoints;

/// <summary>
/// The decision log's identity: ONE row per market, per TRADED session, per expiry.
///
/// 1 Sep 2026 — the log was losing a day, silently. NIFTY and BANKNIFTY
/// decisions made on 31 Aug were gone, replaced by decisions made on 1 Sep.
///
/// The key was (market, as_of, expiry_kind), and as_of is the SETTLED session
/// the GATE READ, not the session being traded:
///
///   31 Aug 13:xx  India last settled = 31 Aug  -> as_of 2026-08-31
///    1 Sep 04:00  India last settled = 31 Aug  -> as_of 2026-08-31  COLLISION
///
/// Wrong in both directions: it merged different trading sessions, and split a
/// single one (1 Sep produced rows under both as_of 31 Aug and as_of 1 Sep).
/// US markets escaped only because their settled session happened to advance.
/// </summary>
public class StrangleDecisionKeyTest
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
    private const string Migration =
        "backend/TradePro.Api/db/migrations/073_decision_key_is_the_traded_session.sql";

    [Fact]
    public void TheUpsertKeysOnTheTradedSessionNotTheSettledOne()
    {
        var s = Src(Endpoint);
        Assert.Contains(
            "ON CONFLICT (market, COALESCE(exchange_date, as_of), COALESCE(expiry_kind, ''))", s);
        // The old key must be gone from the conflict target, or consecutive
        // days collide again.
        Assert.DoesNotContain("ON CONFLICT (market, as_of, COALESCE(expiry_kind, ''))", s);
    }

    [Fact]
    public void AsOfIsKeptAsDataAndRefreshedOnConflict()
    {
        // as_of records what the gate READ — the input needed to re-judge the
        // decision later. It stops being an identity; it must not stop being
        // recorded, and a same-session re-run must update it.
        var s = Src(Endpoint);
        Assert.Contains("as_of         = EXCLUDED.as_of", s);
    }

    [Fact]
    public void TheIndexMatchesTheConflictTargetExactly()
    {
        // A conflict target that does not match a unique index is a runtime
        // error on every insert, not a compile error.
        var m = Src(Migration);
        Assert.Contains(
            "(market, COALESCE(exchange_date, as_of), COALESCE(expiry_kind, ''))", m);
        Assert.Contains("DROP INDEX IF EXISTS strangle_decision_log_uniq", m);
    }

    [Fact]
    public void DuplicatesAreCollapsedKeepingTheLatestEvaluation()
    {
        // Creating the unique index fails outright if same-key rows already
        // exist — and 1 Sep had exactly that. The newest evaluation wins: same
        // session, same market, re-read later in the day.
        var m = Src(Migration);
        Assert.Contains("DELETE FROM strangle_decision_log", m);
        Assert.Contains("(a.decided_at_utc, a.id) < (b.decided_at_utc, b.id)", m);
    }

    [Fact]
    public void TheMigrationCarriesNoStatementLevelTransaction()
    {
        var m = Src(Migration);
        Assert.DoesNotContain("BEGIN;", m);
        Assert.DoesNotContain("COMMIT;", m);
    }
}

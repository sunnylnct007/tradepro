using System.Text.RegularExpressions;
using Dapper;
using TradePro.Api.Tests.Infrastructure;
using Xunit;

namespace TradePro.Api.Tests.Endpoints;

/// <summary>
/// An exit must be filed against the round-trip it actually closed.
///
/// THE DEFECT, from the live decision log (24 Sep 2026). index_strangle_close.py
/// hardcoded expiryKind="monthly" on every close — left over from when monthly
/// was the only expiry placed. On 14 Sep 2026, the first session running both:
///
///     SPX weekly   placed=true   credit 2,866.74   exit —          realised —
///     SPX MONTHLY  placed=FALSE  credit —          exit 2,691.63   realised 175.11
///
/// 2,866.74 − 2,691.63 = 175.11 exactly. The arithmetic was right and the ROW
/// was wrong: a monthly unit that never placed carried the weekly's exit and
/// P&L. Same shape on 15 and 21 Sep. Before 14 Sep only monthly placed, so the
/// hardcoded value was accidentally correct and every row reconciled at 1.00x —
/// which is why this looked like a sudden break rather than a latent bug.
///
/// The strangle_execution writer was corrected that day to match on strikes.
/// THIS writer — the decision log, which the end-of-day check, the desk board
/// and every P&L query read — was not. One fix, two sites, one applied.
///
/// This test runs the PRODUCTION SQL, read out of the endpoint source, against
/// a real Postgres. A copy of the statement pasted into a test would pass while
/// the shipped one stayed broken.
/// </summary>
[Collection("postgres")]
public sealed class TheExitMustLandOnTheExpiryItClosed
{
    private readonly PostgresFixture _fx;

    public TheExitMustLandOnTheExpiryItClosed(PostgresFixture fx)
    {
        _fx = fx;
        using var conn = _fx.Db.OpenConnection();
        conn.Execute("TRUNCATE strangle_decision_log RESTART IDENTITY CASCADE;");
    }

    /// <summary>The /execution UPDATE exactly as it ships, never a copy.</summary>
    private static string ProductionSql()
    {
        // .git is a FILE in a worktree and a directory in a clone — check both,
        // or every one of these tests fails in a worktree for a reason that has
        // nothing to do with what it is testing.
        var d = new DirectoryInfo(AppContext.BaseDirectory);
        while (d is not null
               && !Directory.Exists(Path.Combine(d.FullName, ".git"))
               && !File.Exists(Path.Combine(d.FullName, ".git")))
            d = d.Parent;
        Assert.NotNull(d);
        var src = File.ReadAllText(Path.Combine(
            d!.FullName, "backend/TradePro.Api/Endpoints/StrangleDecisionLogEndpoints.cs"));

        var at = src.IndexOf("UPDATE strangle_decision_log", StringComparison.Ordinal);
        Assert.True(at > 0, "could not find the decision-log UPDATE");
        var end = src.IndexOf("\", row);", at, StringComparison.Ordinal);
        Assert.True(end > at, "could not find the end of the UPDATE");
        return src[at..end].Replace("\"\"", "\"");
    }

    private static DynamicParameters Params(string sql,
                                            IDictionary<string, object?> set)
    {
        // Every @param the statement mentions, defaulted to NULL, so the test
        // states only what the close actually sends.
        var p = new DynamicParameters();
        foreach (Match m in Regex.Matches(sql, @"@(\w+)"))
            if (!p.ParameterNames.Contains(m.Groups[1].Value))
                p.Add(m.Groups[1].Value, null);
        foreach (var kv in set) p.Add(kv.Key, kv.Value);
        return p;
    }

    private void SeedBothExpiries(DateTime session)
    {
        using var conn = _fx.Db.OpenConnection();
        // XSP on 14 Sep 2026: the weekly and the monthly, at the strikes that
        // actually differed between them.
        conn.Execute(@"
            INSERT INTO strangle_decision_log
                (market, as_of, exchange_date, decision, reason, expiry_kind,
                 put_strike, call_strike, placed)
            VALUES
                ('XSP', @s, @s, 'CANDIDATE', 'test', 'monthly', 752, 775, false),
                ('XSP', @s, @s, 'CANDIDATE', 'test', 'weekly',  750, 773, true);",
            new { s = session });
    }

    [Fact]
    public async Task AWeeklyExitDoesNotLandOnTheMonthlyRow()
    {
        var session = new DateTime(2026, 9, 14);
        SeedBothExpiries(session);
        var sql = ProductionSql();

        // The close job knows the STRIKES it closed and claims no expiry —
        // exactly what index_strangle_close.py now sends.
        await using var conn = await _fx.Db.OpenConnectionAsync();
        var n = await conn.ExecuteAsync(sql, Params(sql, new Dictionary<string, object?>
        {
            ["Market"] = "XSP",
            ["AsOf"] = session,
            ["ExpiryKind"] = null,
            ["PutStrike"] = 750m,
            ["CallStrike"] = 773m,
            ["ExitCostActual"] = 263.63m,
            ["RealisedPnl"] = 12.93m,
            ["ClosedAtUtc"] = new DateTime(2026, 9, 14, 19, 45, 0, DateTimeKind.Utc),
            ["CloseTrigger"] = "end_of_day",
        }));

        Assert.Equal(1, n);   // it matched exactly one row...

        var rows = (await conn.QueryAsync<(string Kind, decimal? Exit, decimal? Pnl)>(
            @"SELECT expiry_kind, exit_cost_actual, realised_pnl
                FROM strangle_decision_log WHERE market = 'XSP' ORDER BY expiry_kind;"))
            .ToList();

        var monthly = rows.Single(r => r.Kind == "monthly");
        var weekly = rows.Single(r => r.Kind == "weekly");

        // ...and it was the one whose strikes were closed.
        Assert.Equal(263.63m, weekly.Exit);
        Assert.Equal(12.93m, weekly.Pnl);

        // The row that never placed must carry no money. This is the assertion
        // that fails against the shipped SQL before this fix.
        Assert.Null(monthly.Exit);
        Assert.Null(monthly.Pnl);
    }

    [Fact]
    public async Task AnExitWhoseStrikesMatchNothingIsRefusedNotReassigned()
    {
        // Landing on a guess is what produced nine sessions of wrong rows. If
        // we cannot identify the round-trip, the write must match NOTHING so
        // the endpoint's own 404 fires and says the P&L is unrecorded.
        var session = new DateTime(2026, 9, 14);
        SeedBothExpiries(session);
        var sql = ProductionSql();

        await using var conn = await _fx.Db.OpenConnectionAsync();
        var n = await conn.ExecuteAsync(sql, Params(sql, new Dictionary<string, object?>
        {
            ["Market"] = "XSP",
            ["AsOf"] = session,
            ["ExpiryKind"] = null,
            ["PutStrike"] = 999m,       // a strike nobody traded
            ["CallStrike"] = 1000m,
            ["ExitCostActual"] = 100m,
            ["RealisedPnl"] = 1m,
            ["ClosedAtUtc"] = new DateTime(2026, 9, 14, 19, 45, 0, DateTimeKind.Utc),
        }));

        Assert.Equal(0, n);
    }

    [Fact]
    public async Task AnOPENStillAttachesByExpiryKind()
    {
        // An open is keyed by expiry_kind, which its caller sends correctly and
        // which must keep working: a fill may legitimately land on a strike the
        // proposal did not name, and matching those on strikes would 404 every
        // placement.
        var session = new DateTime(2026, 9, 14);
        SeedBothExpiries(session);
        var sql = ProductionSql();

        await using var conn = await _fx.Db.OpenConnectionAsync();
        var n = await conn.ExecuteAsync(sql, Params(sql, new Dictionary<string, object?>
        {
            ["Market"] = "XSP",
            ["AsOf"] = session,
            ["ExpiryKind"] = "weekly",
            ["Placed"] = true,
            ["CreditActual"] = 88.78m,
            ["PlacedAtUtc"] = new DateTime(2026, 9, 14, 14, 12, 0, DateTimeKind.Utc),
        }));

        Assert.Equal(1, n);
        var credit = await conn.ExecuteScalarAsync<decimal?>(
            @"SELECT credit_actual FROM strangle_decision_log
               WHERE market='XSP' AND expiry_kind='weekly';");
        Assert.Equal(88.78m, credit);
    }
}

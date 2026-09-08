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

    /// <summary>Comments stripped: this file now EXPLAINS the old behaviour in
    /// prose, so a plain Contains would happily match the description of the
    /// bug instead of the code. Three tests in this repo have already passed
    /// that way.</summary>
    private static string CodeOnly(string src) =>
        string.Join("\n", src.Split('\n')
            .Where(l => !l.TrimStart().StartsWith("--")
                     && !l.TrimStart().StartsWith("//")));

    [Fact]
    public void TheSelectFiltersOnTheSameKeyTheUpsertWritesOn()
    {
        // The upsert keys on COALESCE(exchange_date, as_of); the SELECT filtered
        // on as_of. Those are the same column only until a weekend or a holiday
        // separates them.
        //
        // 8 Sep 2026: US rows for exchange_date 2026-09-08 carried as_of
        // 2026-09-04, because 7 Sep was Labor Day. The close job asks days=3,
        // so its window began 2026-09-05 and every US row fell outside it. It
        // read zero placed rows while four pairs were open at the broker,
        // judged them opened on an earlier session, and flattened all four
        // seven minutes after they were opened.
        var s = CodeOnly(Src(Endpoint));
        Assert.Contains(
            "AND COALESCE(exchange_date, as_of) >= (CURRENT_DATE - (@days || ' days')::interval)", s);
        Assert.DoesNotContain("AND as_of >= (CURRENT_DATE", s);
    }

    [Fact]
    public void TheSelectOrdersOnThatSameKeyToo()
    {
        // Ordering by as_of puts a row from a holiday-shortened week in the
        // wrong place, so "the latest decision" is not the latest one traded.
        var s = CodeOnly(Src(Endpoint));
        Assert.Contains("ORDER BY COALESCE(exchange_date, as_of) DESC, market", s);
    }

    [Fact]
    public void TheSummaryFiltersOnTheKeyToo()
    {
        // The THIRD site. Found only by going looking for the other two after
        // fixing the row SELECT — it filtered on as_of like the others, so the
        // per-market tally silently dropped the same holiday-shifted rows.
        // One value, three definitions; fixing two of them is not fixing it.
        var s = CodeOnly(Src(Endpoint));
        Assert.DoesNotContain("WHERE as_of >= (CURRENT_DATE", s);
    }

    [Fact]
    public void TheLivePnlRefusesATotalWhenTheOpenHalfIsUnknown()
    {
        // A realised figure added to an unknown open one is a number that looks
        // complete and is not. Null means UNKNOWN; it must never render as flat.
        var s = CodeOnly(Src(Endpoint));
        Assert.Contains("double? total = unrealised is double u2 ? realised + u2 : null;", s);
    }

    [Fact]
    public void TheLivePnlSurfacesABrokerErrorRatherThanReportingFlat()
    {
        // GetPositionsAsync returns its error in the result rather than
        // throwing. Falling through to an empty list would report the book as
        // FLAT — the single most dangerous thing this endpoint could say.
        var s = CodeOnly(Src(Endpoint));
        Assert.Contains("if (pos.Error is not null)", s);
        Assert.Contains("throw new InvalidOperationException(pos.Error)", s);
    }

    [Fact]
    public void TheLivePnlReadsPositionsFresh()
    {
        // IBKR serves positions from its own cache, and a CLOSED position comes
        // back as a qty-0 row. A stale read here prices a book we do not hold.
        var s = CodeOnly(Src(Endpoint));
        Assert.Contains("GetPositionsAsync(ct, forceFresh: true)", s);
        Assert.Contains("p.Quantity != 0m", s);
    }

    [Fact]
    public void StatsWithholdARatioTheSampleCannotCarry()
    {
        // 5 closed trades cannot support a win rate, and quoting one to a
        // decimal place is how a defect gets mistaken for an edge. Counts are
        // always shown; the RATIO is what is withheld, with the n stated.
        var s = CodeOnly(Src(Endpoint));
        Assert.Contains("const int MinForRate = 20;", s);
        Assert.Contains("winRateWithheld", s);
    }

    [Fact]
    public void StatsNeverAverageGatedWithShadow()
    {
        // The gate IS the strategy. Averaging the days it refused with the days
        // it allowed destroys the only measurement the shadow fills exist to
        // produce.
        var s = CodeOnly(Src(Endpoint));
        Assert.Contains("r.shadow != true", s);
        Assert.Contains("r.shadow == true", s);
    }

    [Fact]
    public void StatsNeverSumAcrossCurrencies()
    {
        // The automated desk is paper USD; the manual India book is real money
        // in INR. One total across both is not a number.
        var s = CodeOnly(Src(Endpoint));
        Assert.Contains("manual.GroupBy(r => (string?)r.currency", s);
    }

    [Fact]
    public void StatsSayWhenNothingHasMeasuredTheStrategyYet()
    {
        // Every closed trade being a shadow fill means the strategy as designed
        // has never traded. That is the headline, and it must be emitted rather
        // than left for the reader to infer from a zero.
        var s = CodeOnly(Src(Endpoint));
        Assert.Contains("if (gated.Count == 0 && shadow.Count > 0)", s);
        Assert.Contains("stale_overnight", s);
    }

    [Fact]
    public void ALegsPlacementTimeIsAttributedOnlyOnAnUNAMBIGUOUSMatch()
    {
        // IBKR gives a position no open time, so it comes from the decision row
        // that placed it, matched on the OCC strike. Two markets can print the
        // same strike, and a confident WRONG timestamp is worse than none --
        // the whole reason timings were asked for is to tell a six-hour trade
        // from a seven-minute one.
        var s = CodeOnly(Src(Endpoint));
        Assert.Contains("if (hits.Count == 1)", s);
        Assert.Contains("not guessing which", s);
        // And an unattributed leg must SAY why rather than render a bare dash.
        Assert.Contains("whyNoTime", s);
    }

    [Fact]
    public void OnlyStillOpenRowsCanClaimAnOpenLeg()
    {
        // A row that already closed cannot be the origin of a leg open right
        // now. Without this, yesterday's SPX 7630P row would lend its
        // timestamp to today's identical strike and report it held for a day.
        var s = CodeOnly(Src(Endpoint));
        Assert.Contains("r.closed_at_utc == null", s);
    }

    [Fact]
    public void ThePnlCarriesTheInstantTheOpenHalfWasPriced()
    {
        // The realised half is historical and does not move; the open half
        // does. A P&L without its mark time is a number of unknown age.
        var s = CodeOnly(Src(Endpoint));
        Assert.Contains("markedAtUtc = DateTime.UtcNow", s);
    }

    [Fact]
    public void ClosedTradesCarryPlacedAndClosedSoHoldTimeIsDerivable()
    {
        // +187.45 over six hours and +187.45 over seven minutes are different
        // facts. On this desk that gap is a strategy result versus the
        // stale_overnight defect.
        var s = CodeOnly(Src(Endpoint));
        Assert.Contains("placedAtUtc = (DateTime?)r.placed_at_utc", s);
        Assert.Contains("closedAtUtc = (DateTime?)r.closed_at_utc", s);
        Assert.Contains("heldMinutes", s);
    }
}

using TradePro.Api.Oms;
using TradePro.Api.Positions;
using Xunit;

namespace TradePro.Api.Tests.Positions;

/// <summary>
/// Pure-core coverage for the data-trust reconciliation invariants
/// (<see cref="PnlReconciliation"/>). These are the checks GET
/// /api/pnl/reconciliation runs against the broker (golden source). The
/// endpoint plumbing is thin; the correctness that matters — does the math
/// FAIL when the numbers disagree, and name the offender — lives here and is
/// proven without any DB / HTTP.
/// </summary>
public class PnlReconciliationTest
{
    // ── 1. open_pnl_reconciles ───────────────────────────────────────────
    [Fact]
    public void OpenPnl_passes_when_sum_matches_account_within_tolerance()
    {
        // Σ marks = 100.40, account Ppl = 100.00, tol = $25 → within band.
        var c = PnlReconciliation.ReconcileOpenPnl(100.40m, 100.00m, 25m);
        Assert.True(c.Ok);
        Assert.Equal("open_pnl_reconciles", c.Name);
        Assert.Equal(PnlReconciliation.SeverityInfo, c.Severity);
    }

    [Fact]
    public void OpenPnl_fails_and_reports_both_numbers_when_marks_disagree()
    {
        // Marks say +500, broker account says +100 → $400 drift, way over $25.
        var c = PnlReconciliation.ReconcileOpenPnl(500m, 100m, 25m);
        Assert.False(c.Ok);
        Assert.Equal(PnlReconciliation.SeverityError, c.Severity);
        Assert.Contains("500", c.Detail);
        Assert.Contains("100", c.Detail);
        Assert.Contains("diff=400", c.Detail);
    }

    [Theory]
    [InlineData(25.00, true)]   // exactly on the boundary → still OK (≤)
    [InlineData(25.01, false)]  // a cent over → FAIL
    [InlineData(-25.00, true)]  // boundary is symmetric (abs diff)
    [InlineData(-25.01, false)]
    public void OpenPnl_tolerance_boundary_is_inclusive(double diff, bool expectOk)
    {
        // account=0 → tol floor is the $25 absolute. diff = sum − account.
        var c = PnlReconciliation.ReconcileOpenPnl((decimal)diff, 0m, 25m);
        Assert.Equal(expectOk, c.Ok);
    }

    [Fact]
    public void OpenPnl_tolerance_scales_with_account_size()
    {
        // Big book: 2% of £10,000 = £200 floor beats the $25 absolute.
        var tol = PnlReconciliation.OpenPnlTolerance(10_000m, absoluteFloor: 25m, pct: 0.02m);
        Assert.Equal(200m, tol);
        // Tiny book: the $25 floor wins over 2% of £100 (£2).
        Assert.Equal(25m, PnlReconciliation.OpenPnlTolerance(100m));
    }

    [Fact]
    public void SumOpenPnlMarks_prefers_broker_ppl_and_falls_back_on_omit()
    {
        // Row 1 carries the broker's own Ppl (+50, golden); row 2 omits it so
        // we recompute (cur−avg)×qty = (110−100)×3 = +30. Σ = 80.
        var pos = new (string, decimal, decimal?, decimal?, decimal?)[]
        {
            ("AAPL", 2m, 180m, 200m, 50m),     // Ppl present → use 50
            ("MSFT", 3m, 100m, 110m, (decimal?)null),  // omit → fallback 30
        };
        var sum = PnlReconciliation.SumOpenPnlMarks(pos, out var note);
        Assert.Equal(80m, sum);
        Assert.Contains("1 row(s) via broker Ppl", note);
        Assert.Contains("1 via", note);
    }

    [Fact]
    public void SumOpenPnlMarks_excludes_rows_with_no_ppl_and_no_usable_quote()
    {
        // GHOST has no Ppl, a zero avg (can't recompute), no current price →
        // excluded (marking it to $0 would fabricate a phantom loss), named.
        var pos = new (string, decimal, decimal?, decimal?, decimal?)[]
        {
            ("AAPL", 1m, 100m, 130m, (decimal?)null),       // fallback = 30
            ("GHOST", 5m, 0m, (decimal?)null, (decimal?)null),  // excluded
        };
        var sum = PnlReconciliation.SumOpenPnlMarks(pos, out var note);
        Assert.Equal(30m, sum);
        Assert.Contains("excluded", note);
        Assert.Contains("GHOST", note);
    }

    [Fact]
    public void OpenPnl_with_fallback_marks_reconciles_to_account_then_passes()
    {
        // 88-position book where rows omit per-row Ppl: we compute marks via
        // the fallback and they DO reconcile to the account Ppl → PASS (no
        // longer skipped). avg=100, cur=110, qty=10 → +100 mark; account=100.
        var pos = new (string, decimal, decimal?, decimal?, decimal?)[]
        {
            ("AAPL", 10m, 100m, 110m, (decimal?)null),
        };
        var sum = PnlReconciliation.SumOpenPnlMarks(pos, out var note);
        var c = PnlReconciliation.ReconcileOpenPnl(
            sum, accountPpl: 100m, tolerance: PnlReconciliation.OpenPnlTolerance(100m), note: note);
        Assert.True(c.Ok);
        Assert.Equal(PnlReconciliation.SeverityInfo, c.Severity);
        Assert.Contains("fallback", c.Detail);
    }

    [Fact]
    public void OpenPnl_with_fallback_fails_with_diff_when_it_does_not_reconcile()
    {
        // The honest-gap case: our per-position Σ (fallback) can't see T212's
        // fxPpl / pie effects, so it does NOT reconcile to the account Ppl.
        // The check must FAIL with both numbers + the diff — NOT forced green.
        var pos = new (string, decimal, decimal?, decimal?, decimal?)[]
        {
            ("AAPL", 10m, 100m, 110m, (decimal?)null),   // computed Σ = +100
        };
        var sum = PnlReconciliation.SumOpenPnlMarks(pos, out var note);
        var c = PnlReconciliation.ReconcileOpenPnl(
            sum, accountPpl: 860m, tolerance: PnlReconciliation.OpenPnlTolerance(860m), note: note);
        Assert.False(c.Ok);
        Assert.Equal(PnlReconciliation.SeverityError, c.Severity);
        Assert.Contains("100", c.Detail);
        Assert.Contains("860", c.Detail);
        Assert.Contains("diff=-760", c.Detail);
    }

    // ── 2. oms_matches_broker ────────────────────────────────────────────
    [Fact]
    public void OmsVsBroker_passes_when_every_symbol_reconciles()
    {
        var oms = new[] { ("AAPL", 5m), ("MSFT", 3m) };
        var broker = new[] { ("AAPL", 5m), ("MSFT", 3m) };
        var c = PnlReconciliation.ReconcileOmsVsBroker(oms, broker, 0.0001m);
        Assert.True(c.Ok);
        Assert.Equal(PnlReconciliation.SeverityInfo, c.Severity);
    }

    [Fact]
    public void OmsVsBroker_sums_strategy_buckets_before_comparing()
    {
        // OMS holds AAPL across two strategy rows (2 + 3); broker says 5 → OK.
        var oms = new[] { ("AAPL", 2m), ("AAPL", 3m) };
        var broker = new[] { ("AAPL", 5m) };
        Assert.True(PnlReconciliation.ReconcileOmsVsBroker(oms, broker, 0.0001m).Ok);
    }

    [Fact]
    public void OmsVsBroker_fails_and_names_the_drifting_symbol()
    {
        // OMS thinks we hold 7 NVDA; broker says 5 → drift of 2 on NVDA only.
        var oms = new[] { ("AAPL", 5m), ("NVDA", 7m) };
        var broker = new[] { ("AAPL", 5m), ("NVDA", 5m) };
        var c = PnlReconciliation.ReconcileOmsVsBroker(oms, broker, 0.0001m);
        Assert.False(c.Ok);
        Assert.Equal(PnlReconciliation.SeverityError, c.Severity);
        Assert.Contains("NVDA", c.Detail);
        Assert.Contains("diff=2", c.Detail);
        Assert.DoesNotContain("AAPL", c.Detail);  // the clean symbol isn't listed
    }

    [Fact]
    public void OmsVsBroker_flags_one_sided_position_as_drift()
    {
        // OMS records a LUK position the broker doesn't report (stale ledger).
        var oms = new[] { ("LUK", 10m) };
        var broker = Array.Empty<(string, decimal)>();
        var c = PnlReconciliation.ReconcileOmsVsBroker(oms, broker, 0.0001m);
        Assert.False(c.Ok);
        Assert.Contains("LUK", c.Detail);
    }

    [Fact]
    public void OmsVsBroker_uses_bare_key_so_broker_formats_line_up()
    {
        // OMS "AAPL" must reconcile with the broker's "AAPL_US_EQ", and an IG
        // epic with its bare pair, via ReconcileMath.Bare.
        var oms = new[] { ("AAPL", 5m), ("EURUSD", 1m) };
        var broker = new[] { ("AAPL_US_EQ", 5m), ("CS.D.EURUSD.MINI.IP", 1m) };
        Assert.True(PnlReconciliation
            .ReconcileOmsVsBroker(oms, broker, 0.0001m, ReconcileMath.Bare).Ok);
    }

    [Fact]
    public void OmsVsBroker_resolves_renamed_ticker_through_map_no_false_drift()
    {
        // The live failure: OMS recorded the position as "JEF" (what the
        // strategy ordered); the broker holds it as the stale "LUK_US_EQ"
        // (Leucadia → Jefferies rename). Resolving OMS "JEF" through the
        // broker_ticker_map → "LUK_US_EQ" → bare "LUK" lines it up with the
        // broker's LUK → NO drift. Without the map this would report the
        // phantom JEF oms=17/broker=0 + LUK oms=0/broker=17 pair.
        var oms = new[] { ("JEF", 17m) };
        var broker = new[] { ("LUK_US_EQ", 17m) };
        string Resolve(string s) => s == "JEF" ? "LUK_US_EQ" : s;

        var c = PnlReconciliation.ReconcileOmsVsBroker(
            oms, broker, 0.0001m, ReconcileMath.Bare, Resolve);
        Assert.True(c.Ok);
        Assert.DoesNotContain("JEF", c.Detail);
        Assert.DoesNotContain("drift", c.Detail);
    }

    [Fact]
    public void OmsVsBroker_still_catches_genuine_drift_after_map_resolution()
    {
        // The map resolves JEF→LUK so the qty must still MATCH; here the OMS
        // says 17 but the broker holds 10 → genuine drift on LUK, still caught.
        var oms = new[] { ("JEF", 17m) };
        var broker = new[] { ("LUK_US_EQ", 10m) };
        string Resolve(string s) => s == "JEF" ? "LUK_US_EQ" : s;

        var c = PnlReconciliation.ReconcileOmsVsBroker(
            oms, broker, 0.0001m, ReconcileMath.Bare, Resolve);
        Assert.False(c.Ok);
        Assert.Contains("LUK", c.Detail);
        Assert.Contains("diff=7", c.Detail);
    }

    [Fact]
    public void OmsVsBroker_unmapped_symbol_falls_back_to_bare_keying()
    {
        // No map row for AAPL → resolver returns it unchanged → plain bare-key
        // still lines OMS "AAPL" up with the broker's "AAPL_US_EQ".
        var oms = new[] { ("AAPL", 5m) };
        var broker = new[] { ("AAPL_US_EQ", 5m) };
        string Resolve(string s) => s;   // empty map: identity
        Assert.True(PnlReconciliation
            .ReconcileOmsVsBroker(oms, broker, 0.0001m, ReconcileMath.Bare, Resolve).Ok);
    }

    // ── 3. realised_trading_types_only ───────────────────────────────────
    [Fact]
    public void RealisedTypes_passes_when_total_is_trading_only()
    {
        // DEAL + WITH only; reported total = their sum.
        var txns = new (string?, decimal)[]
        {
            ("DEAL", 120m), ("WITH", -5m), ("DEAL", -40m),
        };
        var c = PnlReconciliation.ReconcileRealisedTradingTypes(txns, 75m);
        Assert.True(c.Ok);
        Assert.Equal(PnlReconciliation.SeverityInfo, c.Severity);
    }

    [Fact]
    public void RealisedTypes_fails_when_a_DEPO_leaks_into_the_total_the_999M_bug()
    {
        // The regression: a £9.99M demo DEPO got summed into "realised". The
        // reported total includes it; recomputing over DEAL+WITH does not →
        // FAIL, and the leaked DEPO is named.
        var txns = new (string?, decimal)[]
        {
            ("DEAL", 100m),
            ("WITH", -10m),
            ("DEPO", 9_990_000m),   // the £9.99M funding line — NOT P&L
        };
        var reportedWithLeak = 100m - 10m + 9_990_000m;   // bug summed everything
        var c = PnlReconciliation.ReconcileRealisedTradingTypes(txns, reportedWithLeak);
        Assert.False(c.Ok);
        Assert.Equal(PnlReconciliation.SeverityError, c.Severity);
        Assert.Contains("DEPO", c.Detail);
        Assert.Contains("90", c.Detail);   // Σ(DEAL+WITH) = 90 appears in the detail
    }

    [Fact]
    public void RealisedTypes_passes_when_DEPO_present_but_correctly_excluded()
    {
        // The live failure: a DEPO:6.36 funding row EXISTS, but realised
        // correctly excludes it (reported = Σ(DEAL+WITH) = 90). Presence of a
        // funding row is fine/expected — this must PASS, not FAIL, with the
        // excluded row surfaced as an informational note in the detail.
        var txns = new (string?, decimal)[]
        {
            ("DEAL", 100m), ("WITH", -10m), ("DEPO", 6.36m),
        };
        var c = PnlReconciliation.ReconcileRealisedTradingTypes(txns, 90m);
        Assert.True(c.Ok);
        Assert.Equal(PnlReconciliation.SeverityInfo, c.Severity);
        Assert.Contains("DEPO", c.Detail);        // surfaced …
        Assert.Contains("EXCLUDED", c.Detail);    // … as a note, not a leak
    }

    [Fact]
    public void RealisedTypes_fails_only_when_a_non_trading_type_leaks_in()
    {
        // Same DEPO present, but this time it LEAKED into the reported total
        // (96.36 instead of 90) → FAIL, naming the leak.
        var txns = new (string?, decimal)[]
        {
            ("DEAL", 100m), ("WITH", -10m), ("DEPO", 6.36m),
        };
        var c = PnlReconciliation.ReconcileRealisedTradingTypes(txns, 96.36m);
        Assert.False(c.Ok);
        Assert.Equal(PnlReconciliation.SeverityError, c.Severity);
        Assert.Contains("leaked in", c.Detail);
        Assert.Contains("DEPO", c.Detail);
    }

    [Fact]
    public void RealisedTypes_ignores_zero_pnl_non_trading_rows()
    {
        // A DEPO carrying 0 P&L is harmless and must not trip the guard.
        var txns = new (string?, decimal)[]
        {
            ("DEAL", 50m), ("DEPO", 0m), ("TRANSFER", 0m),
        };
        Assert.True(PnlReconciliation.ReconcileRealisedTradingTypes(txns, 50m).Ok);
    }

    [Theory]
    [InlineData("DEAL", true)]
    [InlineData("with", true)]   // case-insensitive
    [InlineData("DEPO", false)]
    [InlineData("TRANSFER", false)]
    [InlineData(null, false)]
    public void IsTradingType_recognises_only_DEAL_and_WITH(string? type, bool expected)
        => Assert.Equal(expected, PnlReconciliation.IsTradingType(type));

    // ── 4. no_zero_cost_basis ────────────────────────────────────────────
    [Fact]
    public void ZeroCostBasis_passes_when_every_held_position_has_a_price()
    {
        var pos = new (string, decimal, decimal?)[]
        {
            ("AAPL", 5m, 180.5m), ("MSFT", 3m, 410m),
        };
        Assert.True(PnlReconciliation.ReconcileNoZeroCostBasis(pos).Ok);
    }

    [Fact]
    public void ZeroCostBasis_fails_and_lists_the_phantom_fill()
    {
        // Held 12 of GHOST at avgPrice 0 → phantom fill.
        var pos = new (string, decimal, decimal?)[]
        {
            ("AAPL", 5m, 180.5m), ("GHOST", 12m, 0m),
        };
        var c = PnlReconciliation.ReconcileNoZeroCostBasis(pos);
        Assert.False(c.Ok);
        Assert.Equal(PnlReconciliation.SeverityError, c.Severity);
        Assert.Contains("GHOST", c.Detail);
        Assert.DoesNotContain("AAPL", c.Detail);
    }

    [Fact]
    public void ZeroCostBasis_ignores_zero_qty_and_null_price()
    {
        // qty 0 at price 0 is a closed line, not a phantom fill; null price is
        // "broker omitted it", not zero → neither should fail.
        var pos = new (string, decimal, decimal?)[]
        {
            ("CLOSED", 0m, 0m), ("NOPRICE", 4m, (decimal?)null),
        };
        Assert.True(PnlReconciliation.ReconcileNoZeroCostBasis(pos).Ok);
    }

    // ── 5. all_held_symbols_resolve ──────────────────────────────────────
    [Fact]
    public void HeldSymbolsResolve_passes_when_all_resolve()
    {
        var held = new[] { "AAPL", "MSFT" };
        var c = PnlReconciliation.ReconcileHeldSymbolsResolve(held, _ => true);
        Assert.True(c.Ok);
    }

    [Fact]
    public void HeldSymbolsResolve_warns_and_lists_the_stranded_holding()
    {
        // LUK (delisted) doesn't resolve → WARN, named. Note: severity is WARN,
        // not ERROR — the position is stranded but no number is wrong.
        var held = new[] { "AAPL", "LUK" };
        bool Resolves(string s) => s != "LUK";
        var c = PnlReconciliation.ReconcileHeldSymbolsResolve(held, Resolves);
        Assert.False(c.Ok);
        Assert.Equal(PnlReconciliation.SeverityWarn, c.Severity);
        Assert.Contains("LUK", c.Detail);
        Assert.DoesNotContain("AAPL", c.Detail);
    }

    // ── envelope ─────────────────────────────────────────────────────────
    [Fact]
    public void Summarise_overall_ok_only_when_no_check_failed()
    {
        var pass = PnlReconciliation.ReconcileOpenPnl(0m, 0m, 25m);                 // ok
        var fail = PnlReconciliation.ReconcileOpenPnl(1000m, 0m, 25m);             // fail
        var skip = PnlReconciliation.ReconCheck.Skipped("x", "source down");       // skip

        var clean = PnlReconciliation.Summarise(new[] { pass, skip });
        Assert.True(clean.Ok);          // a skip does NOT fail the report …
        Assert.Equal(1, clean.Skipped); // … but is counted so coverage is honest

        var broken = PnlReconciliation.Summarise(new[] { pass, fail, skip });
        Assert.False(broken.Ok);
        Assert.Equal(1, broken.Passed);
        Assert.Equal(1, broken.Failed);
        Assert.Equal(1, broken.Skipped);
    }

    [Fact]
    public void Skipped_check_is_tri_state_null_not_false()
    {
        // A skipped check must never read as a pass OR a fail.
        var skip = PnlReconciliation.ReconCheck.Skipped("open_pnl_reconciles", "T212 down");
        Assert.Null(skip.Ok);
        Assert.Equal(PnlReconciliation.SeverityInfo, skip.Severity);
        Assert.Contains("T212 down", skip.Detail);
    }
}

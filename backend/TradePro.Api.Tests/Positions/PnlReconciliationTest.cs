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
    public void RealisedTypes_flags_latent_non_trading_pnl_even_when_total_is_clean()
    {
        // Reported total correctly excludes the DEPO (= 90), but a DEPO with
        // P&L is present → flag the latent leak so it can't silently start
        // corrupting the figure later.
        var txns = new (string?, decimal)[]
        {
            ("DEAL", 100m), ("WITH", -10m), ("DEPO", 5000m),
        };
        var c = PnlReconciliation.ReconcileRealisedTradingTypes(txns, 90m);
        Assert.False(c.Ok);
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

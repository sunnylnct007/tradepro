using System.Text.RegularExpressions;
using Dapper;
using Npgsql;
using TradePro.Api.Oms;
using TradePro.Api.Positions;
using TradePro.Api.Providers.Trading212;

namespace TradePro.Api.Endpoints;

/// <summary>
/// /api/pnl/reconciliation — the data-trust report. Phase 2 of the
/// trustworthy-data plan: make P&amp;L / position correctness PROVABLE by
/// checking invariants against the broker (golden source) and FAILING LOUDLY
/// when they don't hold.
///
/// Read-only diagnostic. Always returns a 200 with a report envelope — every
/// check DEGRADES GRACEFULLY: a data source being down marks that one check
/// `skipped` (ok=null) with a reason instead of throwing, so the operator
/// always gets a report and can see exactly what could and couldn't be
/// verified. The numeric / list comparisons live in the pure, unit-tested
/// <see cref="PnlReconciliation"/> core; this file is just plumbing — fetch
/// from the golden source, hand the numbers to the core.
/// </summary>
public static class ReconciliationEndpoints
{
    public static IEndpointRouteBuilder MapReconciliationEndpoints(this IEndpointRouteBuilder app)
    {
        // GET /api/pnl/reconciliation — run every invariant against T212 demo
        // (the algo's primary equity broker) + the OMS ledger + IG realised
        // history. Defaults to demo because that's where the strategy books;
        // ?account=live targets the live T212 connection instead.
        app.MapGet("/pnl/reconciliation", async (
            string? account,
            Trading212DemoClient t212Demo,
            Trading212Client t212Live,
            Trading212DemoCashCache t212DemoCash,
            Trading212DemoPositionsCache t212DemoPositions,
            Trading212PositionsCache t212LivePositions,
            TradePro.Api.Providers.IG.IGClient ig,
            IOmsService oms,
            NpgsqlDataSource db,
            CancellationToken ct) =>
        {
            var useDemo = !string.Equals(account, "live", StringComparison.OrdinalIgnoreCase);
            var t212Label = useDemo ? "T212_DEMO" : "T212_LIVE";
            var checks = new List<PnlReconciliation.ReconCheck>();

            // ── Fetch T212 positions + cash once (shared across checks 1/2/4/5).
            // A failed/disabled fetch leaves these null → the dependent checks
            // each skip with the reason rather than the whole endpoint throwing.
            Trading212PositionsResult? t212Pos = null;
            string? t212PosError = null;
            try
            {
                var enabled = useDemo ? t212Demo.IsEnabled : t212Live.IsEnabled;
                if (!enabled)
                    t212PosError = $"{t212Label} client disabled";
                else
                    t212Pos = useDemo
                        ? await t212DemoPositions.GetAsync(ct)
                        : await t212LivePositions.GetAsync(ct);
                if (t212Pos?.Error is not null)
                    t212PosError = $"{t212Label} positions error: {t212Pos.Error}";
            }
            catch (Exception ex)
            {
                t212PosError = $"{t212Label} positions fetch threw: {ex.Message}";
            }

            // Cash (account-level open P&L) — demo only today; live cash isn't
            // wired on the live client yet (same limitation cash-summary notes).
            Trading212CashResult? t212Cash = null;
            string? t212CashError = null;
            try
            {
                if (useDemo && t212Demo.IsEnabled)
                {
                    t212Cash = await t212DemoCash.GetAsync(ct);
                    if (t212Cash.Error is not null)
                        t212CashError = $"T212_DEMO cash error: {t212Cash.Error}";
                }
                else if (!useDemo)
                {
                    t212CashError = "live T212 cash fetch not wired yet (demo only)";
                }
                else
                {
                    t212CashError = "T212_DEMO client disabled";
                }
            }
            catch (Exception ex)
            {
                t212CashError = $"T212 cash fetch threw: {ex.Message}";
            }

            // ── 1. open_pnl_reconciles ──────────────────────────────────────
            if (t212Pos is null)
                checks.Add(PnlReconciliation.ReconCheck.Skipped(
                    "open_pnl_reconciles", t212PosError ?? "T212 positions unavailable"));
            else if (t212Cash?.Ppl is not decimal accountPpl)
                checks.Add(PnlReconciliation.ReconCheck.Skipped(
                    "open_pnl_reconciles",
                    t212CashError ?? "T212 account Ppl unavailable"));
            else
            {
                // Per-position unrealised: prefer the broker's OWN Ppl per row
                // (the golden number that sums to account Ppl); when a row omits
                // it, fall back to (currentPrice − avg) × qty — the same guarded
                // fallback IntegrationsEndpoints uses (commit 0d27e20) — instead
                // of skipping the whole check. Rows with neither Ppl nor a usable
                // quote are excluded with a note. If the computed Σ still doesn't
                // reconcile to the account Ppl the check FAILS honestly (T212's
                // account Ppl can carry fxPpl/pie effects our sum can't see).
                var markRows = t212Pos.Positions.Select(p =>
                    (Sym: p.Instrument?.Ticker ?? p.Ticker ?? "(null)",
                     p.Quantity, p.AveragePricePaid, p.CurrentPrice, p.Ppl));
                var sumPositions = PnlReconciliation.SumOpenPnlMarks(markRows, out var markNote);
                var tol = PnlReconciliation.OpenPnlTolerance(accountPpl);
                checks.Add(PnlReconciliation.ReconcileOpenPnl(
                    sumPositions, accountPpl, tol, markNote));
            }

            // ── 2. oms_matches_broker ───────────────────────────────────────
            if (t212Pos is null)
                checks.Add(PnlReconciliation.ReconCheck.Skipped(
                    "oms_matches_broker", t212PosError ?? "T212 positions unavailable"));
            else
            {
                List<OmsPosition>? omsRows = null;
                try
                {
                    omsRows = (await oms.ListPositionsAsync(null))
                        .Where(p => p.Broker == t212Label)
                        .ToList();
                }
                catch (Exception ex)
                {
                    checks.Add(PnlReconciliation.ReconCheck.Skipped(
                        "oms_matches_broker", $"OMS positions unavailable: {ex.Message}"));
                }
                if (omsRows is not null)
                {
                    var omsPairs = omsRows.Select(p => (p.Symbol, p.Quantity));
                    var brokerPairs = t212Pos.Positions
                        .Select(p => (Sym: p.Instrument?.Ticker ?? p.Ticker ?? "", p.Quantity));

                    // Resolve each OMS source ticker through the broker_ticker_map
                    // (source_ticker → broker_ticker) BEFORE bare-keying, so an
                    // OMS "JEF" maps to the broker's stale "LUK_US_EQ" → bare
                    // "LUK" and reconciles instead of reporting phantom drift.
                    // No hardcoded map: read it from broker_ticker_map for this
                    // broker; missing rows fall through to plain bare-keying.
                    Dictionary<string, string> srcToBroker = new(StringComparer.OrdinalIgnoreCase);
                    try
                    {
                        await using var conn = await db.OpenConnectionAsync(ct);
                        var rows = await conn.QueryAsync<(string source_ticker, string broker_ticker)>(@"
                            SELECT source_ticker, broker_ticker FROM broker_ticker_map
                            WHERE broker = @broker
                              AND source_ticker IS NOT NULL AND broker_ticker IS NOT NULL;",
                            new { broker = t212Label });
                        foreach (var (src, bt) in rows)
                            if (!string.IsNullOrWhiteSpace(src) && !string.IsNullOrWhiteSpace(bt))
                                srcToBroker[src.Trim()] = bt.Trim();
                    }
                    catch
                    {
                        // Fail open: no map → plain bare-keying (genuine drift
                        // still caught; JEF/LUK simply won't auto-resolve).
                    }

                    string ResolveBrokerTicker(string oms) =>
                        srcToBroker.TryGetValue((oms ?? "").Trim(), out var bt) ? bt : (oms ?? "");

                    checks.Add(PnlReconciliation.ReconcileOmsVsBroker(
                        omsPairs, brokerPairs, epsilon: 0.0001m,
                        bareKey: ReconcileMath.Bare,
                        resolveBrokerTicker: ResolveBrokerTicker));
                }
            }

            // ── 3. realised_trading_types_only ──────────────────────────────
            // Regression guard for the £9.99M DEPO-leak bug. Pull IG's raw
            // transaction history (DEAL + WITH + DEPO + …), recompute the
            // realised total over trading types only, and prove the reported
            // total excludes funding. IG is the realised source here (the OMS
            // holds no IG fills); T212 realised history is a separate roadmap.
            try
            {
                if (!ig.IsEnabled)
                    checks.Add(PnlReconciliation.ReconCheck.Skipped(
                        "realised_trading_types_only", "IG client disabled"));
                else
                {
                    var to = DateOnly.FromDateTime(DateTime.UtcNow);
                    var from = to.AddDays(-30);
                    var hist = await ig.GetTransactionHistoryAsync(from, to, ct);
                    if (hist.Error is not null)
                        checks.Add(PnlReconciliation.ReconCheck.Skipped(
                            "realised_trading_types_only", $"IG history error: {hist.Error}"));
                    else
                    {
                        // The endpoint's published realised total is Σ over
                        // trading types — recompute it from the SAME raw rows
                        // the report would publish, exactly as the live
                        // ig/history endpoint does (IntegrationsEndpoints).
                        var reported = hist.Transactions
                            .Where(t => PnlReconciliation.IsTradingType(t.Type))
                            .Sum(t => t.ProfitAndLoss);
                        var all = hist.Transactions.Select(t => (t.Type, t.ProfitAndLoss));
                        checks.Add(PnlReconciliation.ReconcileRealisedTradingTypes(all, reported));
                    }
                }
            }
            catch (Exception ex)
            {
                checks.Add(PnlReconciliation.ReconCheck.Skipped(
                    "realised_trading_types_only", $"IG history fetch threw: {ex.Message}"));
            }

            // ── 4. no_zero_cost_basis ───────────────────────────────────────
            if (t212Pos is null)
                checks.Add(PnlReconciliation.ReconCheck.Skipped(
                    "no_zero_cost_basis", t212PosError ?? "T212 positions unavailable"));
            else
            {
                var rows = t212Pos.Positions.Select(p =>
                    (Sym: p.Instrument?.Ticker ?? p.Ticker ?? "(null)",
                     p.Quantity, p.AveragePricePaid));
                checks.Add(PnlReconciliation.ReconcileNoZeroCostBasis(rows));
            }

            // ── 5. all_held_symbols_resolve ─────────────────────────────────
            if (t212Pos is null)
                checks.Add(PnlReconciliation.ReconCheck.Skipped(
                    "all_held_symbols_resolve", t212PosError ?? "T212 positions unavailable"));
            else
            {
                var held = t212Pos.Positions
                    .Where(p => p.Quantity != 0m)
                    .Select(p => ReconcileMath.Bare(p.Instrument?.Ticker ?? p.Ticker ?? ""))
                    .Where(s => !string.IsNullOrWhiteSpace(s))
                    .Distinct(StringComparer.OrdinalIgnoreCase)
                    .ToList();

                // Load the broker_ticker_map for this broker once. A held bare
                // symbol resolves if it has a map entry OR matches the
                // unambiguous heuristic shape (clean US-equity ticker / FX pair).
                // Anything else is stranded/untradeable (e.g. a delisted LUK).
                HashSet<string>? mapped = null;
                try
                {
                    await using var conn = await db.OpenConnectionAsync(ct);
                    var srcs = await conn.QueryAsync<string>(@"
                        SELECT DISTINCT upper(source_ticker) FROM broker_ticker_map
                        WHERE broker = @broker AND source_ticker IS NOT NULL;",
                        new { broker = t212Label });
                    mapped = srcs.ToHashSet(StringComparer.OrdinalIgnoreCase);
                }
                catch (Exception ex)
                {
                    checks.Add(PnlReconciliation.ReconCheck.Skipped(
                        "all_held_symbols_resolve", $"broker_ticker_map unavailable: {ex.Message}"));
                }
                if (mapped is not null)
                {
                    var mappedSet = mapped;
                    bool Resolves(string bare) =>
                        mappedSet.Contains(bare) || HeuristicResolvable(bare);
                    checks.Add(PnlReconciliation.ReconcileHeldSymbolsResolve(held, Resolves));
                }
            }

            var (ok, passed, failed, skipped) = PnlReconciliation.Summarise(checks);
            return Results.Ok(new
            {
                ok,
                utc = DateTime.UtcNow,
                account = useDemo ? "demo" : "live",
                broker = t212Label,
                summary = new { passed, failed, skipped, total = checks.Count },
                checks = checks.Select(c => new
                {
                    name = c.Name,
                    ok = c.Ok,
                    detail = c.Detail,
                    severity = c.Severity,
                }),
            });
        });

        return app;
    }

    // A bare symbol is heuristically tradeable when it is either a clean FX
    // pair (6 letters, e.g. EURUSD) or a plain alphanumeric equity ticker
    // (1–5 chars, e.g. AAPL). Multi-listed / venue-suffixed / oddball shapes
    // that aren't in broker_ticker_map are treated as unresolved so a stranded
    // holding (delisted, wrong venue) surfaces instead of silently "passing".
    private static readonly Regex _fxPair = new(@"^[A-Z]{6}$", RegexOptions.Compiled);
    private static readonly Regex _equity = new(@"^[A-Z]{1,5}$", RegexOptions.Compiled);

    private static bool HeuristicResolvable(string bare)
    {
        if (string.IsNullOrWhiteSpace(bare)) return false;
        var u = bare.Trim().ToUpperInvariant();
        return _fxPair.IsMatch(u) || _equity.IsMatch(u);
    }
}

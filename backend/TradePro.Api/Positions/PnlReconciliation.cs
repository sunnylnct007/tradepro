namespace TradePro.Api.Positions;

/// <summary>
/// Pure, dependency-free reconciliation invariants for the data-trust report
/// (GET /api/pnl/reconciliation). Every method here takes already-fetched
/// numbers / lists and returns a <see cref="ReconCheck"/> — NO DB, NO HTTP, NO
/// clock. That is the whole point: P&amp;L / position correctness becomes
/// PROVABLE because the comparison logic is unit-tested in isolation, and the
/// endpoint is reduced to "fetch from the golden source, hand the numbers to
/// these helpers".
///
/// The broker is the golden source (see project memory: "Broker is golden
/// source for positions"). When our derived numbers (per-position marks, the
/// OMS ledger, the realised-total math) disagree with the broker, the check
/// FAILS LOUDLY rather than silently papering over the drift — that drift is
/// exactly the class of bug (phantom fills, the £9.99M DEPO leak, stale OMS
/// buckets) this report exists to catch.
/// </summary>
public static class PnlReconciliation
{
    /// <summary>Severity of a failed (or skipped) check, so the cockpit can
    /// colour-code and the operator can triage. ERROR = a correctness
    /// invariant is broken (act now); WARN = drift worth eyeballing but not
    /// necessarily a money bug; INFO = informational / skipped.</summary>
    public const string SeverityError = "error";
    public const string SeverityWarn = "warn";
    public const string SeverityInfo = "info";

    /// <summary>One row of the reconciliation report.
    /// <para/>
    /// <see cref="Ok"/> is tri-state: true = invariant holds, false = it is
    /// VIOLATED, null = the check was SKIPPED (a data source was unavailable).
    /// A skipped check must never read as a pass — the endpoint degrades
    /// gracefully but stays honest about what it could not verify.</summary>
    public readonly record struct ReconCheck(
        string Name,
        bool? Ok,
        string Detail,
        string Severity)
    {
        /// <summary>A check we could not run because its data source was down /
        /// disabled. ok=null, severity=info, with the reason in detail.</summary>
        public static ReconCheck Skipped(string name, string reason) =>
            new(name, null, $"skipped — {reason}", SeverityInfo);
    }

    // ── 1. open_pnl_reconciles ───────────────────────────────────────────
    /// <summary>
    /// |Σ(per-position unrealisedAbs) − account-level open P&amp;L| ≤ tolerance.
    /// FAIL → our per-position marks disagree with the broker's own account
    /// P&amp;L, i.e. we are mis-pricing at least one holding. Tolerance is
    /// supplied by the caller (typically max($25, 2% of |accountPpl|)).
    /// </summary>
    public static ReconCheck ReconcileOpenPnl(
        decimal sumPositions, decimal accountPpl, decimal tolerance, string? note = null)
    {
        var diff = sumPositions - accountPpl;
        var ok = Math.Abs(diff) <= Math.Abs(tolerance);
        var detail =
            $"Σ per-position unrealised={sumPositions:0.##}, account Ppl={accountPpl:0.##}, "
            + $"diff={diff:0.##}, tolerance=±{Math.Abs(tolerance):0.##}";
        if (!string.IsNullOrWhiteSpace(note))
            detail += $" [{note}]";
        // When the computed Σ does NOT reconcile to the account Ppl, FAIL with
        // both numbers + the diff (do NOT paper over it). T212's account Ppl
        // may include fxPpl / pie effects our per-position sum can't see; that
        // honest gap is exactly what this check exists to surface.
        return new ReconCheck(
            "open_pnl_reconciles", ok, detail,
            ok ? SeverityInfo : SeverityError);
    }

    /// <summary>
    /// Sum each position's open-P&amp;L mark for <see cref="ReconcileOpenPnl"/>,
    /// preferring the broker's OWN per-row Ppl (golden — sums to account Ppl by
    /// construction) and falling back to <c>(currentPrice − avgPrice) × qty</c>
    /// when a row omits it. The fallback is GUARDED: it only applies when
    /// avgPrice &gt; 0 AND currentPrice is present — a missing quote would mark
    /// the holding to $0 and fabricate a phantom loss (the LUK class of bug),
    /// so such rows are EXCLUDED from the sum and named in <paramref name="note"/>
    /// instead. Mirrors the per-position mark logic in
    /// IntegrationsEndpoints (commit 0d27e20).
    /// </summary>
    /// <param name="positions">(symbol, qty, avgPrice, currentPrice, ppl) per
    /// held position. ppl is the broker's own per-row open P&amp;L when present.</param>
    /// <param name="note">Out: human-readable summary of how many rows used the
    /// broker Ppl, how many used the fallback, and which were excluded.</param>
    /// <returns>Σ of the marks we could compute.</returns>
    public static decimal SumOpenPnlMarks(
        IEnumerable<(string Sym, decimal Qty, decimal? AvgPrice, decimal? CurrentPrice, decimal? Ppl)> positions,
        out string note)
    {
        decimal sum = 0m;
        int usedPpl = 0, usedFallback = 0;
        var excluded = new List<string>();

        foreach (var p in positions)
        {
            if (p.Ppl is decimal ppl)
            {
                sum += ppl;
                usedPpl++;
            }
            else if (p.AvgPrice is decimal avg && avg > 0m && p.CurrentPrice is decimal cur)
            {
                sum += (cur - avg) * p.Qty;
                usedFallback++;
            }
            else
            {
                // No broker Ppl and no usable quote → excluding it (marking to $0
                // would fabricate a phantom loss). Surfaced honestly in the note.
                excluded.Add(string.IsNullOrWhiteSpace(p.Sym) ? "(null)" : p.Sym);
            }
        }

        note = $"{usedPpl} row(s) via broker Ppl, {usedFallback} via "
            + "(currentPrice−avg)×qty fallback"
            + (excluded.Count > 0
                ? $", {excluded.Count} excluded (no Ppl + no usable quote): "
                  + string.Join(", ", excluded.OrderBy(s => s))
                : "");
        return sum;
    }

    /// <summary>The tolerance band for <see cref="ReconcileOpenPnl"/>:
    /// max(absoluteFloor, pct × |accountPpl|). A floor (e.g. $25) absorbs
    /// rounding / FX-conversion noise when the account P&amp;L is tiny; the
    /// percentage scales it for a large book.</summary>
    public static decimal OpenPnlTolerance(
        decimal accountPpl, decimal absoluteFloor = 25m, decimal pct = 0.02m)
        => Math.Max(absoluteFloor, Math.Abs(accountPpl) * pct);

    // ── 2. oms_matches_broker ────────────────────────────────────────────
    /// <summary>
    /// For each symbol, Σ(OMS position qty across strategies) vs the broker's
    /// position qty. Any |diff| &gt; epsilon is drift — an unattributed or stale
    /// ledger entry (the OMS recorded a fill the broker never accepted, or a
    /// manual trade bypassed the OMS). FAIL listing every offending symbol.
    /// <para/>
    /// Symbols are compared on their BARE key (so a T212 "AAPL_US_EQ" and an
    /// IG "CS.D.EURUSD.MINI.IP" line up with the OMS bare symbol) via the
    /// caller-supplied <paramref name="bareKey"/> — we keep this file free of
    /// the broker-format knowledge that lives in ReconcileMath.Bare.
    /// <para/>
    /// OMS symbols are first run through <paramref name="resolveBrokerTicker"/>
    /// (the broker_ticker_map: source_ticker → broker_ticker) BEFORE bare-keying.
    /// This is what makes the JEF↔LUK pair reconcile: our OMS recorded the
    /// position as "JEF" (what the strategy ordered) but T212 holds it under the
    /// stale ticker "LUK_US_EQ" (Leucadia→Jefferies rename). Mapping OMS "JEF" →
    /// "LUK_US_EQ" → bare "LUK" lines it up with the broker's LUK instead of
    /// reporting phantom drift (JEF oms=17/broker=0 + LUK oms=0/broker=17).
    /// When no map row exists the resolver returns the symbol unchanged, so we
    /// fall back to plain bare-keying and genuine drift is still caught.
    /// </summary>
    public static ReconCheck ReconcileOmsVsBroker(
        IEnumerable<(string Sym, decimal OmsQty)> omsPositions,
        IEnumerable<(string Sym, decimal BrokerQty)> brokerPositions,
        decimal epsilon,
        Func<string, string>? bareKey = null,
        Func<string, string>? resolveBrokerTicker = null)
    {
        bareKey ??= s => (s ?? "").Trim().ToUpperInvariant();
        resolveBrokerTicker ??= s => s;

        // OMS side: resolve the source ticker to the broker's ticker (via the
        // broker_ticker_map) first, THEN bare-key — so OMS "JEF" → "LUK_US_EQ" →
        // "LUK" matches the broker's "LUK".
        var oms = omsPositions
            .GroupBy(p => bareKey(resolveBrokerTicker(p.Sym)))
            .Where(g => !string.IsNullOrWhiteSpace(g.Key))
            .ToDictionary(g => g.Key, g => g.Sum(p => p.OmsQty));
        var broker = brokerPositions
            .GroupBy(p => bareKey(p.Sym))
            .Where(g => !string.IsNullOrWhiteSpace(g.Key))
            .ToDictionary(g => g.Key, g => g.Sum(p => p.BrokerQty));

        var drifts = new List<string>();
        foreach (var sym in oms.Keys.Union(broker.Keys).OrderBy(s => s))
        {
            var o = oms.GetValueOrDefault(sym, 0m);
            var b = broker.GetValueOrDefault(sym, 0m);
            var d = o - b;
            if (Math.Abs(d) > Math.Abs(epsilon))
                drifts.Add($"{sym}: oms={o:0.####} broker={b:0.####} diff={d:0.####}");
        }

        var ok = drifts.Count == 0;
        var detail = ok
            ? $"all {oms.Keys.Union(broker.Keys).Count()} symbol(s) within ±{Math.Abs(epsilon):0.####}"
            : $"{drifts.Count} symbol(s) drifted: {string.Join("; ", drifts)}";
        return new ReconCheck(
            "oms_matches_broker", ok, detail,
            ok ? SeverityInfo : SeverityError);
    }

    // ── 3. realised_trading_types_only ───────────────────────────────────
    /// <summary>
    /// Regression guard for the £9.99M bug: the realised total MUST be summed
    /// over TRADING transaction types only (DEAL + WITH), excluding account
    /// funding (DEPO) and other cash movements. We recompute Σ over the
    /// trading rows and assert it equals the reported total. If a non-trading
    /// type leaked into the reported figure, the recompute won't match and we
    /// FAIL, naming the leaked types.
    /// <para/>
    /// The mere PRESENCE of non-trading rows (DEPO funding, transfers) is fine —
    /// indeed expected on a funded account — as long as they are EXCLUDED from
    /// the realised total. Those excluded rows are surfaced as an informational
    /// note in the detail, NOT a failure. The check only FAILS when a
    /// non-trading type actually leaked INTO the realised figure (reported ≠
    /// Σ(DEAL+WITH)).
    /// </summary>
    /// <param name="transactions">(type, profitAndLoss) for EVERY transaction
    /// the source returned — trading AND non-trading.</param>
    /// <param name="reportedTotal">The realised total as the endpoint would
    /// publish it.</param>
    /// <param name="tolerance">Money-rounding slack (default 1 cent).</param>
    public static ReconCheck ReconcileRealisedTradingTypes(
        IEnumerable<(string? Type, decimal Pnl)> transactions,
        decimal reportedTotal,
        decimal tolerance = 0.01m)
    {
        var txns = transactions.ToList();

        var tradingTotal = txns.Where(t => IsTradingType(t.Type)).Sum(t => t.Pnl);

        // Non-trading rows that carry P&L (DEPO funding, transfers). Their mere
        // PRESENCE is fine — expected on a funded account — as long as they are
        // EXCLUDED from realised (which they are: realised sums trading types
        // only). Surfaced as an informational note, NOT a failure. The only
        // failure mode is one of these actually LEAKING into the realised total.
        var nonTradingTypes = txns
            .Where(t => !IsTradingType(t.Type) && t.Pnl != 0m)
            .GroupBy(t => (t.Type ?? "(null)").ToUpperInvariant())
            .Select(g => $"{g.Key}:{g.Sum(x => x.Pnl):0.##}")
            .OrderBy(s => s)
            .ToList();

        // PASS ⟺ the reported total equals Σ(DEAL+WITH). If they match, the
        // non-trading rows were correctly excluded — regardless of how many
        // exist or how big they are.
        var ok = Math.Abs(reportedTotal - tradingTotal) <= Math.Abs(tolerance);

        var excludedNote = nonTradingTypes.Count > 0
            ? $"; non-trading rows present and correctly EXCLUDED: {string.Join(", ", nonTradingTypes)}"
            : "; no non-trading P&L present";

        var detail = ok
            ? $"realised={reportedTotal:0.##} equals Σ(DEAL+WITH)={tradingTotal:0.##}{excludedNote}"
            : $"reported realised={reportedTotal:0.##} ≠ Σ(DEAL+WITH)={tradingTotal:0.##} "
              + $"(diff={reportedTotal - tradingTotal:0.##}) — a non-trading type leaked in"
              + (nonTradingTypes.Count > 0 ? $"; non-trading P&L: {string.Join(", ", nonTradingTypes)}" : "");

        return new ReconCheck(
            "realised_trading_types_only", ok, detail,
            ok ? SeverityInfo : SeverityError);
    }

    /// <summary>Realised P&amp;L counts TRADING transactions only: DEAL (trade
    /// P&amp;L) + WITH (financing / commission / admin on trades). Everything
    /// else (DEPO funding, transfers) is a cash movement, NOT P&amp;L. Mirrors
    /// IntegrationsEndpoints.IsTradingTxn — kept here so the guard is pure /
    /// testable.</summary>
    public static bool IsTradingType(string? type) =>
        string.Equals(type, "DEAL", StringComparison.OrdinalIgnoreCase)
        || string.Equals(type, "WITH", StringComparison.OrdinalIgnoreCase);

    // ── 4. no_zero_cost_basis ────────────────────────────────────────────
    /// <summary>
    /// No held position (quantity ≠ 0) may have averagePricePaid == 0 — that is
    /// the phantom-fill class (a position booked without a real entry price,
    /// which then marks to a fabricated P&amp;L). FAIL listing every offender.
    /// A null averagePricePaid is treated as "not provided" (skipped per row),
    /// NOT as zero, so a broker that simply omits the field doesn't false-fail.
    /// </summary>
    public static ReconCheck ReconcileNoZeroCostBasis(
        IEnumerable<(string Sym, decimal Qty, decimal? AvgPrice)> positions)
    {
        var offenders = positions
            .Where(p => p.Qty != 0m && p.AvgPrice is decimal a && a == 0m)
            .Select(p => $"{p.Sym} (qty={p.Qty:0.####})")
            .OrderBy(s => s)
            .ToList();

        var ok = offenders.Count == 0;
        var detail = ok
            ? "no held position has a zero cost basis"
            : $"{offenders.Count} position(s) with qty≠0 but avgPrice==0 (phantom fills): "
              + string.Join(", ", offenders);
        return new ReconCheck(
            "no_zero_cost_basis", ok, detail,
            ok ? SeverityInfo : SeverityError);
    }

    // ── 5. all_held_symbols_resolve ──────────────────────────────────────
    /// <summary>
    /// Every held broker symbol must be tradeable: it has a broker_ticker_map
    /// entry OR resolves via the heuristic without ambiguity. A held symbol
    /// that resolves to nothing is untradeable (e.g. a delisted LUK we still
    /// "hold" but can never exit through the normal path) — surface it so the
    /// operator knows the position is stranded. FAIL listing offenders.
    /// </summary>
    /// <param name="heldSymbols">Bare symbols currently held at the broker.</param>
    /// <param name="resolves">Returns true when the symbol is resolvable
    /// (mapped or cleanly heuristic-derivable). Supplied by the caller, which
    /// owns the map + heuristic.</param>
    public static ReconCheck ReconcileHeldSymbolsResolve(
        IEnumerable<string> heldSymbols,
        Func<string, bool> resolves)
    {
        var held = heldSymbols
            .Where(s => !string.IsNullOrWhiteSpace(s))
            .Select(s => s.Trim())
            .Distinct(StringComparer.OrdinalIgnoreCase)
            .ToList();

        var unresolved = held.Where(s => !resolves(s)).OrderBy(s => s).ToList();

        var ok = unresolved.Count == 0;
        var detail = ok
            ? $"all {held.Count} held symbol(s) resolve to a tradeable instrument"
            : $"{unresolved.Count} held symbol(s) unresolved/untradeable: "
              + string.Join(", ", unresolved);
        return new ReconCheck(
            "all_held_symbols_resolve", ok, detail,
            // Untradeable holdings are a WARN, not an ERROR: the position is
            // stranded but no number is wrong. Distinct from the P&L-integrity
            // ERRORs above so triage can prioritise.
            ok ? SeverityInfo : SeverityWarn);
    }

    /// <summary>Roll the individual checks into the report envelope. overall
    /// `ok` is true only when NO check is an outright failure (ok==false);
    /// SKIPPED checks (ok==null) don't fail the report but are reported, and
    /// counted, so the caller can see coverage was incomplete.</summary>
    public static (bool Ok, int Passed, int Failed, int Skipped) Summarise(
        IEnumerable<ReconCheck> checks)
    {
        var list = checks.ToList();
        var failed = list.Count(c => c.Ok == false);
        var passed = list.Count(c => c.Ok == true);
        var skipped = list.Count(c => c.Ok is null);
        return (failed == 0, passed, failed, skipped);
    }
}

using Dapper;
using Npgsql;
using TradePro.Api.Oms;
using TradePro.Api.Positions;
using TradePro.Api.Providers.Trading212;

namespace TradePro.Api.Endpoints;

/// <summary>
/// GET /api/pnl/by-strategy — one row per strategy so the trader can RANK the
/// sleeves side-by-side (intraday_flat vs ichimoku_equity vs ichimoku_fx_mr).
///
/// The sleeves live on different brokers with different P&amp;L availability, so
/// the honest answer per field is sometimes "n/a". This endpoint NEVER
/// fabricates: when a component genuinely isn't obtainable it returns null and
/// puts the reason in <c>notes</c>. Each source is wrapped in try/catch; a
/// failure degrades that one field to null + note rather than throwing.
///
/// Sources (reusing the patterns in IntegrationsEndpoints):
///   • strategy→broker — strategy_broker_map (config-driven, never hardcoded).
///   • Realised, IG strategies — IG closed-deal history attributed by asset
///     class (FX vs EQUITY), exactly as /integrations/ig/history does (the OMS
///     holds no IG fills, so it can't attribute them).
///   • Realised, T212 strategies — replayed from the OMS fill ledger via a
///     long-only FIFO match (PnlByStrategy.FifoRealised). T212 has no realised
///     FEED; the ledger is the only provable source.
///   • Open, T212 — Σ the broker's own per-position Ppl, for the symbols the
///     OMS attributes to that strategy (T212 fills ARE in the OMS).
///   • Open, IG — Σ the IG per-position unrealised, attributed by asset class.
///
/// Currencies are NEVER blended: each row carries its broker's native currency
/// (T212 → USD, IG → GBP) and total = open + realised only within that one
/// currency, only when both halves are present.
/// </summary>
public static class PnlByStrategyEndpoints
{
    public static IEndpointRouteBuilder MapPnlByStrategyEndpoints(this IEndpointRouteBuilder app)
    {
        app.MapGet("/pnl/by-strategy", async (
            int? days,
            Trading212DemoClient t212Demo,
            Trading212DemoPositionsCache t212DemoPositions,
            TradePro.Api.Providers.IG.IGClient ig,
            IOmsService oms,
            IConfiguration config,
            NpgsqlDataSource db,
            CancellationToken ct) =>
        {
            // Realised window for IG (closed-deal history). T212 realised is
            // life-to-date from the full ledger and ignores this.
            var window = Math.Clamp(days ?? 3650, 1, 3650);

            // ── 1. strategy → broker (config-driven) ───────────────────────
            // Read the map; if it's unavailable we can't attribute anything, so
            // return an explicit error envelope rather than guessing.
            var map = new List<(string StrategyId, string Broker)>();
            string? mapError = null;
            try
            {
                await using var conn = await db.OpenConnectionAsync(ct);
                var rows = await conn.QueryAsync<(string strategy_id, string broker)>(
                    "SELECT strategy_id, broker FROM strategy_broker_map ORDER BY strategy_id");
                map.AddRange(rows.Select(r => (r.strategy_id, r.broker)));
            }
            catch (Exception ex)
            {
                mapError = $"strategy_broker_map unavailable: {ex.Message}";
            }
            if (mapError is not null)
                return Results.Ok(new { utc = DateTime.UtcNow, error = mapError, rows = Array.Empty<object>() });

            // Native currency per broker. IG demo/live account is denominated in
            // GBP; T212's algo equity account in USD. (Used only to LABEL the
            // row's numbers — we never convert/blend across currencies.)
            // Config-driven (BrokerCurrencies in appsettings) — was a hardcoded
            // "IG ? GBP : USD" that rendered T212 (Ichimoku Equity) P&L in $ on a
            // GBP account. Update the currency centrally in config, not here.
            string CurrencyFor(string broker) =>
                TradePro.Api.Configuration.BrokerCurrencies.Resolve(config, broker);

            // ── 2. shared fetches (each failure → dependent fields null) ────
            // OMS positions — the only ledger tagged with which strategy holds a
            // symbol. T212 open is attributed through this; IG has no OMS fills.
            List<OmsPosition>? omsPos = null;
            string? omsError = null;
            try { omsPos = (await oms.ListPositionsAsync(null)).ToList(); }
            catch (Exception ex) { omsError = $"OMS positions unavailable: {ex.Message}"; }

            // T212 demo per-position Ppl (the broker's OWN open P&L).
            Trading212PositionsResult? t212 = null;
            string? t212Error = null;
            try
            {
                if (t212Demo.IsEnabled)
                {
                    t212 = await t212DemoPositions.GetAsync(ct);
                    if (t212.Error is not null) t212Error = $"T212 positions error: {t212.Error}";
                }
                else t212Error = "T212 demo client disabled";
            }
            catch (Exception ex) { t212Error = $"T212 positions fetch threw: {ex.Message}"; }

            // IG per-position unrealised + asset class. Marks come from the
            // /positions market block; equity CFDs ("…CASH.IP") and FX both
            // carry bid/offer, so both get a mark — equity CFD open is NOT
            // structurally missing (see the panel notes).
            var igOpenByClass = new Dictionary<string, decimal>(StringComparer.OrdinalIgnoreCase);
            int igMarkable = 0, igUnmarkable = 0;
            string? igPosError = null;
            try
            {
                if (!ig.IsEnabled) igPosError = "IG client disabled";
                else
                {
                    var res = await ig.GetPositionsAsync(ct);
                    if (res.Error is not null) igPosError = $"IG positions error: {res.Error}";
                    foreach (var p in res.Positions)
                    {
                        var cls = IntegrationsEndpoints.AssetClassOf(p.InstrumentName ?? p.Epic) ?? "EQUITY";
                        decimal? mid = (p.Bid is decimal b && p.Offer is decimal o) ? (b + o) / 2m : null;
                        if (mid is decimal m && p.EntryLevel > 0m)
                        {
                            var signedQty = p.Direction == "SELL" ? -p.Size : p.Size;
                            var lot = p.LotSize ?? 1m;
                            // Quote-ccy P&L. We deliberately do NOT cross-convert
                            // here (the row is labelled in the account ccy and IG
                            // demo equity CFDs quote in GBP already); FX cross-ccy
                            // is a known follow-up the ig/positions endpoint owns.
                            igOpenByClass[cls] = igOpenByClass.GetValueOrDefault(cls)
                                + (m - p.EntryLevel) * signedQty * lot;
                            igMarkable++;
                        }
                        else igUnmarkable++;
                    }
                }
            }
            catch (Exception ex) { igPosError = $"IG positions fetch threw: {ex.Message}"; }

            // IG realised history, attributed per strategy (asset-class basis).
            // Reuse the exact attribution + trading-type filter ig/history uses.
            // Per strategy: (realisedLtd over the window, realisedToday for the
            // UTC-today byDay slice, trades, winning). "Today" is attributed to
            // the transaction's own UTC date, exactly the basis ig/history's
            // byDay uses, so the two surfaces agree.
            var igRealisedByStrategy = new Dictionary<string, (decimal Ltd, decimal Today, int Trades, int Winning)>(
                StringComparer.OrdinalIgnoreCase);
            string? igHistError = null;
            try
            {
                if (!ig.IsEnabled) igHistError = "IG client disabled";
                else
                {
                    var to = DateOnly.FromDateTime(DateTime.UtcNow);
                    var from = to.AddDays(-window);
                    var todayKey = to.ToString("yyyy-MM-dd");
                    var hist = await ig.GetTransactionHistoryAsync(from, to, ct);
                    if (hist.Error is not null) igHistError = $"IG history error: {hist.Error}";
                    else
                    {
                        // asset class → owning IG strategy (config + emitted symbols).
                        var classToStrategy = await BuildIgClassMapAsync(db, hist.Transactions.Select(t => t.Instrument), ct);
                        bool IsTrading(string? t) =>
                            string.Equals(t, "DEAL", StringComparison.OrdinalIgnoreCase)
                            || string.Equals(t, "WITH", StringComparison.OrdinalIgnoreCase);
                        // Same date-key derivation ig/history's byDay uses.
                        static string DayKey(string? d) =>
                            d is null ? "" : (d.Length >= 10 ? d[..10] : d);
                        foreach (var tx in hist.Transactions.Where(t => IsTrading(t.Type)))
                        {
                            var cls = IntegrationsEndpoints.AssetClassOf(tx.Instrument);
                            var sid = cls is not null && classToStrategy.TryGetValue(cls, out var s) ? s : null;
                            if (sid is null) continue;
                            var cur = igRealisedByStrategy.GetValueOrDefault(sid);
                            // "trade" + "win" are counted on DEAL rows only (a WITH
                            // financing/commission row isn't a round-trip).
                            var isDeal = string.Equals(tx.Type, "DEAL", StringComparison.OrdinalIgnoreCase);
                            var isToday = DayKey(tx.Date) == todayKey;
                            igRealisedByStrategy[sid] = (
                                cur.Ltd + tx.ProfitAndLoss,
                                cur.Today + (isToday ? tx.ProfitAndLoss : 0m),
                                cur.Trades + (isDeal ? 1 : 0),
                                cur.Winning + (isDeal && tx.ProfitAndLoss > 0m ? 1 : 0));
                        }
                    }
                }
            }
            catch (Exception ex) { igHistError = $"IG history fetch threw: {ex.Message}"; }

            // ── 3. build one row per mapped strategy ───────────────────────
            var outRows = new List<PnlByStrategy.StrategyPnlRow>();
            foreach (var (strategyId, broker) in map)
            {
                var ccy = CurrencyFor(broker);
                var isIg = broker.StartsWith("IG", StringComparison.OrdinalIgnoreCase);
                decimal? open = null, realisedToday = null, realisedLtd = null;
                int trades = 0;
                double? winRate = null;
                var noteParts = new List<string?>();

                if (isIg)
                {
                    // OPEN — Σ IG unrealised for this strategy's asset class.
                    if (igPosError is not null) noteParts.Add($"open: {igPosError}");
                    else
                    {
                        var cls = await AssetClassForIgStrategyAsync(db, strategyId, map, ct);
                        if (cls is not null && igOpenByClass.TryGetValue(cls, out var o)) open = o;
                        else if (cls is null) noteParts.Add("open: could not derive this strategy's asset class");
                        else noteParts.Add($"open: no open IG {cls} positions");
                        if (igUnmarkable > 0)
                            noteParts.Add($"{igUnmarkable} IG position(s) had no broker mark and were excluded from open");
                    }
                    // REALISED — IG closed-deal history (golden), split today / LTD.
                    if (igHistError is not null) noteParts.Add($"realised: {igHistError}");
                    else if (igRealisedByStrategy.TryGetValue(strategyId, out var r))
                    {
                        realisedLtd = r.Ltd;
                        realisedToday = r.Today;   // today's byDay slice (may be 0)
                        trades = r.Trades;
                        winRate = r.Trades > 0 ? 100.0 * r.Winning / r.Trades : null;
                    }
                    else noteParts.Add("realised: no IG closed deals attributed in window");
                }
                else
                {
                    // T212 strategy. OPEN — Σ the broker's own Ppl (golden) for the
                    // symbols the OMS attributes to this strategy, falling back to
                    // (CurrentPrice − avg) × qty using T212's OWN price when ppl is
                    // absent (the demo /positions feed omits per-row ppl).
                    if (t212Error is not null) noteParts.Add($"open: {t212Error}");
                    else if (omsError is not null) noteParts.Add($"open: {omsError}");
                    else
                    {
                        var (o, missing, usedFallback) = T212OpenForStrategy(strategyId, broker, omsPos!, t212!);
                        open = o;
                        if (usedFallback > 0)
                            noteParts.Add($"{usedFallback} held symbol(s) used T212's (currentPrice − avg) × qty for open (broker omitted per-position ppl)");
                        if (missing > 0)
                            noteParts.Add($"{missing} held symbol(s) had neither T212 ppl nor a usable price+avg and were excluded from open");
                    }
                    // REALISED — FIFO replay of this strategy's OMS fills, split
                    // today vs LTD by the SELL fill's date. T212 has no realised
                    // feed; the ledger is the only provable source.
                    if (omsError is not null) noteParts.Add($"realised: {omsError}");
                    else
                    {
                        try
                        {
                            var fills = await LoadDatedFillsAsync(db, strategyId, broker, ct);
                            if (fills.Count == 0)
                                noteParts.Add("realised: no OMS fills for this strategy yet");
                            else
                            {
                                var rr = PnlByStrategy.FifoRealisedSplit(fills);
                                realisedLtd = rr.RealisedLtd;
                                realisedToday = rr.RealisedToday;
                                trades = rr.ClosedTrades;
                                winRate = rr.WinRatePct;
                                noteParts.Add("realised derived from OMS fills (FIFO; T212 has no realised feed); today = sells dated today (UTC)");
                                if (rr.UnmatchedSellQty > 0m)
                                    noteParts.Add($"{rr.UnmatchedSellQty:0.##} sell qty had no matching buy lot in the ledger — realised may understate");
                            }
                        }
                        catch (Exception ex)
                        {
                            noteParts.Add($"realised: OMS fills unavailable: {ex.Message}");
                        }
                    }
                }

                outRows.Add(new PnlByStrategy.StrategyPnlRow(
                    StrategyId: strategyId,
                    Broker: broker,
                    Currency: ccy,
                    OpenPnl: open,
                    RealisedToday: realisedToday,
                    RealisedLtd: realisedLtd,
                    RealisedPnl: realisedLtd,
                    TotalPnl: PnlByStrategy.Total(open, realisedLtd),
                    Trades: trades,
                    WinRatePct: winRate,
                    Notes: PnlByStrategy.JoinNotes(noteParts.ToArray())));
            }

            return Results.Ok(new
            {
                utc = DateTime.UtcNow,
                window = new { days = window, basis = "Realised today = banked on UTC-today's closed deals/sells; realised LTD = banked life-to-date (IG closed-deal history over the window; T212 FIFO over the OMS ledger). Open = unrealised mark-to-market now." },
                rows = outRows.Select(r => new
                {
                    strategyId = r.StrategyId,
                    broker = r.Broker,
                    currency = r.Currency,
                    openPnl = r.OpenPnl,
                    realisedToday = r.RealisedToday,
                    realisedLtd = r.RealisedLtd,
                    realisedPnl = r.RealisedPnl,
                    totalPnl = r.TotalPnl,
                    trades = r.Trades,
                    winRatePct = r.WinRatePct,
                    notes = r.Notes,
                }),
            });
        });

        return app;
    }

    // ── helpers ──────────────────────────────────────────────────────────

    /// <summary>Open P&amp;L for the symbols this strategy holds in the OMS,
    /// marked from T212's OWN data. Prefer the broker's per-position Ppl (golden
    /// — sums to the account-level Ppl); when the feed omits ppl for a symbol
    /// (the demo /positions feed does), FALL BACK to T212's own
    /// (CurrentPrice − AveragePricePaid) × Quantity — guarded on avg &gt; 0 and a
    /// present CurrentPrice (which IS populated, even for delisted names like
    /// LUK). A symbol with neither is EXCLUDED (never marked to $0), mirroring
    /// the trading212/positions fix in IntegrationsEndpoints (commit 0d27e20).
    /// Returns (open, excludedCount, fallbackCount).</summary>
    private static (decimal Open, int Missing, int UsedFallback) T212OpenForStrategy(
        string strategyId, string broker, List<OmsPosition> omsPos, Trading212PositionsResult t212)
    {
        // Bare-key the FULL T212 position by ticker so we can reach both its ppl
        // AND its currentPrice/avg/qty for the fallback (e.g. "AAPL_US_EQ" → "AAPL").
        var byKey = new Dictionary<string, Trading212Position>(StringComparer.OrdinalIgnoreCase);
        foreach (var p in t212.Positions)
        {
            var t = p.Instrument?.Ticker ?? p.Ticker;
            var key = ReconcileMath.Bare(t ?? "");
            if (!string.IsNullOrWhiteSpace(key)) byKey[key] = p;
        }
        decimal open = 0m;
        int missing = 0, usedFallback = 0;
        var held = omsPos.Where(p =>
            string.Equals(p.StrategyId, strategyId, StringComparison.OrdinalIgnoreCase)
            && p.Broker.StartsWith("T212", StringComparison.OrdinalIgnoreCase)
            && p.Quantity != 0m);
        foreach (var p in held)
        {
            var key = ReconcileMath.Bare(p.Symbol);
            if (!byKey.TryGetValue(key, out var pos)) { missing++; continue; }
            if (pos.Ppl is decimal v) { open += v; continue; }
            // Fallback: T212's own (currentPrice − avg) × qty. Guard avg > 0 and
            // a present currentPrice; otherwise exclude (don't mark to $0).
            if (pos.AveragePricePaid is decimal avg && avg > 0m
                && pos.CurrentPrice is decimal cur)
            {
                open += (cur - avg) * pos.Quantity;
                usedFallback++;
            }
            else missing++;
        }
        return (open, missing, usedFallback);
    }

    /// <summary>Load this strategy's broker fills from the OMS ledger, oldest
    /// first, as dated side/qty/price tuples for the today/LTD FIFO matcher. Each
    /// fill is flagged IsToday when its UTC fill date == today, so realised can be
    /// split into "banked today" vs life-to-date. Read-only query against
    /// oms_orders ⋈ oms_fills — no PostgresOmsService change.</summary>
    private static async Task<List<PnlByStrategy.DatedFill>> LoadDatedFillsAsync(
        NpgsqlDataSource db, string strategyId, string broker, CancellationToken ct)
    {
        await using var conn = await db.OpenConnectionAsync(ct);
        var rows = await conn.QueryAsync<(string side, decimal qty, decimal price, DateTime fill_at_utc)>(@"
            SELECT o.side AS side, f.qty AS qty, f.price AS price, f.fill_at_utc AS fill_at_utc
            FROM oms_orders o
            JOIN oms_fills f ON f.order_id = o.id
            WHERE o.strategy_id = @sid AND o.broker = @broker
            ORDER BY f.fill_at_utc ASC, f.id ASC;",
            new { sid = strategyId, broker });
        var today = DateTime.UtcNow.Date;
        return rows.Select(r => new PnlByStrategy.DatedFill(
            r.side, r.qty, r.price,
            DateTime.SpecifyKind(r.fill_at_utc, DateTimeKind.Utc).ToUniversalTime().Date == today)).ToList();
    }

    /// <summary>Build the asset-class → owning-IG-strategy map exactly the way
    /// /integrations/ig/history does: strategies that route to IG (config), each
    /// claiming the asset classes of the symbols it has emitted; the lone IG
    /// strategy with no derivable symbols absorbs the remaining class.</summary>
    private static async Task<Dictionary<string, string>> BuildIgClassMapAsync(
        NpgsqlDataSource db, IEnumerable<string?> seenInstruments, CancellationToken ct)
    {
        var classToStrategy = new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase);
        try
        {
            await using var conn = await db.OpenConnectionAsync(ct);
            var igStrategies = (await conn.QueryAsync<string>(
                "SELECT strategy_id FROM strategy_broker_map WHERE broker ILIKE 'IG%'")).ToList();
            var derived = new Dictionary<string, HashSet<string>>();
            foreach (var sid in igStrategies)
            {
                var syms = await conn.QueryAsync<string>(
                    "SELECT DISTINCT symbol FROM orders WHERE strategy_name = @sid", new { sid });
                var classes = syms.Select(IntegrationsEndpoints.AssetClassOf)
                    .Where(c => c is not null).Select(c => c!)
                    .ToHashSet(StringComparer.OrdinalIgnoreCase);
                derived[sid] = classes;
                foreach (var c in classes) classToStrategy.TryAdd(c, sid);
            }
            var unknownStrategies = derived.Where(kv => kv.Value.Count == 0).Select(kv => kv.Key).ToList();
            var seenClasses = seenInstruments.Select(IntegrationsEndpoints.AssetClassOf)
                .Where(c => c is not null).Select(c => c!)
                .ToHashSet(StringComparer.OrdinalIgnoreCase);
            var unclaimed = seenClasses.Where(c => !classToStrategy.ContainsKey(c)).ToList();
            if (unknownStrategies.Count == 1)
                foreach (var c in unclaimed) classToStrategy.TryAdd(c, unknownStrategies[0]);
        }
        catch { /* attribution best-effort; caller handles an empty map */ }
        return classToStrategy;
    }

    /// <summary>The asset class this IG strategy owns — the inverse of the
    /// class→strategy map, so we know which slice of IG open unrealised to sum
    /// for it. Same config-driven derivation; returns null when undeterminable.
    /// </summary>
    private static async Task<string?> AssetClassForIgStrategyAsync(
        NpgsqlDataSource db, string strategyId,
        List<(string StrategyId, string Broker)> map, CancellationToken ct)
    {
        // The instruments we "saw" for the inverse map are the IG asset classes
        // any IG strategy could trade — derive from both FX and EQUITY so the
        // unknown-strategy fallback can resolve intraday_flat → EQUITY.
        var classMap = await BuildIgClassMapAsync(db, new[] { "EURUSD", "AAPL" }, ct);
        foreach (var (cls, sid) in classMap)
            if (string.Equals(sid, strategyId, StringComparison.OrdinalIgnoreCase))
                return cls;
        return null;
    }
}

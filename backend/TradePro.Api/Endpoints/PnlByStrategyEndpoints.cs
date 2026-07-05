using System.Text.Json;
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

            // IBKR account-state (NLV + per-position unrealised), pushed by the algo
            // daemon — the .NET IBKR client is bound to the LIVE harvest account, so
            // the server can't query the paper book directly. The clone's OPEN P&L is
            // summed from these live marks; REALISED still comes from the OMS FIFO
            // (IBKR fills DO carry prices, unlike T212). Keyed by bare symbol.
            var ibkrUnrealBySymbol = new Dictionary<string, decimal>(StringComparer.OrdinalIgnoreCase);
            string? ibkrStateError = null;
            try
            {
                await using var conn = await db.OpenConnectionAsync(ct);
                var positionsJson = await conn.ExecuteScalarAsync<string?>(@"
                    SELECT positions::text
                    FROM broker_account_state
                    WHERE broker = 'IBKR_PAPER'
                    ORDER BY updated_at_utc DESC LIMIT 1;");
                if (!string.IsNullOrWhiteSpace(positionsJson))
                {
                    using var doc = JsonDocument.Parse(positionsJson);
                    if (doc.RootElement.ValueKind == JsonValueKind.Array)
                        foreach (var p in doc.RootElement.EnumerateArray())
                        {
                            var sym = p.TryGetProperty("symbol", out var s) ? s.GetString() : null;
                            if (string.IsNullOrWhiteSpace(sym)) continue;
                            decimal u = p.TryGetProperty("unrealisedPnl", out var up)
                                        && up.ValueKind == JsonValueKind.Number ? up.GetDecimal() : 0m;
                            ibkrUnrealBySymbol[ReconcileMath.Bare(sym)] = u;
                        }
                }
            }
            catch (Exception ex) { ibkrStateError = $"IBKR account-state unavailable: {ex.Message}"; }

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

            // ── 2b. broker-confirmation coverage (fail-loud) ───────────────
            // The single honest signal for "can we trust this row": of a
            // strategy's FILLED orders, how many carry a broker_order_id (the id
            // the broker returns on ACK). T212/IG record it synchronously; the
            // IBKR paper clones route via a fire-and-forget inbox and record
            // NONE — so their fills/P&L are OMS-SIMULATED, not broker-verified,
            // and must NOT be compared like-for-like against the real book.
            // Data-driven off the ledger — never a hardcoded broker-name check.
            var fillCoverage = new Dictionary<string, (int Filled, int WithBrokerId)>(StringComparer.OrdinalIgnoreCase);
            string? coverageError = null;
            try
            {
                await using var conn = await db.OpenConnectionAsync(ct);
                var covRows = await conn.QueryAsync<(string strategy_id, int filled, int with_id)>(@"
                    SELECT strategy_id,
                           COUNT(*)::int              AS filled,
                           COUNT(broker_order_id)::int AS with_id
                    FROM oms_orders
                    WHERE state = 'FILLED'
                    GROUP BY strategy_id;");
                foreach (var c in covRows) fillCoverage[c.strategy_id] = (c.filled, c.with_id);
            }
            catch (Exception ex) { coverageError = $"broker-confirmation coverage unavailable: {ex.Message}"; }

            // ── 3. build one row per mapped strategy ───────────────────────
            var outRows = new List<PnlByStrategy.StrategyPnlRow>();
            foreach (var (strategyId, broker) in map)
            {
                var ccy = CurrencyFor(broker);
                var isIg = broker.StartsWith("IG", StringComparison.OrdinalIgnoreCase);
                var isIbkr = broker.StartsWith("IBKR", StringComparison.OrdinalIgnoreCase);
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
                    // OPEN — IBKR marks come from the account-state push; T212 from
                    // its own /positions feed. (REALISED below is OMS FIFO for both.)
                    if (isIbkr)
                    {
                        if (ibkrStateError is not null) noteParts.Add($"open: {ibkrStateError}");
                        else if (omsError is not null) noteParts.Add($"open: {omsError}");
                        else
                        {
                            var (o, attributed) = IbkrOpenForStrategy(strategyId, omsPos!, ibkrUnrealBySymbol);
                            open = o;
                            noteParts.Add(attributed > 0
                                ? $"open: Σ IBKR live per-position unrealised over {attributed} OMS-attributed symbol(s)"
                                : "open: no OMS-attributed IBKR positions for this strategy (open=0)");
                        }
                    }
                    // T212 strategy. OPEN — Σ the broker's own Ppl (golden) for the
                    // symbols the OMS attributes to this strategy, falling back to
                    // (CurrentPrice − avg) × qty using T212's OWN price when ppl is
                    // absent (the demo /positions feed omits per-row ppl).
                    else if (t212Error is not null) noteParts.Add($"open: {t212Error}");
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
                            var (bySymbol, unpricedFills, incompleteSymbols) =
                                await LoadDatedFillsAsync(db, strategyId, broker, ct);
                            if (bySymbol.Count == 0 && unpricedFills == 0)
                                noteParts.Add("realised: no OMS fills for this strategy yet");
                            else if (incompleteSymbols > 0)
                                // GUARD: the ledger has zero/blank-price fills (the T212
                                // fill-price gap). FIFO realised cannot be reconstructed from
                                // a holed ledger — a zero-cost BUY lot makes a later SELL book
                                // the FULL proceeds as phantom profit (this is what produced
                                // the bogus +£5k realised). Publish NO number rather than a
                                // fabricated one; broker Net Liq is the source of truth.
                                // realisedLtd/realisedToday/winRate stay null; trades stays 0.
                                noteParts.Add($"realised NOT computable: {unpricedFills} fill(s) across {incompleteSymbols} symbol(s) recorded with no broker price (T212 fill-price gap); realised left blank rather than a fabricated figure — reconcile to the broker's Net Liq");
                            else
                            {
                                // Correct realised: FIFO PER SYMBOL (matching a sell against a
                                // different ticker's buy lot is meaningless), summed across
                                // this strategy's symbols.
                                decimal ltd = 0m, tod = 0m, unmatched = 0m;
                                int closed = 0, won = 0;
                                foreach (var stream in bySymbol.Values)
                                {
                                    var rr = PnlByStrategy.FifoRealisedSplit(stream);
                                    ltd += rr.RealisedLtd;
                                    tod += rr.RealisedToday;
                                    closed += rr.ClosedTrades;
                                    won += rr.WinningTrades;
                                    unmatched += rr.UnmatchedSellQty;
                                }
                                realisedLtd = ltd;
                                realisedToday = tod;
                                trades = closed;
                                winRate = closed > 0 ? 100.0 * won / closed : (double?)null;
                                noteParts.Add("realised derived from OMS fills (per-symbol FIFO; T212 has no realised feed); today = sells dated today (UTC)");
                                if (unmatched > 0m)
                                    noteParts.Add($"{unmatched:0.##} sell qty had no matching buy lot in the ledger — realised may understate");
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
                coverageError,
                rows = outRows.Select(r =>
                {
                    fillCoverage.TryGetValue(r.StrategyId, out var cov);
                    var filled = cov.Filled;
                    var confirmed = cov.WithBrokerId;
                    // UNCONFIRMED only when the strategy HAS fills yet NONE carry a
                    // broker order id — i.e. the whole book is OMS-simulated. A
                    // strategy with no fills yet isn't "unconfirmed", just empty.
                    var unconfirmed = filled > 0 && confirmed == 0;
                    var confirmation = coverageError is not null
                        ? $"confirmation unknown: {coverageError}"
                        : unconfirmed
                            ? $"UNCONFIRMED — 0 of {filled} filled orders carry a broker order id; positions & P&L are OMS-simulated, NOT broker-verified. Not comparable to broker-confirmed strategies until execution is routed through the broker."
                            : filled > 0
                                ? $"broker-verified ({confirmed}/{filled} filled orders carry a broker order id)"
                                : "no fills yet";
                    return new
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
                        filledOrders = filled,
                        brokerConfirmedFills = confirmed,
                        unconfirmed,
                        confirmation,
                        notes = r.Notes,
                    };
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

    /// <summary>Open P&amp;L for an IBKR strategy: Σ the broker's OWN per-position
    /// unrealised (pushed via account-state) over ONLY the symbols the OMS
    /// attributes to this strategy. No whole-account fallback — multiple
    /// strategies can share one IBKR account, so attributing the account TOTAL
    /// to a strategy with no tracked positions double-counts another strategy's
    /// book (it falsely showed the FX-clone +$328 = the equity clone's marks).
    /// A strategy with zero attributed positions has zero open P&amp;L — honest.
    /// Returns (open, attributedSymbols).</summary>
    private static (decimal Open, int Attributed) IbkrOpenForStrategy(
        string strategyId, List<OmsPosition> omsPos, Dictionary<string, decimal> unrealBySymbol)
    {
        var held = omsPos.Where(p =>
            string.Equals(p.StrategyId, strategyId, StringComparison.OrdinalIgnoreCase)
            && p.Broker.StartsWith("IBKR", StringComparison.OrdinalIgnoreCase)
            && p.Quantity != 0m);
        decimal open = 0m;
        int attributed = 0;
        foreach (var p in held)
            if (unrealBySymbol.TryGetValue(ReconcileMath.Bare(p.Symbol), out var u))
            {
                open += u;
                attributed++;
            }
        return (open, attributed);
    }

    /// <summary>Load this strategy's broker fills from the OMS ledger, oldest
    /// first, as dated side/qty/price tuples for the today/LTD FIFO matcher. Each
    /// fill is flagged IsToday when its UTC fill date == today, so realised can be
    /// split into "banked today" vs life-to-date. Read-only query against
    /// oms_orders ⋈ oms_fills — no PostgresOmsService change.</summary>
    /// Returns (priced fills for the FIFO, count of UNPRICED fills). Unpriced
    /// fills (price ≤ 0) come from aged-out MOO orders: T212 drops them from
    /// /orders before the poller captures a price, so it records the fill at 0
    /// to keep POSITION tracking correct (broker = golden source) — but those
    /// 0s must NOT feed realised P&L (they'd fake £0 / 0% win). We exclude them
    /// and surface the count so the row can report "realised not captured".
    /// <summary>
    /// Load this strategy's OMS fills GROUPED BY SYMBOL — FIFO realised P&amp;L is
    /// only valid per-symbol (matching a SELL of one ticker against a BUY lot of
    /// another is nonsense). Within each symbol, fills are time-ordered.
    /// <para/>
    /// Unpriced fills (price ≤ 0 — the T212 fill-price gap) CANNOT be matched:
    /// a zero-cost BUY lot makes a later SELL book the full proceeds as phantom
    /// profit, and dropping it silently leaves the SELL to match a wrong lot.
    /// Either way the symbol's realised is no longer provable, so we mark the
    /// WHOLE symbol "incomplete" and exclude it from realised (counting the
    /// fills + symbols affected) rather than publish a fabricated number.
    /// </summary>
    private static async Task<(Dictionary<string, List<PnlByStrategy.DatedFill>> BySymbol,
                               int UnpricedFills, int IncompleteSymbols)> LoadDatedFillsAsync(
        NpgsqlDataSource db, string strategyId, string broker, CancellationToken ct)
    {
        await using var conn = await db.OpenConnectionAsync(ct);
        var rows = await conn.QueryAsync<(string symbol, string side, decimal qty, decimal price, DateTime fill_at_utc)>(@"
            SELECT o.symbol AS symbol, o.side AS side, f.qty AS qty, f.price AS price, f.fill_at_utc AS fill_at_utc
            FROM oms_orders o
            JOIN oms_fills f ON f.order_id = o.id
            WHERE o.strategy_id = @sid AND o.broker = @broker
            ORDER BY f.fill_at_utc ASC, f.id ASC;",
            new { sid = strategyId, broker });
        var today = DateTime.UtcNow.Date;
        var bySymbol = new Dictionary<string, List<PnlByStrategy.DatedFill>>(StringComparer.OrdinalIgnoreCase);
        var tainted = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
        int unpricedFills = 0;
        foreach (var r in rows)
        {
            var sym = r.symbol ?? "";
            if (r.price <= 0m)
            {
                // A holed ledger for this symbol — its realised can't be trusted.
                unpricedFills++;
                tainted.Add(sym);
                continue;
            }
            if (!bySymbol.TryGetValue(sym, out var list))
                bySymbol[sym] = list = new List<PnlByStrategy.DatedFill>();
            list.Add(new PnlByStrategy.DatedFill(
                r.side, r.qty, r.price,
                DateTime.SpecifyKind(r.fill_at_utc, DateTimeKind.Utc).ToUniversalTime().Date == today));
        }
        // Drop any symbol that had ≥1 unpriced fill — partial priced fills for it
        // would FIFO-mismatch. Excluded from realised; surfaced as incomplete.
        foreach (var sym in tainted) bySymbol.Remove(sym);
        return (bySymbol, unpricedFills, tainted.Count);
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

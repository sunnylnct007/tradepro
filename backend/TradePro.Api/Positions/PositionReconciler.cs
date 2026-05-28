using System.Net;
using Dapper;
using Npgsql;
using TradePro.Api.Oms;
using TradePro.Api.Providers.Trading212;

namespace TradePro.Api.Positions;

/// <summary>
/// Diffs broker (T212) positions vs our derived positions (sum of fills
/// in oms_orders + oms_fills) and persists drift into
/// position_drift_events.
///
/// Architecture per the systematic-trading discussion:
/// - Broker is golden. We don't auto-correct our records; we LOG the
///   divergence and surface it to the operator (in-app banner + daily
///   digest). Auto-correction silently destroys audit trails.
/// - This is intentionally NOT a background service yet. Triggered on
///   demand from the positions endpoint + scheduled CLI. Promote to a
///   background timer once we trust the diff cadence (Day 1 vs Day N).
/// - One run produces one snapshot. Open drift events for the same
///   (broker, symbol) persist across runs — they're only resolved by
///   an explicit operator action.
/// </summary>
public sealed class PositionReconciler
{
    private readonly Trading212DemoPositionsCache _demoPositionsCache;
    private readonly IOmsService _oms;
    private readonly NpgsqlDataSource _db;
    private readonly ILogger<PositionReconciler> _log;

    // Tier thresholds — see migration 016 for the rationale. Tunable
    // via Settings KV later; hard-coded for now to keep the surface
    // small while we learn the real-world divergence rate.
    private const decimal MinorQtyDriftFrac = 0.0m;     // anything > 0 logs minor
    private const decimal MajorQtyDriftFrac = 0.01m;    // > 1% of |internal_qty|
    private const decimal CriticalQtyDriftFrac = 0.05m; // > 5% of |internal_qty|
    private const decimal MajorPriceDriftFrac = 0.01m;
    private const decimal CriticalPriceDriftFrac = 0.05m;

    public PositionReconciler(
        Trading212DemoPositionsCache demoPositionsCache,
        IOmsService oms,
        NpgsqlDataSource db,
        ILogger<PositionReconciler> log)
    {
        _demoPositionsCache = demoPositionsCache;
        _oms = oms;
        _db = db;
        _log = log;
    }

    /// <summary>One reconciliation pass for the T212 demo account.
    /// Returns the drift events created (already persisted).</summary>
    public async Task<ReconcileResult> ReconcileT212DemoAsync(CancellationToken ct)
    {
        var brokerLabel = "T212_DEMO";
        // 1. Get broker truth (cached, so this is cheap)
        var brokerResult = await _demoPositionsCache.GetAsync(ct);
        if (brokerResult.HttpStatus == (int)HttpStatusCode.TooManyRequests
            && brokerResult.Positions is null)
        {
            _log.LogWarning("T212 rate limited + no cached positions — skipping reconcile");
            return new ReconcileResult(
                Broker: brokerLabel,
                BrokerPositions: 0, InternalPositions: 0,
                EventsCreated: Array.Empty<DriftEvent>(),
                Error: "T212 rate-limited, no cached snapshot");
        }
        if (brokerResult.Error is not null && brokerResult.Positions is null)
        {
            return new ReconcileResult(
                Broker: brokerLabel,
                BrokerPositions: 0, InternalPositions: 0,
                EventsCreated: Array.Empty<DriftEvent>(),
                Error: brokerResult.Error);
        }

        var brokerByNormalisedTicker = (brokerResult.Positions ?? new List<Trading212Position>())
            .GroupBy(p => NormaliseTicker(p.Ticker))
            .ToDictionary(g => g.Key, g => g.Sum(x => x.Quantity));
        var brokerAvgPrice = (brokerResult.Positions ?? new List<Trading212Position>())
            .Where(p => p.AveragePricePaid is not null)
            .GroupBy(p => NormaliseTicker(p.Ticker))
            .ToDictionary(g => g.Key, g => g.First().AveragePricePaid);

        // 2. Get internal records — sum-of-fills per symbol for orders
        //    that went through this broker.
        var internalAll = await _oms.ListPositionsAsync(strategyId: null);
        var internalByTicker = internalAll
            .Where(p => string.Equals(p.Broker, brokerLabel, StringComparison.OrdinalIgnoreCase))
            .GroupBy(p => NormaliseTicker(p.Symbol))
            .Select(g => new
            {
                Ticker = g.Key,
                Quantity = g.Sum(x => x.Quantity),
                AvgPrice = g.Average(x => x.AvgPrice),
            })
            .ToDictionary(x => x.Ticker, x => x);

        // 3. Diff — union of tickers, per-ticker drift computation.
        var allTickers = new HashSet<string>(brokerByNormalisedTicker.Keys);
        foreach (var k in internalByTicker.Keys) allTickers.Add(k);

        var events = new List<DriftEvent>();
        foreach (var ticker in allTickers.OrderBy(t => t))
        {
            decimal? brokerQty = brokerByNormalisedTicker.TryGetValue(ticker, out var bq) ? bq : null;
            decimal? internalQty = internalByTicker.TryGetValue(ticker, out var iq) ? iq.Quantity : null;
            decimal? brokerPrice = brokerAvgPrice.TryGetValue(ticker, out var bp) ? bp : null;
            decimal? internalPrice = internalByTicker.TryGetValue(ticker, out var ip) ? ip.AvgPrice : null;

            var (severity, qtyDrift, priceDriftPct) = ClassifyDrift(brokerQty, internalQty, brokerPrice, internalPrice);
            if (severity is null) continue; // no material drift

            events.Add(new DriftEvent(
                Broker: brokerLabel, Symbol: ticker,
                BrokerQty: brokerQty, InternalQty: internalQty, QtyDrift: qtyDrift,
                BrokerAvgPrice: brokerPrice, InternalAvgPrice: internalPrice,
                PriceDriftPct: priceDriftPct, Severity: severity));
        }

        // 4. Persist — only log NEW drift events. If an open (unresolved)
        //    event for the same (broker, symbol) already exists with the
        //    same severity, don't double-log; the operator already knows.
        //    Different severity → log (state changed).
        var persisted = await PersistAsync(events, ct);

        return new ReconcileResult(
            Broker: brokerLabel,
            BrokerPositions: brokerByNormalisedTicker.Count,
            InternalPositions: internalByTicker.Count,
            EventsCreated: persisted,
            Error: null);
    }

    /// <summary>Mark a drift event resolved. Operator-initiated.</summary>
    public async Task<bool> ResolveAsync(long eventId, string resolvedBy, string? note, CancellationToken ct)
    {
        await using var conn = await _db.OpenConnectionAsync();
        var n = await conn.ExecuteAsync(@"
            UPDATE position_drift_events
            SET resolved_at_utc = NOW(),
                resolved_by = @resolvedBy,
                resolution_note = @note
            WHERE id = @eventId AND resolved_at_utc IS NULL;",
            new { eventId, resolvedBy, note });
        return n > 0;
    }

    // ---------------------------------------------------------------- //

    private (string? severity, decimal qtyDrift, decimal? priceDriftPct) ClassifyDrift(
        decimal? brokerQty, decimal? internalQty,
        decimal? brokerPrice, decimal? internalPrice)
    {
        // Both sides empty (rare — shouldn't be in the union) → no drift.
        if (brokerQty is null && internalQty is null) return (null, 0m, null);

        var b = brokerQty ?? 0m;
        var i = internalQty ?? 0m;
        var qtyDrift = b - i;
        var qtyMagnitude = Math.Max(Math.Abs(i), Math.Abs(b));

        // Exact match → no drift, return nothing.
        if (qtyDrift == 0m && (brokerPrice is null || internalPrice is null
                               || Math.Abs(brokerPrice.Value - internalPrice.Value) < 0.01m))
        {
            return (null, 0m, null);
        }

        // Asymmetric — one side has the symbol, the other doesn't.
        // CRITICAL: either trader manually traded outside our system,
        // or our records are corrupted.
        if (brokerQty is null || internalQty is null
            || (b == 0m) != (i == 0m))
        {
            return ("critical", qtyDrift, null);
        }

        // Same direction, both non-zero — compute fractional drift.
        var qtyFrac = qtyMagnitude > 0 ? Math.Abs(qtyDrift) / qtyMagnitude : 0m;
        decimal? priceFrac = null;
        if (brokerPrice is not null && internalPrice is not null && internalPrice.Value > 0)
        {
            var pDrift = brokerPrice.Value - internalPrice.Value;
            priceFrac = Math.Abs(pDrift) / internalPrice.Value;
        }
        var priceDriftPct = priceFrac.HasValue ? priceFrac.Value * 100m : (decimal?)null;

        if (qtyFrac >= CriticalQtyDriftFrac || (priceFrac ?? 0m) >= CriticalPriceDriftFrac)
            return ("critical", qtyDrift, priceDriftPct);
        if (qtyFrac >= MajorQtyDriftFrac || (priceFrac ?? 0m) >= MajorPriceDriftFrac)
            return ("major", qtyDrift, priceDriftPct);
        if (qtyFrac > MinorQtyDriftFrac)
            return ("minor", qtyDrift, priceDriftPct);
        return (null, 0m, null);
    }

    private async Task<List<DriftEvent>> PersistAsync(IReadOnlyList<DriftEvent> events, CancellationToken ct)
    {
        if (events.Count == 0) return new();
        var persisted = new List<DriftEvent>();
        await using var conn = await _db.OpenConnectionAsync();
        foreach (var e in events)
        {
            // Dedupe — if there's already an unresolved event for the
            // same (broker, symbol) at the same severity, skip; the
            // operator's already been told. Different severity logs
            // (the state of the drift changed).
            var alreadyOpen = await conn.QueryFirstOrDefaultAsync<long?>(@"
                SELECT id FROM position_drift_events
                WHERE broker = @broker AND symbol = @symbol
                  AND severity = @severity
                  AND resolved_at_utc IS NULL
                LIMIT 1;",
                new { broker = e.Broker, symbol = e.Symbol, severity = e.Severity });
            if (alreadyOpen is not null) continue;

            await conn.ExecuteAsync(@"
                INSERT INTO position_drift_events
                  (broker, symbol, broker_qty, internal_qty, qty_drift,
                   broker_avg_price, internal_avg_price, price_drift_pct,
                   severity, detected_at_utc)
                VALUES (@broker, @symbol, @brokerQty, @internalQty, @qtyDrift,
                        @brokerAvgPrice, @internalAvgPrice, @priceDriftPct,
                        @severity, NOW());",
                new
                {
                    broker = e.Broker, symbol = e.Symbol,
                    brokerQty = e.BrokerQty, internalQty = e.InternalQty,
                    qtyDrift = e.QtyDrift,
                    brokerAvgPrice = e.BrokerAvgPrice, internalAvgPrice = e.InternalAvgPrice,
                    priceDriftPct = e.PriceDriftPct,
                    severity = e.Severity,
                });
            persisted.Add(e);
            _log.LogInformation(
                "Position drift {Severity}: {Broker} {Symbol} broker={B} internal={I} drift={D}",
                e.Severity, e.Broker, e.Symbol, e.BrokerQty, e.InternalQty, e.QtyDrift);
        }
        return persisted;
    }

    // T212 returns tickers like "AAPL_US_EQ"; our OMS stores either
    // that or the bare "AAPL". Strip the suffix on both sides for
    // matching so we don't false-positive on "AAPL vs AAPL_US_EQ".
    private static string NormaliseTicker(string ticker)
    {
        if (string.IsNullOrWhiteSpace(ticker)) return string.Empty;
        var t = ticker.Trim().ToUpperInvariant();
        var underscore = t.IndexOf('_');
        return underscore > 0 ? t[..underscore] : t;
    }

    public sealed record DriftEvent(
        string Broker,
        string Symbol,
        decimal? BrokerQty,
        decimal? InternalQty,
        decimal QtyDrift,
        decimal? BrokerAvgPrice,
        decimal? InternalAvgPrice,
        decimal? PriceDriftPct,
        string Severity);

    public sealed record ReconcileResult(
        string Broker,
        int BrokerPositions,
        int InternalPositions,
        IReadOnlyList<DriftEvent> EventsCreated,
        string? Error);
}

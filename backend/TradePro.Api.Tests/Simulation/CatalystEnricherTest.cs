using Dapper;
using TradePro.Api.Models;
using TradePro.Api.Simulation;
using TradePro.Api.Tests.Infrastructure;
using Xunit;

namespace TradePro.Api.Tests.Simulation;

/// <summary>
/// Phase C-3 — pins the catalyst-overlay enrichment logic:
///
///   * Flag math (ComputeFlag) — the Ecopetrol pattern. Pure
///     function, no DB. Fast scenarios.
///   * End-to-end via Postgres fixture — UPSERT catalysts, run
///     the enricher, assert SignalDecision arrives populated
///     with the right shape + flag. Catches a future schema
///     migration breaking the join.
///
/// All tests reset the catalysts table per-test so a leak from
/// test N can't seed test N+1.
/// </summary>
[Collection("postgres")]
public sealed class CatalystEnricherTest
{
    private readonly PostgresFixture _fx;

    public CatalystEnricherTest(PostgresFixture fx)
    {
        _fx = fx;
        using var conn = _fx.Db.OpenConnection();
        conn.Execute("DELETE FROM catalysts;");
    }

    // ── Pure flag-math scenarios ──────────────────────────────────

    [Fact]
    public void HOLD_plus_imminent_high_severity_is_divergence()
    {
        var catalysts = new[]
        {
            new CatalystSummary(1, "election", "2026-06-13", "Colombia election",
                "high", "extractor.news", DaysUntil: 8),
        };
        var flag = CatalystEnricher.ComputeFlag("HOLD", catalysts);
        Assert.Equal("tech_event_divergence", flag);
    }

    [Fact]
    public void BUY_plus_imminent_high_severity_is_alignment()
    {
        var catalysts = new[]
        {
            new CatalystSummary(1, "central_bank", "2026-06-12", "FOMC decision",
                "high", "extractor.news", DaysUntil: 7),
        };
        var flag = CatalystEnricher.ComputeFlag("BUY", catalysts);
        Assert.Equal("tech_event_alignment", flag);
    }

    [Fact]
    public void HOLD_plus_medium_severity_does_not_set_flag()
    {
        // Medium catalysts surface as chips but never tip the flag.
        var catalysts = new[]
        {
            new CatalystSummary(1, "earnings", "2026-06-12", "earnings expected",
                "medium", "extractor.news", DaysUntil: 7),
        };
        Assert.Null(CatalystEnricher.ComputeFlag("HOLD", catalysts));
    }

    [Fact]
    public void HOLD_plus_far_high_severity_does_not_set_flag()
    {
        // 30 days out — beyond the 14-day "imminent" threshold.
        var catalysts = new[]
        {
            new CatalystSummary(1, "election", "2026-07-04", "election",
                "high", "extractor.news", DaysUntil: 30),
        };
        Assert.Null(CatalystEnricher.ComputeFlag("HOLD", catalysts));
    }

    [Fact]
    public void HOLD_plus_past_high_severity_does_not_set_flag()
    {
        // Past catalysts are informational only.
        var catalysts = new[]
        {
            new CatalystSummary(1, "earnings", "2026-05-01", "Q1 earnings",
                "high", "extractor.news", DaysUntil: -30),
        };
        Assert.Null(CatalystEnricher.ComputeFlag("HOLD", catalysts));
    }

    [Fact]
    public void SELL_plus_imminent_high_severity_stays_silent()
    {
        // SELL semantics are ambiguous (catalyst could confirm exit
        // OR contradict it). We surface chips but no flag.
        var catalysts = new[]
        {
            new CatalystSummary(1, "earnings", "2026-06-12", "Q2 earnings",
                "high", "extractor.news", DaysUntil: 7),
        };
        Assert.Null(CatalystEnricher.ComputeFlag("SELL", catalysts));
    }

    [Fact]
    public void Undated_catalyst_does_not_trip_flag()
    {
        // DaysUntil = null means we don't know when this fires.
        // The conservative call: chip surfaces, no flag.
        var catalysts = new[]
        {
            new CatalystSummary(1, "regulatory", null, "FDA decision pending",
                "high", "extractor.news", DaysUntil: null),
        };
        Assert.Null(CatalystEnricher.ComputeFlag("HOLD", catalysts));
    }

    [Fact]
    public void Empty_catalysts_returns_null_flag()
    {
        Assert.Null(CatalystEnricher.ComputeFlag("HOLD", Array.Empty<CatalystSummary>()));
        Assert.Null(CatalystEnricher.ComputeFlag("BUY", Array.Empty<CatalystSummary>()));
    }

    [Fact]
    public void Mixed_catalysts_only_high_counts_for_flag()
    {
        // Two mediums + one high. Flag should still set on the high.
        var catalysts = new[]
        {
            new CatalystSummary(1, "earnings", "2026-06-15", "Q earnings",
                "medium", "extractor.news", DaysUntil: 10),
            new CatalystSummary(2, "operator_note", "2026-06-13", "operator note",
                "medium", "operator", DaysUntil: 8),
            new CatalystSummary(3, "election", "2026-06-14", "key vote",
                "high", "extractor.news", DaysUntil: 9),
        };
        Assert.Equal("tech_event_divergence", CatalystEnricher.ComputeFlag("HOLD", catalysts));
    }

    // ── End-to-end via Postgres ──────────────────────────────────

    private SignalDecision MakeBareDecision(string symbol, string action)
        => new(
            Symbol: symbol,
            Strategy: "ichimoku_equity",
            AsOf: DateTime.UtcNow,
            Action: action,
            Confidence: 0.5,
            Reasons: new[] { "test reason" },
            Indicators: new IndicatorSnapshot(null, null, null, null, null, null, null),
            SuggestedStopLossPct: null,
            SuggestedTargetPct: null);

    [Fact]
    public async Task EnrichOne_populates_empty_list_when_no_catalysts()
    {
        var enricher = new CatalystEnricher(_fx.Db);
        var decision = MakeBareDecision("AAPL", "HOLD");
        var result = await enricher.EnrichOneAsync(decision, CancellationToken.None);
        Assert.NotNull(result.Catalysts);
        Assert.Empty(result.Catalysts!);
        Assert.Null(result.CatalystFlag);
    }

    [Fact]
    public async Task EnrichOne_fetches_active_catalyst_and_sets_divergence()
    {
        // The Ecopetrol shape — HOLD verdict + imminent high catalyst.
        await using var conn = await _fx.Db.OpenConnectionAsync();
        await conn.ExecuteAsync(@"
            INSERT INTO catalysts (symbol, kind, occurs_on, title, source, severity)
            VALUES ('EC', 'election', CURRENT_DATE + INTERVAL '8 days',
                    'Colombia presidential election', 'extractor.news', 'high');");

        var enricher = new CatalystEnricher(_fx.Db);
        var decision = MakeBareDecision("EC", "HOLD");
        var result = await enricher.EnrichOneAsync(decision, CancellationToken.None);

        Assert.Single(result.Catalysts!);
        Assert.Equal("election", result.Catalysts![0].Kind);
        Assert.Equal("high", result.Catalysts![0].Severity);
        Assert.Equal("tech_event_divergence", result.CatalystFlag);
    }

    [Fact]
    public async Task EnrichMany_batches_one_query_across_symbols()
    {
        // Insert catalysts for two symbols, enrich both decisions
        // in one call. Implicit cost check: this exercises the
        // ANY(@symbols) path, not N round-trips.
        await using var conn = await _fx.Db.OpenConnectionAsync();
        await conn.ExecuteAsync(@"
            INSERT INTO catalysts (symbol, kind, occurs_on, title, source, severity)
            VALUES
                ('AAPL', 'earnings', CURRENT_DATE + INTERVAL '5 days',
                 'Q3 earnings', 'extractor.news', 'medium'),
                ('SPY', 'central_bank', CURRENT_DATE + INTERVAL '3 days',
                 'FOMC meeting', 'extractor.news', 'high'),
                ('NVDA', 'operator_note', CURRENT_DATE,
                 'no event', 'operator', 'low');");

        var enricher = new CatalystEnricher(_fx.Db);
        var decisions = new[]
        {
            MakeBareDecision("AAPL", "BUY"),
            MakeBareDecision("SPY", "HOLD"),
            MakeBareDecision("MSFT", "BUY"),  // no catalysts
        };
        var enriched = await enricher.EnrichManyAsync(decisions, CancellationToken.None);

        Assert.Equal(3, enriched.Count);

        var aapl = enriched.Single(d => d.Symbol == "AAPL");
        Assert.Single(aapl.Catalysts!);
        Assert.Equal("medium", aapl.Catalysts![0].Severity);
        // BUY + medium = no flag (only HIGH tips the flag).
        Assert.Null(aapl.CatalystFlag);

        var spy = enriched.Single(d => d.Symbol == "SPY");
        Assert.Single(spy.Catalysts!);
        Assert.Equal("tech_event_divergence", spy.CatalystFlag);

        var msft = enriched.Single(d => d.Symbol == "MSFT");
        Assert.Empty(msft.Catalysts!);
        Assert.Null(msft.CatalystFlag);
    }

    [Fact]
    public async Task Dismissed_catalysts_excluded_from_enrichment()
    {
        // C-1 lets operators dismiss catalysts. Those rows must
        // not show up in the enrichment query.
        await using var conn = await _fx.Db.OpenConnectionAsync();
        await conn.ExecuteAsync(@"
            INSERT INTO catalysts (symbol, kind, occurs_on, title, source, severity, status)
            VALUES
                ('EC', 'election', CURRENT_DATE + INTERVAL '8 days',
                 'active election', 'extractor.news', 'high', 'active'),
                ('EC', 'operator_note', CURRENT_DATE + INTERVAL '7 days',
                 'noise', 'operator', 'high', 'dismissed');");

        var enricher = new CatalystEnricher(_fx.Db);
        var result = await enricher.EnrichOneAsync(
            MakeBareDecision("EC", "HOLD"), CancellationToken.None);

        Assert.Single(result.Catalysts!);
        Assert.Equal("election", result.Catalysts![0].Kind);
    }
}

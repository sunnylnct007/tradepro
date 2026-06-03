using Dapper;
using TradePro.Api.Tests.Infrastructure;
using Xunit;

namespace TradePro.Api.Tests.Admin;

/// <summary>
/// Schema-level tests for the catalyst registry (migration 038 +
/// CatalystsEndpoints). Same Dapper-against-PostgresFixture pattern
/// as DataTrustTest. Asserts:
///   1. INSERT honours defaults (status=active, severity=medium)
///   2. UNIQUE (symbol, kind, occurs_on, source) — re-insert via the
///      endpoint's UPSERT updates in place
///   3. Filter by symbol / window / status works as the cockpit panel
///      expects
///   4. Dismiss flips status to 'dismissed' + drops out of the active
///      default view
/// </summary>
[Collection("postgres")]
public sealed class CatalystsTest
{
    private readonly PostgresFixture _fx;

    public CatalystsTest(PostgresFixture fx)
    {
        _fx = fx;
        using var conn = _fx.Db.OpenConnection();
        // Per-test cleanup — no migration seeds for catalysts so a
        // plain DELETE is enough.
        conn.Execute("DELETE FROM catalysts;");
    }

    [Fact]
    public async Task Insert_applies_status_and_severity_defaults()
    {
        await using var conn = await _fx.Db.OpenConnectionAsync();
        await conn.ExecuteAsync(@"
            INSERT INTO catalysts (symbol, kind, title, source)
            VALUES ('EC', 'election', 'Colombia election in 10 days', 'extractor.news');");
        var (status, severity) =
            await conn.QuerySingleAsync<(string status, string severity)>(@"
                SELECT status, severity FROM catalysts
                WHERE symbol = 'EC' AND kind = 'election';");
        Assert.Equal("active", status);
        Assert.Equal("medium", severity);
    }

    [Fact]
    public async Task Unique_constraint_on_symbol_kind_occurs_on_source()
    {
        await using var conn = await _fx.Db.OpenConnectionAsync();
        await conn.ExecuteAsync(@"
            INSERT INTO catalysts (symbol, kind, occurs_on, title, source)
            VALUES ('AAPL', 'earnings', '2026-07-31', 'Q3 earnings', 'finnhub_earnings_calendar');");
        var exc = await Assert.ThrowsAsync<Npgsql.PostgresException>(async () =>
        {
            await conn.ExecuteAsync(@"
                INSERT INTO catalysts (symbol, kind, occurs_on, title, source)
                VALUES ('AAPL', 'earnings', '2026-07-31', 'duplicate from same source',
                        'finnhub_earnings_calendar');");
        });
        Assert.Equal("23505", exc.SqlState); // unique_violation
    }

    [Fact]
    public async Task Upsert_pattern_updates_in_place_when_keys_match()
    {
        // Mirrors the endpoint's ON CONFLICT — same key on a second
        // insert refreshes title / severity / payload without leaving
        // a duplicate row.
        await using var conn = await _fx.Db.OpenConnectionAsync();
        await conn.ExecuteAsync(@"
            INSERT INTO catalysts (symbol, kind, occurs_on, title, source, severity)
            VALUES ('AAPL', 'earnings', '2026-07-31', 'Q3 earnings expected', 'finnhub_earnings_calendar', 'medium')
            ON CONFLICT (symbol, kind, occurs_on, source)
            DO UPDATE SET title = EXCLUDED.title, severity = EXCLUDED.severity,
                          updated_at_utc = NOW();");
        await conn.ExecuteAsync(@"
            INSERT INTO catalysts (symbol, kind, occurs_on, title, source, severity)
            VALUES ('AAPL', 'earnings', '2026-07-31', 'Q3 earnings — guidance day', 'finnhub_earnings_calendar', 'high')
            ON CONFLICT (symbol, kind, occurs_on, source)
            DO UPDATE SET title = EXCLUDED.title, severity = EXCLUDED.severity,
                          updated_at_utc = NOW();");
        var rows = (await conn.QueryAsync<(string title, string severity)>(@"
            SELECT title, severity FROM catalysts
            WHERE symbol = 'AAPL' AND kind = 'earnings';")).AsList();
        Assert.Single(rows);
        Assert.Equal("Q3 earnings — guidance day", rows[0].title);
        Assert.Equal("high", rows[0].severity);
    }

    [Fact]
    public async Task Window_filter_by_occurs_on()
    {
        // Three catalysts: yesterday, today, 60 days out. A window
        // of (back=14, ahead=14) must exclude the 60-day one.
        await using var conn = await _fx.Db.OpenConnectionAsync();
        await conn.ExecuteAsync(@"
            INSERT INTO catalysts (symbol, kind, occurs_on, title, source) VALUES
                ('SPY', 'central_bank', CURRENT_DATE - INTERVAL '1 day',
                 'FOMC yesterday', 'extractor.news'),
                ('SPY', 'central_bank', CURRENT_DATE,
                 'FOMC today', 'extractor.news'),
                ('SPY', 'central_bank', CURRENT_DATE + INTERVAL '60 days',
                 'FOMC in two months', 'extractor.news');");
        var count = await conn.ExecuteScalarAsync<long>(@"
            SELECT COUNT(*) FROM catalysts
            WHERE status = 'active'
              AND symbol = 'SPY'
              AND occurs_on BETWEEN
                  CURRENT_DATE - INTERVAL '14 days'
                  AND CURRENT_DATE + INTERVAL '14 days';");
        Assert.Equal(2, (int)count);
    }

    [Fact]
    public async Task Dismiss_excludes_row_from_active_default()
    {
        await using var conn = await _fx.Db.OpenConnectionAsync();
        await conn.ExecuteAsync(@"
            INSERT INTO catalysts (symbol, kind, occurs_on, title, source)
            VALUES ('TSLA', 'operator_note', CURRENT_DATE, 'noise', 'operator');");
        var id = await conn.ExecuteScalarAsync<long>(@"
            SELECT id FROM catalysts WHERE symbol = 'TSLA';");
        await conn.ExecuteAsync(@"
            UPDATE catalysts SET status = 'dismissed', updated_at_utc = NOW()
            WHERE id = @id;",
            new { id });
        var activeCount = await conn.ExecuteScalarAsync<long>(@"
            SELECT COUNT(*) FROM catalysts
            WHERE status = 'active' AND symbol = 'TSLA';");
        Assert.Equal(0, (int)activeCount);
    }

    [Fact]
    public async Task Severity_ordering_high_before_medium_before_low()
    {
        // Validates the ORDER BY CASE expression the endpoint uses.
        await using var conn = await _fx.Db.OpenConnectionAsync();
        await conn.ExecuteAsync(@"
            INSERT INTO catalysts (symbol, kind, occurs_on, title, source, severity) VALUES
                ('A', 'operator_note', CURRENT_DATE, 'low', 'operator', 'low'),
                ('B', 'operator_note', CURRENT_DATE, 'high', 'operator', 'high'),
                ('C', 'operator_note', CURRENT_DATE, 'medium', 'operator', 'medium');");
        var symbols = (await conn.QueryAsync<string>(@"
            SELECT symbol FROM catalysts
            WHERE status = 'active'
            ORDER BY
                CASE severity
                    WHEN 'high' THEN 0
                    WHEN 'medium' THEN 1
                    WHEN 'low' THEN 2
                    ELSE 3
                END,
                occurs_on,
                symbol;")).AsList();
        Assert.Equal(new[] { "B", "C", "A" }, symbols);
    }
}

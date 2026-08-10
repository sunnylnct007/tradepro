using Dapper;
using Npgsql;
using TradePro.Api.Providers.Finnhub;

namespace TradePro.Api.Endpoints;

/// <summary>
/// /api/earnings-calendar — the central earnings-date store (migration 062).
/// The earnings-proximity gate consults THIS table first; live per-symbol
/// Finnhub calls are fallback only (they rate-limit at universe scale —
/// the 182/728 EARNINGS_UNKNOWN storm). Filled by the nightly bulk harvest
/// (one Finnhub /calendar/earnings call for the whole market) and by the
/// owner's manual uploads (tradepro-earnings-upload).
/// </summary>
public static class EarningsCalendarEndpoints
{
    public static IEndpointRouteBuilder MapEarningsCalendarEndpoints(this IEndpointRouteBuilder app)
    {
        var g = app.MapGroup("/earnings-calendar").WithTags("EarningsCalendar");

        // Dates near today for one symbol: past `back` days + next `ahead` days.
        // Carries store coverage so a caller can tell "no row for X" apart from
        // "store is empty/stale" — an empty store must read as CAN'T-VERIFY,
        // never as earnings-clear (the fail-open trap).
        g.MapGet("/{symbol}", async (string symbol, NpgsqlDataSource db, int? back, int? ahead) =>
        {
            var sym = symbol.Trim().ToUpperInvariant();
            var b = back is > 0 and <= 400 ? back.Value : 30;
            var a = ahead is > 0 and <= 400 ? ahead.Value : 45;
            await using var conn = await db.OpenConnectionAsync();
            var rows = (await conn.QueryAsync(@"
                SELECT report_date, session, source, uploaded_at
                FROM earnings_calendar
                WHERE symbol = @sym
                  AND report_date BETWEEN CURRENT_DATE - @b AND CURRENT_DATE + @a
                ORDER BY report_date ASC;",
                new { sym, b, a })).AsList();
            var coverage = await conn.QuerySingleAsync(@"
                SELECT COUNT(*)::int AS total_rows,
                       COUNT(DISTINCT symbol)::int AS symbols,
                       MAX(uploaded_at) AS last_upload_utc
                FROM earnings_calendar;");
            return Results.Ok(new
            {
                symbol = sym,
                events = rows,
                store = new
                {
                    totalRows = (int)coverage.total_rows,
                    symbols = (int)coverage.symbols,
                    lastUploadUtc = (DateTime?)coverage.last_upload_utc,
                },
            });
        });

        // Owner upload / harvest push: batch upsert. Last write wins per
        // (symbol, report_date) — a corrected date is a NEW row, so uploads
        // should carry the full set they know about.
        g.MapPost("/", async (EarningsCalendarBatchBody body, NpgsqlDataSource db) =>
        {
            if (body?.Rows is null || body.Rows.Count == 0)
                return Results.BadRequest(new { error = "rows required" });
            var bad = body.Rows.FirstOrDefault(r =>
                string.IsNullOrWhiteSpace(r.Symbol) || !DateOnly.TryParse(r.ReportDate, out _));
            if (bad is not null)
                return Results.BadRequest(new
                {
                    error = $"invalid row (symbol='{bad?.Symbol}', report_date='{bad?.ReportDate}'): "
                          + "symbol and an ISO report_date are required — refusing a partial poison batch",
                });
            await using var conn = await db.OpenConnectionAsync();
            foreach (var r in body.Rows)
            {
                await conn.ExecuteAsync(@"
                    INSERT INTO earnings_calendar (symbol, report_date, session, source)
                    VALUES (@Symbol, @ReportDate::date, @Session, COALESCE(@Source, 'owner_upload'))
                    ON CONFLICT (symbol, report_date) DO UPDATE SET
                        session = EXCLUDED.session, source = EXCLUDED.source,
                        uploaded_at = NOW();",
                    new { Symbol = r.Symbol.Trim().ToUpperInvariant(), r.ReportDate, r.Session, r.Source });
            }
            return Results.Ok(new { ok = true, upserted = body.Rows.Count });
        });

        // Nightly bulk harvest: ONE Finnhub /calendar/earnings?from&to call for
        // the entire market — no per-symbol fan-out, so no rate-limit storm.
        // Triggered by the Mac's launchd job (tradepro-earnings-harvest); the
        // server owns the Finnhub key. Explicit {enabled:false} when Finnhub
        // isn't configured — never a silent empty harvest.
        g.MapPost("/harvest", async (NpgsqlDataSource db, FinnhubClient finnhub,
            CancellationToken ct, int? back, int? ahead) =>
        {
            var b = back is > 0 and <= 60 ? back.Value : 14;
            var a = ahead is > 0 and <= 120 ? ahead.Value : 45;
            var from = DateOnly.FromDateTime(DateTime.UtcNow).AddDays(-b);
            var to = DateOnly.FromDateTime(DateTime.UtcNow).AddDays(a);
            var events = await finnhub.GetEarningsCalendarBulkAsync(from, to, ct);
            if (events is null)
                return Results.Ok(new
                {
                    enabled = false,
                    message = "Finnhub integration is disabled — set Finnhub:ApiKey in config. "
                            + "Store NOT updated (an empty harvest must be loud, not silent).",
                    upserted = 0,
                });
            await using var conn = await db.OpenConnectionAsync(ct);
            var n = 0;
            foreach (var e in events)
            {
                if (string.IsNullOrWhiteSpace(e.Symbol) || !DateOnly.TryParse(e.Date, out _))
                    continue;
                await conn.ExecuteAsync(@"
                    INSERT INTO earnings_calendar (symbol, report_date, session, source)
                    VALUES (@sym, @date::date, @session, 'finnhub_bulk')
                    ON CONFLICT (symbol, report_date) DO UPDATE SET
                        session = EXCLUDED.session, source = EXCLUDED.source,
                        uploaded_at = NOW();",
                    new { sym = e.Symbol.Trim().ToUpperInvariant(), date = e.Date, session = e.Hour });
                n++;
            }
            return Results.Ok(new
            {
                enabled = true,
                from = from.ToString("yyyy-MM-dd"),
                to = to.ToString("yyyy-MM-dd"),
                fetched = events.Count,
                upserted = n,
            });
        });

        return app;
    }
}

public sealed record EarningsCalendarRowBody(
    string Symbol,
    string ReportDate,   // ISO YYYY-MM-DD
    string? Session,     // 'bmo' / 'amc' / '' — null = unknown
    string? Source);

public sealed record EarningsCalendarBatchBody(List<EarningsCalendarRowBody> Rows);

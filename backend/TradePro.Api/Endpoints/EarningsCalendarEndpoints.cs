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

            // CHUNKED (12 Aug 2026): one call for the whole window came back
            // at EXACTLY 1500 rows — Finnhub silently truncates large bulk
            // responses, and the truncation cost us real reports (UBER's
            // early-Aug and AAPL's 31-Jul prints were absent → the exact
            // EARNINGS_UNKNOWN penalties the store exists to end). Weekly
            // sub-windows keep every response far below the cap (~9 calls
            // for the default 59-day window — still no per-symbol fan-out).
            await using var conn = await db.OpenConnectionAsync(ct);
            var n = 0;
            var fetched = 0;
            var chunks = 0;
            var suspectTruncation = new List<string>();
            var anyEnabled = false;
            async Task<int> UpsertAsync(IReadOnlyList<Providers.Finnhub.FinnhubEarningsEvent> events)
            {
                var count = 0;
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
                    count++;
                }
                return count;
            }

            for (var start = from; start <= to; start = start.AddDays(7))
            {
                var end = start.AddDays(6) < to ? start.AddDays(6) : to;
                var events = await finnhub.GetEarningsCalendarBulkAsync(start, end, ct);
                if (events is null)
                    continue;   // disabled or transient failure — graded below
                anyEnabled = true;
                chunks++;
                if (events.Count >= 1000)
                {
                    // ADAPTIVE SPLIT (12 Aug 2026, round two): peak earnings-season
                    // weeks genuinely exceed the cap on their own (05-11 Aug came
                    // back at exactly 1500 — UBER's 6-Aug report was STILL missing
                    // after weekly chunking). Re-fetch the suspect week day by day;
                    // a single DAY at ≥1000 is Finnhub's floor and stays flagged.
                    for (var d = start; d <= end; d = d.AddDays(1))
                    {
                        var dayEvents = await finnhub.GetEarningsCalendarBulkAsync(d, d, ct);
                        if (dayEvents is null)
                            continue;
                        chunks++;
                        fetched += dayEvents.Count;
                        if (dayEvents.Count >= 1000)
                            suspectTruncation.Add($"{d:yyyy-MM-dd}={dayEvents.Count} (single day at cap)");
                        n += await UpsertAsync(dayEvents);
                    }
                    continue;
                }
                fetched += events.Count;
                n += await UpsertAsync(events);
            }
            if (!anyEnabled)
                return Results.Ok(new
                {
                    enabled = false,
                    message = "Finnhub integration is disabled or every chunk failed — "
                            + "store NOT updated (an empty harvest must be loud, not silent).",
                    upserted = 0,
                });
            return Results.Ok(new
            {
                enabled = true,
                from = from.ToString("yyyy-MM-dd"),
                to = to.ToString("yyyy-MM-dd"),
                chunks,
                fetched,
                upserted = n,
                // Fail-loud: a ≥1000-row weekly chunk is probably capped again —
                // the caller surfaces this in run_log so truncation can never
                // be silent twice.
                suspectedTruncation = suspectTruncation.Count > 0 ? suspectTruncation : null,
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

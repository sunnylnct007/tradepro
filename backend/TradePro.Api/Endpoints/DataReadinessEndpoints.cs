using Dapper;
using Npgsql;

namespace TradePro.Api.Endpoints;

/// <summary>
/// /api/data-readiness — ONE answer to "is the data there or not, and since
/// when".
///
/// Owner, 15 Aug 2026: *"I don't need noise of failure. I need to know if data
/// is there or not and since when. The screen should be loud and clear if it
/// is usable or not. If I go to the data screen I have no proper clue."*
///
/// The existing surfaces answer the wrong question: each job reports on
/// ITSELF (run_log rows, per-lane grades, coverage matrices), so a reader has
/// to assemble the verdict by hand — which is how a 19-run 5-minute outage and
/// a four-day-stale price cache both went unnoticed while every panel looked
/// busy and green-ish.
///
/// This endpoint reports on the DATASET instead: for each one, is it usable
/// right now, what is the newest data in it, and — when it is not usable —
/// since when. No stream of warnings; one row per dataset and one headline
/// verdict.
///
/// It REPLACES the need to read run-log/bar-cache panels to decide whether
/// today's numbers can be traded on. Those panels stay for forensics.
/// </summary>
public static class DataReadinessEndpoints
{
    // A dataset is judged against the cadence it is SUPPOSED to have, not
    // against a generic timeout — daily bars may legitimately be ~3 days old
    // over a long weekend, while a 30-minute intraday lane is broken after 2h
    // of market time.
    private sealed record DatasetSpec(
        string Key, string Label, string Purpose, double MaxAgeHours, bool MarketHoursOnly);

    public static IEndpointRouteBuilder MapDataReadinessEndpoints(this IEndpointRouteBuilder app)
    {
        var g = app.MapGroup("/data-readiness").WithTags("DataReadiness");

        g.MapGet("/", async (NpgsqlDataSource db) =>
        {
            await using var conn = await db.OpenConnectionAsync();
            var now = DateTime.UtcNow;
            var rows = new List<object>();

            // ── PG-backed datasets: ask the DATA, not the job ──────────
            async Task<(DateTime? newest, long count, int symbols)> StoreStateAsync(
                string table, string tsCol, string symCol)
            {
                try
                {
                    var r = await conn.QuerySingleAsync($@"
                        SELECT MAX({tsCol}) AS newest, COUNT(*)::bigint AS n,
                               COUNT(DISTINCT {symCol})::int AS syms FROM {table};");
                    return ((DateTime?)r.newest, (long)r.n, (int)r.syms);
                }
                catch
                {
                    return (null, 0, 0);
                }
            }

            void Add(string key, string label, string purpose, bool usable,
                     DateTime? asOf, string detail, string? brokenSince = null,
                     object? extra = null)
                => rows.Add(new
                {
                    key,
                    label,
                    purpose,
                    usable,
                    asOfUtc = asOf,
                    ageHours = asOf is null ? (double?)null
                        : Math.Round((now - asOf.Value).TotalHours, 1),
                    brokenSince,
                    detail,
                    extra,
                });

            // Earnings calendar — the wheel's event gate depends on it.
            var earn = await StoreStateAsync("earnings_calendar", "uploaded_at", "symbol");
            var earnUsable = earn.newest is not null && (now - earn.newest.Value).TotalHours <= 48;
            Add("earnings_calendar", "Earnings dates",
                "blocks selling premium across a print",
                earnUsable, earn.newest,
                earn.newest is null
                    ? "EMPTY — no earnings dates stored; every proximity check reads UNVERIFIED"
                    : $"{earn.count:N0} dates across {earn.symbols:N0} symbols; last harvest "
                      + $"{(now - earn.newest.Value).TotalHours:F0}h ago"
                      + (earnUsable ? "" : " — STALE, nightly harvest has not run"),
                earnUsable ? null : earn.newest?.ToString("u"));

            // IV history — the vega gate graduates from the bridge only when
            // this matures, so "how many days deep" is the real question.
            var iv = await StoreStateAsync("options_iv_daily", "captured_at_utc", "symbol");
            int ivDepth = 0;
            try
            {
                ivDepth = await conn.ExecuteScalarAsync<int>(
                    "SELECT COALESCE(MAX(c),0) FROM (SELECT COUNT(*) c FROM options_iv_daily GROUP BY symbol) t;");
            }
            catch { /* table absent */ }
            var ivUsable = iv.newest is not null && (now - iv.newest.Value).TotalHours <= 96;
            Add("options_iv_daily", "IV history",
                "needed for a real IV-Rank; until ~60 days the screen uses the IV/HV bridge",
                ivUsable, iv.newest,
                iv.newest is null
                    ? "EMPTY — no IV points captured"
                    : $"deepest symbol has {ivDepth} day(s) of {60} needed; {iv.symbols} symbols; "
                      + $"newest point {(now - iv.newest.Value).TotalHours:F0}h ago"
                      + (ivUsable ? "" : " — STALE, no new points"),
                ivUsable ? null : iv.newest?.ToString("u"),
                new { depthDays = ivDepth, neededDays = 60 });

            // Own option quotes — the forward dataset for future backtests.
            var oq = await StoreStateAsync("option_quote_daily", "captured_at_utc", "symbol");
            var oqUsable = oq.newest is not null && (now - oq.newest.Value).TotalHours <= 96;
            Add("option_quote_daily", "Option quotes (own capture)",
                "the only source for a future options backtest — IBKR serves no expired-contract history",
                oqUsable, oq.newest,
                oq.newest is null
                    ? "EMPTY — capture has never run"
                    : $"{oq.count:N0} contract-days across {oq.symbols} symbols; newest "
                      + $"{(now - oq.newest.Value).TotalHours:F0}h ago"
                      + (oqUsable ? "" : " — STALE, screens are not capturing"),
                oqUsable ? null : oq.newest?.ToString("u"));

            // ── run_log-derived lanes: consecutive-degradation detection ──
            // A single "partial" is noise; N in a row with no improvement is
            // an outage. This is what turns 19 unnoticed 5m runs into one
            // sentence with a start time.
            async Task AddLaneAsync(string key, string label, string purpose,
                                    string process, string match, double maxAgeH)
            {
                var lane = (await conn.QueryAsync(@"
                    SELECT status, summary, created_at_utc
                    FROM run_log
                    WHERE process = @process
                      AND (@match = '' OR summary LIKE '%' || @match || '%')
                    ORDER BY created_at_utc DESC
                    LIMIT 60;", new { process, match })).AsList();

                if (lane.Count == 0)
                {
                    Add(key, label, purpose, false, null,
                        "NO RUNS RECORDED — this lane has never reported");
                    return;
                }

                var newest = (DateTime)lane[0].created_at_utc;
                var ageH = (now - newest).TotalHours;
                // Walk back to the last genuinely healthy run.
                DateTime? lastGood = null;
                var consecutiveBad = 0;
                foreach (var r in lane)
                {
                    var st = ((string)r.status ?? "").ToLowerInvariant();
                    var good = st == "ok";
                    if (good) { lastGood = (DateTime)r.created_at_utc; break; }
                    consecutiveBad++;
                }

                var ranRecently = ageH <= maxAgeH;
                var usable = ranRecently && consecutiveBad == 0;
                string detail;
                string? since = null;
                if (!ranRecently)
                {
                    detail = $"has not run for {ageH:F0}h (expected within {maxAgeH:F0}h) — "
                           + $"last status '{lane[0].status}'";
                    since = newest.ToString("u");
                }
                else if (consecutiveBad > 0)
                {
                    var firstBad = (DateTime)lane[Math.Min(consecutiveBad, lane.Count) - 1].created_at_utc;
                    since = firstBad.ToString("u");
                    detail = $"{consecutiveBad} consecutive degraded run(s) since "
                           + $"{firstBad:yyyy-MM-dd HH:mm}Z ({(now - firstBad).TotalHours:F0}h) — "
                           + $"latest: {Trim((string?)lane[0].summary)}"
                           + (lastGood is null ? "; no healthy run in the last 60"
                              : $"; last healthy {lastGood:yyyy-MM-dd HH:mm}Z");
                }
                else
                {
                    detail = $"healthy — {Trim((string?)lane[0].summary)}";
                }
                Add(key, label, purpose, usable, newest, detail, since,
                    new { consecutiveDegraded = consecutiveBad });
            }

            await AddLaneAsync("bars_1d", "Daily bars (1d)",
                "every regime, Ichimoku, HV and backtest figure", "bar-cache-harvest", "1d", 30);
            await AddLaneAsync("bars_5m", "Intraday bars (5m)",
                "intraday strategies + microstructure", "bar-cache-harvest", "5m", 3);
            await AddLaneAsync("bars_1m", "Intraday bars (1m)",
                "intraday strategies", "bar-cache-harvest", "1m", 6);
            await AddLaneAsync("options_screen", "Options screen",
                "the wheel candidate board you trade from", "options-screen", "screened", 30);

            var usableCount = rows.Count(r => (bool)r.GetType().GetProperty("usable")!.GetValue(r)!);
            var total = rows.Count;
            // The headline: one word the owner can act on without reading rows.
            var verdict = usableCount == total ? "ALL DATA CURRENT"
                : usableCount >= total - 1 ? "USABLE WITH GAPS"
                : "DEGRADED — CHECK BEFORE TRADING";

            return Results.Ok(new
            {
                generatedAtUtc = now,
                verdict,
                usable = usableCount,
                total,
                datasets = rows,
                note = "One row per DATASET (not per job). 'usable' answers whether the data "
                     + "is there and current enough to act on; 'brokenSince' says when it "
                     + "stopped. Forensics live in the run-log panel.",
            });
        });

        return app;

        static string Trim(string? s)
            => string.IsNullOrWhiteSpace(s) ? "(no summary)"
               : (s.Length <= 90 ? s : s[..90] + "…");
    }
}

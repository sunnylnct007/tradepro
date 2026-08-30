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

    /// <summary>
    /// Pick which run in a lane's history should GRADE the lane, given each
    /// run's covered-symbol count, newest first.
    ///
    /// Exists because an incidental single-symbol fetch was overwriting the
    /// health of an entire dataset (30 Aug 2026): bars_1d reported "all 1
    /// symbols covered" and usable:true while the real nightly harvest had run
    /// 244 symbols with zero missing. Coverage is a missing/total ratio, so a
    /// 1-of-1 run scores 0% missing and grades as perfect — the fail-open
    /// direction, which is the silent one.
    ///
    /// Returns the index of the newest run covering at least half the lane's
    /// own recent maximum, plus how many newer partial runs were skipped.
    /// Returns (-1, 0) when nothing qualifies, and the caller falls back to
    /// grading the newest row as before.
    /// </summary>
    public static (int Index, int PartialsIgnored) PickGradedRun(
        IReadOnlyList<int> coveredNewestFirst)
    {
        if (coveredNewestFirst is null || coveredNewestFirst.Count == 0) return (-1, 0);

        // THE BASELINE IS A PERCENTILE, NOT THE MAXIMUM — and that distinction
        // is load-bearing. Using the all-time max broke bars_5m within minutes
        // of shipping: that lane's history still contains runs of 955 symbols
        // from a universe-resolution bug that was deliberately fixed down to
        // 244. Half of 955 is 478, so every CORRECT 244-symbol run scored as a
        // partial fetch, all of them were discarded, and a healthy lane
        // reported "has not run for 98h". A monitor that cries wolf after a
        // legitimate universe change is no better than one that fails open.
        //
        // The 75th percentile is robust in both directions at once: small
        // ad-hoc fetches sit below it however many there are, and a shrunken
        // universe moves it down with the lane instead of pinning it to a size
        // the lane no longer has.
        var sorted = coveredNewestFirst.OrderBy(x => x).ToList();
        var laneSize = sorted[(int)Math.Min(sorted.Count - 1,
                                            Math.Floor(sorted.Count * 0.75))];
        // A lane that has only ever reported one symbol has no "normal" to
        // compare against — do not invent one.
        if (laneSize <= 1) return (-1, 0);
        // Half the lane's typical size. Deliberately generous: the aim is to
        // exclude 1-of-244 fetches, not to police a harvest that legitimately
        // skipped a few delisted names.
        var floor = (int)Math.Ceiling(laneSize * 0.5);
        for (var i = 0; i < coveredNewestFirst.Count; i++)
            if (coveredNewestFirst[i] >= floor) return (i, i);
        return (-1, 0);
    }

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
            // Hours of EXPECTED-RUN time between two instants. Weekend hours do
            // not count for lanes whose harvest is scheduled Mon-Fri only:
            // measuring those in raw wall-clock makes Friday's perfectly good
            // run read as an outage all weekend, every weekend. Same cry-wolf
            // class as grading on the status word (fixed 9255cc5) — a banner
            // that is red for reasons the operator cannot act on trains them to
            // ignore the one that matters.
            static double ExpectedRunHours(DateTime from, DateTime to)
            {
                if (to <= from) return 0;
                double hours = 0;
                var cursor = from;
                while (cursor < to)
                {
                    var next = cursor.Date.AddDays(1);
                    if (next > to) next = to;
                    if (cursor.DayOfWeek != DayOfWeek.Saturday &&
                        cursor.DayOfWeek != DayOfWeek.Sunday)
                        hours += (next - cursor).TotalHours;
                    cursor = next;
                }
                return hours;
            }

            async Task AddLaneAsync(string key, string label, string purpose,
                                    string process, string match, double maxAgeH,
                                    bool weekdayOnly = false)
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

                // Judge staleness against the schedule the lane ACTUALLY has.
                var effectiveAgeH = weekdayOnly ? ExpectedRunHours(newest, now) : ageH;
                var ranRecently = effectiveAgeH <= maxAgeH;

                // GRADE THE DATA, NOT THE JOB'S STATUS WORD. The harvest
                // summary carries the only thing that answers "is the data
                // there": "1d 179 sym -> 162G/0S/17B/0M". M = symbols with NO
                // data. B(ronze) means the bars ARE complete but came from the
                // yfinance fallback rather than IBKR — a provenance note, not
                // an absence. Grading on status='partial' reported an 11-day
                // daily-bar outage when 177 of 179 symbols were complete and
                // ZERO were missing: precisely the false alarm this endpoint
                // exists to prevent.
                // AN INCIDENTAL FETCH IS NOT A LANE HEALTH REPORT.
                //
                // 30 Aug 2026: bars_1d reported "all 1 symbols covered" and
                // usable:true while the actual nightly harvest had run 244
                // symbols, 244 GOLD, zero missing. What happened is that the
                // swing refresh did a single-symbol cache-miss fetch, and THAT
                // wrote the newest run_log row for the lane. Coverage below is
                // computed as missing/(covered+missing), so a 1-of-1 run scores
                // 0% missing and grades as perfectly healthy.
                //
                // So ANY ad-hoc single-symbol fetch silently overwrote the
                // health of the dataset feeding every regime, Ichimoku, HV and
                // backtest figure — and it did so for three runs without an
                // alarm, because the fail-open direction is the silent one.
                //
                // Fix: grade the newest run that actually covered a
                // LANE-SIZED universe. A run covering a small fraction of what
                // this lane normally does is a partial fetch, not a harvest,
                // and is skipped for grading (it is still reported, so the
                // operator can see one happened).
                var parsed = new List<(DateTime At, string Status, string Summary,
                                       int G, int S, int B, int M, int Total)>();
                foreach (var r in lane)
                {
                    var txt = (string?)r.summary ?? "";
                    var mm = System.Text.RegularExpressions.Regex.Match(
                        txt, @"(\d+)G/(\d+)S/(\d+)B/(\d+)M");
                    if (!mm.Success) continue;
                    int g = int.Parse(mm.Groups[1].Value), s = int.Parse(mm.Groups[2].Value);
                    int b = int.Parse(mm.Groups[3].Value), mis = int.Parse(mm.Groups[4].Value);
                    parsed.Add(((DateTime)r.created_at_utc, ((string)r.status ?? ""),
                                txt, g, s, b, mis, g + s + b + mis));
                }
                var laneSize = parsed.Count > 0 ? parsed.Max(p => p.Total) : 0;
                var pick = PickGradedRun(parsed.Select(p => p.Total).ToList());
                var graded = pick.Index >= 0 ? parsed[pick.Index] : default;
                var partialSkipped = pick.PartialsIgnored;
                if (graded.Total > 0)
                {
                    if (partialSkipped > 0)
                    {
                        // Re-age against the last REAL harvest, not the fetch.
                        newest = graded.At;
                        ageH = (now - newest).TotalHours;
                        effectiveAgeH = weekdayOnly ? ExpectedRunHours(newest, now) : ageH;
                        ranRecently = effectiveAgeH <= maxAgeH;
                    }
                }

                var summaryText = graded.Total > 0
                    ? graded.Summary
                    : (string?)lane[0].summary ?? "";
                var m = System.Text.RegularExpressions.Regex.Match(
                    summaryText, @"(\d+)G/(\d+)S/(\d+)B/(\d+)M");
                int? missing = null, gold = null, bronze = null, silver = null;
                if (m.Success)
                {
                    gold = int.Parse(m.Groups[1].Value);
                    silver = int.Parse(m.Groups[2].Value);
                    bronze = int.Parse(m.Groups[3].Value);
                    missing = int.Parse(m.Groups[4].Value);
                }
                var covered = (gold ?? 0) + (silver ?? 0) + (bronze ?? 0);
                var totalSyms = covered + (missing ?? 0);
                // Unusable only when the data is genuinely absent for a
                // meaningful slice, or the lane has stopped running.
                var missingShare = totalSyms > 0 ? (double)(missing ?? 0) / totalSyms : 0.0;
                var usable = ranRecently && (missing is null || missingShare <= 0.10);

                string detail;
                string? since = null;
                if (!ranRecently)
                {
                    detail = $"has not run for {ageH:F0}h"
                           + (weekdayOnly && effectiveAgeH < ageH - 1
                                ? $" ({effectiveAgeH:F0}h of them weekday time)" : "")
                           + $" (expected within {maxAgeH:F0}h) — "
                           + $"last status '{lane[0].status}'";
                    since = newest.ToString("u");
                }
                else if (missing is > 0 && missingShare > 0.10)
                {
                    since = lastGood?.ToString("u");
                    detail = $"{missing} of {totalSyms} symbols have NO data"
                           + (lastGood is null ? "" : $"; last fully-covered run {lastGood:yyyy-MM-dd HH:mm}Z");
                }
                else if (missing is not null)
                {
                    var fallbackNote = bronze > 0
                        ? $", {bronze} from the yfinance fallback (complete, lower provenance)"
                        : "";
                    detail = $"all {totalSyms} symbols covered — {gold} from IBKR{fallbackNote}"
                           + (missing > 0 ? $"; {missing} missing (within tolerance)" : "")
                           + $"; last run {ageH:F1}h ago";
                }
                else
                {
                    detail = $"ran {ageH:F1}h ago — {Trim(summaryText)}";
                }
                if (partialSkipped > 0)
                {
                    detail += $"; ignored {partialSkipped} partial fetch(es) newer than "
                            + "this harvest (single-symbol cache misses are not lane coverage)";
                }
                Add(key, label, purpose, usable, newest, detail, since,
                    new { gold, silver, bronzeFallback = bronze, missing,
                          consecutiveDegradedRuns = consecutiveBad,
                          laneSize, partialRunsIgnored = partialSkipped });
            }

            // maxAgeH must match each lane's REAL launchd cadence, or the banner
            // reports an outage the operator cannot fix because none exists:
            //   1d  com.tradepro.bar-cache-harvest-daily  21:30 Mon-Fri  (once daily)
            //   5m  com.tradepro.bar-cache-harvest-5m     StartInterval 1800 (continuous)
            //   1m  com.tradepro.bar-cache-harvest        21:15 Mon-Fri  (once daily)
            // 1m was registered at 6h against a job that runs every 24h — it was
            // therefore RED for 18 hours out of every 24, and all weekend, while
            // the harvest was in fact completing normally (15 Aug: 251/251
            // symbols, 0 failed). Corrected to 30h + weekday-only, matching 1d.
            await AddLaneAsync("bars_1d", "Daily bars (1d)",
                "every regime, Ichimoku, HV and backtest figure", "bar-cache-harvest", "1d", 30,
                weekdayOnly: true);
            await AddLaneAsync("bars_5m", "Intraday bars (5m)",
                "intraday strategies + microstructure", "bar-cache-harvest", "5m", 3);
            await AddLaneAsync("bars_1m", "Intraday bars (1m)",
                "intraday strategies", "bar-cache-harvest", "1m", 30,
                weekdayOnly: true);
            // weekdayOnly, like the bar lanes. Without it a Friday-evening run
            // reads as "has not run for 44h" by Sunday afternoon, of which only
            // ~4h is weekday time — the same weekend false alarm already fixed
            // for the IBKR health probe in 3f252df. It sent an external reviewer
            // chasing a scheduling outage that did not exist while the REAL
            // fault (37 consecutive degraded runs) sat untouched.
            await AddLaneAsync("options_screen", "Options screen",
                "the wheel candidate board you trade from", "options-screen", "screened", 30,
                weekdayOnly: true);

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

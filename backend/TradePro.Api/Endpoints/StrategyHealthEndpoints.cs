using System.Text.RegularExpressions;
using Dapper;
using Npgsql;

namespace TradePro.Api.Endpoints;

/// <summary>
/// GET /api/strategies/health — per-strategy activity + outcome health so a
/// silently-stopped strategy is VISIBLE on the cockpit instead of buried in /tmp
/// logs (see feedback_surface_system_health_in_ui).
///
/// Source of truth is the ORDER stream (oms_orders) the LIVE paper daemons write
/// — NOT strategy_decisions (only the comparison/trade-plan flow writes that, so
/// it goes stale even while the daemon trades and made this panel cry wolf once).
///
/// IMPORTANT: oms_orders also carries PER-SYMBOL engine ids like
/// "intraday-ichimoku_equity-AAPL" / "intraday-bollinger_bounce-MSFT" — hundreds
/// of them. We AGGREGATE those into ONE row per base strategy (with a symbol
/// count) so the panel shows a handful of rows, not 500. The 3 main paper
/// strategies stay individual.
///
///   status: healthy  — filled at least one order today
///           blocked  — placed orders today but ALL cancelled, none filled
///           idle     — traded recently (≤30h) but no fills today
///           stale    — no orders in >30h, the lane is NOT ticking either, and
///                      the strategy is still REGISTERED. A real fault.
///           retired  — no orders AND not in the pushed strategy registry: not
///                      trading BY DECISION. NOT a fault, and deliberately does
///                      not count toward the panel's "needs attention" — five
///                      retired sleeves held it red every day until 13 Oct 2026,
///                      which is how a genuinely stopped sleeve gets ignored.
///           unknown  — no orders on record
///
/// retired-vs-stale needs the registry, which only the Mac can introspect
/// (tradepro-paper-strategies-push). If the catalogue is missing or empty the
/// endpoint FAILS CLOSED: nothing is called retired, and the stale reason says
/// the two cannot be told apart. "Registry unavailable" must never render as
/// "nothing is wrong".
///
/// Registered on the `api` group → map "/strategies/health" (NOT "/api/..").
/// </summary>
public static class StrategyHealthEndpoints
{
    // Optional nice display names. The strategy LIST is NOT hardcoded — it's
    // loaded from strategy_broker_map (the single source of truth) at request
    // time, so onboarding a new strategy/broker is a config row, not a code edit
    // here. This map only prettifies labels; unknown ids fall back to "id (broker)".
    private static readonly Dictionary<string, string> LabelOverrides = new()
    {
        ["ichimoku_equity"]      = "Ichimoku Equity (T212 cash)",
        ["ichimoku_equity_ibkr"] = "Ichimoku Equity (IBKR paper · protected)",
        ["intraday_flat"]        = "Intraday Flat (IG 24h CFD)",
        ["ichimoku_fx_mr"]       = "Ichimoku FX MR (IG FX)",
    };

    // Per-symbol engine id → base, e.g. "intraday-ichimoku_equity-AAPL" → "intraday-ichimoku_equity".
    private static readonly Regex PerSymbol = new(@"^(.*)-[A-Z0-9.]{1,6}$", RegexOptions.Compiled);

    // Lane id → registry name. A lane is "<registry name>_<broker>", e.g.
    // momentum_pullback_t212 and momentum_pullback_ibkr are both the registered
    // strategy `momentum_pullback`. The registry knows nothing about brokers, so
    // the suffix must come off before asking it whether a strategy exists.
    //
    // Suffixes are listed rather than inferred: stripping "everything after the
    // last underscore" would turn mean_reversion_swing into mean_reversion, and
    // an unknown-but-registered strategy would read as retired. If a new broker
    // appears and is not listed here, BaseName returns the id unchanged, the
    // registry lookup misses, and the sleeve shows as retired — so the list is
    // checked by a test against the live lane ids.
    private static readonly string[] BrokerSuffixes =
        { "_ibkr", "_t212", "_ig", "_paper", "_live", "_demo" };

    // PUBLIC so the test project can pin it. No InternalsVisibleTo in this
    // solution, and this is the lookup that decides RETIRED vs STALE — an
    // untested hand-maintained suffix list is precisely the shape that keeps
    // misdescribing this panel.
    public static string BaseName(string laneId)
    {
        foreach (var s in BrokerSuffixes)
        {
            if (laneId.EndsWith(s, StringComparison.OrdinalIgnoreCase))
                return laneId[..^s.Length];
        }
        return laneId;
    }

    /// <summary>"&lt;strategyId&gt;-yyyy-MM-dd" -> strategyId.
    ///
    /// Trims from the LAST dash-date, not the first dash: strategy ids contain
    /// dashes themselves ("intraday-ichimoku_equity-AAPL"), so splitting on the
    /// first would reduce that to "intraday" and merge every per-symbol engine
    /// into one phantom sleeve. Public so the parsing is pinned by a test —
    /// this decides whether a lane reads as RUNNING or STOPPED.</summary>
    public static string StrategyIdFromSessionLabel(string label)
    {
        var cut = label.LastIndexOf('-');
        for (int i = 0; i < 2 && cut > 0; i++) cut = label.LastIndexOf('-', cut - 1);
        return cut > 0 ? label[..cut] : label;
    }

    private static string GroupOf(string id, HashSet<string> configured)
    {
        if (configured.Contains(id)) return id;           // configured strategies keep their id
        var m = PerSymbol.Match(id);
        return m.Success ? m.Groups[1].Value : id;        // collapse per-symbol ids to their base
    }

    public static IEndpointRouteBuilder MapStrategyHealthEndpoints(this IEndpointRouteBuilder app)
    {
        app.MapGet("/strategies/health", async (
            NpgsqlDataSource db,
            TradePro.Api.Simulation.IPaperStrategiesStore catalogue,
            TradePro.Api.Simulation.IPaperSnapshotStore snapshots) =>
        {
            await using var conn = await db.OpenConnectionAsync();

            // DID THE LANE TICK? — as distinct from "did it place an order".
            //
            // Every lane pushes a snapshot each pass (/api/ingest/paper-snapshot),
            // session-labelled "<strategyId>-<date>". That is a HEARTBEAT, and it
            // is the honest liveness signal: a lane can be perfectly healthy and
            // place nothing for days.
            //
            // Two cases where the order-based test lies, both live on 16 Oct 2026:
            //   * momentum_pullback_ibkr is in WIND-DOWN. It places exits only,
            //     by design. It will NEVER place an entry again, so it goes
            //     "stale" every single day, forever.
            //   * swing fires on roughly 31% of sessions. 33-42h dry is the rule
            //     working, not a defect — already written down as such.
            // Both read "no orders in Nh — registered, so it SHOULD be trading",
            // which is false, and is the cry-wolf shape three fixes this week
            // already removed from this same panel.
            var lastTick = new Dictionary<string, DateTime>(StringComparer.OrdinalIgnoreCase);
            try
            {
                foreach (var snap in snapshots.List(200))
                {
                    // "<strategyId>-yyyy-MM-dd" -> strategyId. Trim from the LAST
                    // dash-date, not the first dash: strategy ids contain dashes
                    // ("intraday-ichimoku_equity-AAPL").
                    var sid = StrategyIdFromSessionLabel(snap.SessionLabel ?? "");
                    if (sid.Length == 0) continue;
                    if (!lastTick.TryGetValue(sid, out var cur) || snap.ReceivedAtUtc > cur)
                        lastTick[sid] = snap.ReceivedAtUtc;
                }
            }
            catch { /* no heartbeats is "cannot tell", handled at the use site */ }

            // WHICH STRATEGIES STILL EXIST. The Mac introspects its Python
            // registry and pushes the catalogue (tradepro-paper-strategies-push);
            // the API box has no Python, so this store is the only thing here
            // that knows the difference between a sleeve that STOPPED and one
            // that was RETIRED.
            //
            // FAIL CLOSED. `registryKnown` is false when the catalogue has never
            // been pushed or reads empty, and nothing is then called retired —
            // because "the registry is unavailable" must never be rendered as
            // "nothing is wrong". On 13 Oct the catalogue was genuinely empty
            // until pushed, so this path is the normal one on a fresh box, not a
            // theoretical edge.
            var registered = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
            var registryKnown = false;
            string? registryError = null;
            try
            {
                var cur = catalogue.Get();
                if (cur is System.Text.Json.JsonElement el
                    && el.ValueKind == System.Text.Json.JsonValueKind.Object
                    && el.TryGetProperty("strategies", out var arr)
                    && arr.ValueKind == System.Text.Json.JsonValueKind.Array)
                {
                    foreach (var s in arr.EnumerateArray())
                    {
                        if (s.TryGetProperty("name", out var n) && n.GetString() is { Length: > 0 } nm)
                            registered.Add(nm);
                    }
                    registryKnown = registered.Count > 0;
                }

                // A STALE CATALOGUE IS NOT A TRUSTWORTHY ONE, and the dangerous
                // direction is the quiet one: a NEWLY registered strategy that
                // the catalogue has not caught up with is "not in the registry",
                // so if it stopped trading it would read RETIRED — a fault
                // rendered as a decision. That is the silent-degradation shape,
                // and unlike a false red it is invisible.
                //
                // The push now runs on every Mac deploy (mac-deploy-sync.sh), so
                // the catalogue tracks the registry automatically. This bounds
                // what happens when that FAILS. 14 days: long enough that a
                // genuinely quiet fortnight does not trip it, short enough that
                // a broken push surfaces while it still matters.
                if (registryKnown)
                {
                    // AGE IS COMPUTED IN POSTGRES, not in C#. The column is
                    // TIMESTAMPTZ; every client-side option has a way to be
                    // quietly wrong — DateTime depends on Npgsql's Kind
                    // mapping (this desk once had a monitor report
                    // "(-58 min ago)" from that and go permanently silent),
                    // and DateTimeOffset DOES NOT CAST AT ALL: Npgsql hands
                    // back a DateTime and Dapper throws
                    // "Invalid cast from 'System.DateTime' to
                    // 'System.DateTimeOffset'".
                    //
                    // That is not hypothetical — it is what shipped in #294 on
                    // 13 Oct. The throw landed in the catch below, registryKnown
                    // went false, and the retired-vs-stale classification was
                    // silently disabled on a catalogue pushed hours earlier. A
                    // guard added to prevent a silent failure caused one.
                    //
                    // NOW() - updated_at is unambiguous, timezone-correct, and
                    // crosses the boundary as a plain number.
                    var ageDays = await conn.ExecuteScalarAsync<double?>(
                        @"SELECT EXTRACT(EPOCH FROM (NOW() - updated_at)) / 86400.0
                            FROM paper_strategies WHERE id = 'singleton'");
                    if (!ageDays.HasValue || ageDays.Value > 14)
                        registryKnown = false;
                }
            }
            catch (Exception ex)
            {
                // An unreadable catalogue is "cannot tell", never "all retired".
                //
                // NAME THE CAUSE. The bare `catch` here swallowed #294's cast
                // exception for a full day: the panel said "registry
                // unavailable", which is true but unactionable, and nothing
                // anywhere said WHY. A degraded mode that cannot explain itself
                // is indistinguishable from a working one that has nothing to
                // report — the exact shape this panel keeps being bitten by.
                registryKnown = false;
                registryError = ex.GetType().Name + ": " + ex.Message;
            }

            var raw = (await conn.QueryAsync<(string Id, DateTime? LastOrder, int Fills, int Cancels, int Pending, int TodayTotal, int Working)>(
                @"SELECT strategy_id AS Id,
                         MAX(created_at_utc) AS LastOrder,
                         COUNT(*) FILTER (WHERE state IN ('FILLED','PARTIALLY_FILLED') AND created_at_utc >= date_trunc('day', NOW()))::int AS Fills,
                         -- A SUPERSEDED ORDER IS NOT A FAILED ORDER. A lane
                         -- re-emits a standing exit every tick; each new one
                         -- cancels the last with 'superseded by newer order'.
                         -- That is this desk's own bookkeeping, not a broker
                         -- or risk rejection.
                         --
                         -- Counting them made the panel report
                         -- mean_reversion_swing_ibkr as BLOCKED, 26 orders
                         -- today ALL cancelled none filled (14 Oct 2026),
                         -- while that sleeve's exit was sitting LIVE at IBKR,
                         -- PreSubmitted and queued for the open. A red that is
                         -- really a green is how the real ones get ignored —
                         -- the same cry-wolf the retired sleeves caused.
                         COUNT(*) FILTER (WHERE state = 'CANCELLED'
                                            AND COALESCE(cancelled_reason, '') NOT ILIKE 'superseded%'
                                            AND created_at_utc >= date_trunc('day', NOW()))::int AS Cancels,
                         COUNT(*) FILTER (WHERE state = 'PENDING_APPROVAL' AND created_at_utc >= date_trunc('day', NOW()))::int AS Pending,
                         COUNT(*) FILTER (WHERE created_at_utc >= date_trunc('day', NOW()))::int AS TodayTotal,
                         -- LIVE AT THE BROKER, any day. Deliberately NOT scoped
                         -- to today: an order placed after yesterday's close
                         -- sits PreSubmitted overnight and fills at the next
                         -- open. BMRN's exit did exactly that on 13 Oct, and a
                         -- today-only count made the sleeve look like it had
                         -- done nothing.
                         COUNT(*) FILTER (WHERE state IN ('SUBMITTED','WORKING','PARTIALLY_FILLED'))::int AS Working
                  FROM oms_orders
                  WHERE strategy_id IS NOT NULL
                  GROUP BY strategy_id")).ToList();

            var nowUtc = DateTime.UtcNow;

            // CONFIG-DRIVEN strategy list — the single source of truth is
            // strategy_broker_map, NOT a hardcoded array. Add a row there and this
            // panel (+ pnl-by-strategy, which reads the same table) pick the
            // strategy up automatically — onboarding is a config row, not a code edit.
            var configured = (await conn.QueryAsync<(string Id, string Broker)>(
                // DERIVE FROM THE LEDGER, NOT FROM A LIST (28 Sep 2026).
                //
                // strategy_broker_map is hand-maintained, and this is the THIRD
                // surface it has silently misdescribed. The P&L split showed
                // five retired sleeves and omitted mean_reversion_swing_ibkr
                // entirely — 194 orders, invisible, because nobody INSERTed a
                // row. This panel had the same blind spot: five dots, all
                // stale, none of them a sleeve that traded today.
                //
                // The map stays authoritative for the BROKER of a mapped
                // sleeve (routing is a decision orders cannot express). It is
                // not authoritative for EXISTENCE. Anything that has placed an
                // order exists, and oms_orders already carries the broker.
                @"SELECT strategy_id AS Id, broker AS Broker FROM strategy_broker_map
                  UNION
                  SELECT DISTINCT o.strategy_id, o.broker
                    FROM oms_orders o
                   WHERE o.strategy_id IS NOT NULL
                     AND o.broker IS NOT NULL
                     AND o.deleted_at IS NULL
                     AND o.created_at_utc > NOW() - INTERVAL '30 days'
                     AND NOT EXISTS (SELECT 1 FROM strategy_broker_map m
                                      WHERE m.strategy_id = o.strategy_id)
                   ORDER BY 1")).ToList();
            var configuredIds = configured.Select(c => c.Id).ToHashSet();

            // Aggregate by group (configured strategies = themselves; per-symbol ids collapsed).
            var groups = raw.GroupBy(r => GroupOf(r.Id, configuredIds)).ToDictionary(g => g.Key, g => g);
            // Only the CONFIGURED strategies (the ones actually wired to run). The
            // intraday-engine per-symbol groups + reconcile_from_broker aren't set up
            // to trade yet, so they'd show a permanent misleading "not running" —
            // exclude until configured (add to Known when they go live).
            var ids = configured.Select(c => c.Id);

            var strategies = ids.Select(id =>
            {
                groups.TryGetValue(id, out var g);
                var members = g?.ToList() ?? new();
                int symbolCount = members.Count;
                bool isMain = configuredIds.Contains(id);
                var broker = configured.FirstOrDefault(c => c.Id == id).Broker ?? "";
                string label = LabelOverrides.TryGetValue(id, out var lbl)
                               ? lbl
                               : (broker.Length > 0 ? $"{id} ({broker})" : id);

                DateTime? last = members.Where(m => m.LastOrder.HasValue).Select(m => m.LastOrder!.Value)
                                        .DefaultIfEmpty().Max();
                if (last == default(DateTime)) last = null;
                double? minsSince = last.HasValue ? (nowUtc - last.Value).TotalMinutes : null;
                bool stale = !last.HasValue || minsSince > 30 * 60;

                int fills = members.Sum(m => m.Fills);
                int cancels = members.Sum(m => m.Cancels);
                int pending = members.Sum(m => m.Pending);
                int total = members.Sum(m => m.TodayTotal);
                int working = members.Sum(m => m.Working);

                string status, reason;
                if (fills > 0)
                {
                    status = "healthy";
                    reason = $"{fills} filled today" + (cancels > 0 ? $" ({cancels} cancelled)" : "");
                }
                else if (total > 0 && cancels > 0)
                {
                    status = "blocked";
                    reason = $"{cancels} order(s) today, ALL cancelled — none filled";
                }
                else if (!last.HasValue)
                {
                    status = "unknown";
                    reason = "no orders on record";
                }
                // RETIRED IS NOT A FAULT, and must be ruled out BEFORE stale.
                //
                // Owner, 13 Oct 2026: five retired sleeves sat here permanently
                // red — ichimoku_equity/_ibkr, ichimoku_fx_mr/_ibkr,
                // intraday_flat — each saying "no orders in 20d/54d/50d/95d/
                // 117d — may not be trading". The panel's own `anyBad` trips on
                // `stale`, so the cockpit read "needs attention" every single
                // day, which is how a REAL stopped sleeve gets ignored. This
                // desk has shipped that failure before (2 Oct: 3 of 5 reds were
                // the check, not the desk).
                //
                // "may not be trading" was also simply false. They are not
                // trading BY DECISION, and the registry refuses to register
                // them (registry.RETIRED) — so there is nothing to attend to.
                else if (stale && registryKnown && !registered.Contains(BaseName(id)))
                {
                    status = "retired";
                    reason = $"retired — not in the strategy registry; last order "
                           + $"{FmtAge(minsSince!.Value)} ago. Not a fault.";
                }
                else if (stale && lastTick.TryGetValue(id, out var tick)
                         && (nowUtc - tick).TotalMinutes <= 90)
                {
                    // RUNNING, just not trading. 90 min: lanes tick every ~15-20,
                    // so this is several missed passes, not one slow one.
                    status = "idle";
                    reason = $"running (last pass {FmtAge((nowUtc - tick).TotalMinutes)} ago) "
                           + $"but no order in {FmtAge(minsSince!.Value)} — no qualifying "
                           + "signal, or the sleeve is winding down and places exits only";
                }
                else if (stale)
                {
                    status = "stale";
                    // SAY WHICH IT IS. When the registry could not be read we
                    // cannot tell a stopped sleeve from a retired one, and
                    // guessing either way is the silent-degradation shape: call
                    // everything retired and a real outage goes quiet; call
                    // everything stale and the panel cries wolf. Name the gap.
                    reason = registryKnown
                        ? $"no orders in {FmtAge(minsSince!.Value)} and no lane pass received — "
                          + "registered, so it SHOULD be running"
                        : $"no orders in {FmtAge(minsSince!.Value)} — may not be trading "
                          + "(registry unavailable, so retired-vs-stopped cannot be told "
                          + $"apart{(registryError is null ? "" : ": " + registryError)})";
                }
                else if (working > 0)
                {
                    // SAYING "no qualifying signal" HERE WOULD BE FALSE. The
                    // signal fired, the order is at the broker, and it is
                    // waiting for liquidity or for the open. Reported as idle
                    // (nothing to attend to) with a reason that says what is
                    // actually happening, instead of claiming the rule stayed
                    // silent.
                    status = "idle";
                    reason = working == 1
                        ? "1 order working at the broker, waiting to fill"
                        : $"{working} orders working at the broker, waiting to fill";
                }
                else
                {
                    status = "idle";
                    reason = "traded recently, no fills today (no qualifying signal)";
                }

                return new
                {
                    strategy = id,
                    label,
                    isMain,
                    symbolCount,
                    status,
                    reason,
                    lastOrderUtc = last,
                    minutesSinceOrder = minsSince.HasValue ? Math.Round(minsSince.Value, 1) : (double?)null,
                    today = new { fills, cancels, pending, total },
                };
            })
            // Main strategies first, then groups with recent activity, then the rest.
            .OrderByDescending(r => r.isMain)
            .ThenByDescending(r => r.today.fills)
            .ThenBy(r => r.strategy)
            .ToList();

            return Results.Ok(new { generatedAtUtc = nowUtc, strategies });
        }).WithTags("StrategyHealth");

        return app;
    }

    private static string FmtAge(double mins)
    {
        if (mins < 60) return $"{mins:F0}m";
        if (mins < 60 * 48) return $"{mins / 60:F0}h";
        return $"{mins / 1440:F0}d";
    }
}

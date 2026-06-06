using System.Collections.Concurrent;
using System.Text.RegularExpressions;
using Dapper;
using Npgsql;
using TradePro.Api.Providers.Finnhub;
using TradePro.Api.Providers.Trading212;

namespace TradePro.Api.Endpoints;

public static class IntegrationsEndpoints
{
    // Short-TTL cache for the IG realised-history payload, keyed by the
    // requested day-window. IG's demo API enforces a tight per-app key
    // allowance (we have already tripped `exceeded-api-key-allowance`), and
    // anti-abuse can escalate to a 403 session block. The cockpit polls this
    // endpoint, so without a cache every client + refresh hammers IG. On a
    // throttle we serve the last GOOD payload (stale-but-truthful) rather than
    // an empty strip. See memory: IG anti-abuse / broker-session caching.
    private static readonly ConcurrentDictionary<int, (DateTime At, object Payload)> _igHistoryCache = new();
    private static readonly TimeSpan _igHistoryTtl = TimeSpan.FromSeconds(60);

    // ISO-4217 codes for the currencies our FX sleeves trade. Reference data
    // (not strategy config) — used purely to recognise a currency-pair shape
    // like "EURUSD" or "AUD/USD Mini" so a transaction can be tagged FX.
    private static readonly HashSet<string> _iso4217 = new(StringComparer.OrdinalIgnoreCase)
    {
        "USD","EUR","GBP","JPY","CHF","AUD","NZD","CAD","SEK","NOK","DKK",
        "SGD","HKD","ZAR","MXN","PLN","CZK","HUF","TRY","CNH",
    };

    // Asset class of an IG instrument NAME / description or a trading symbol.
    // Currency-pair shapes → "FX"; anything else with text → "EQUITY"; empty
    // → null (unattributed). This reads the instrument as broker data — the
    // same spirit as the frontend's exchangeOf() — it is NOT a hardcoded
    // strategy→class map. Catches both deals ("AUD/USD Mini") and the fee
    // rows whose description embeds the pair ("…FX Interest…AUD/USD Mini…").
    internal static string? AssetClassOf(string? s)
    {
        if (string.IsNullOrWhiteSpace(s)) return null;
        var u = s.ToUpperInvariant();
        if (Regex.IsMatch(u, @"\b[A-Z]{3}/[A-Z]{3}\b")) return "FX";
        var first = u.Split(' ', '\t', '-')[0];
        if (Regex.IsMatch(first, @"^[A-Z]{6}$")
            && _iso4217.Contains(first[..3]) && _iso4217.Contains(first[3..]))
            return "FX";
        return "EQUITY";
    }

    // A clean instrument label for per-symbol P&L grouping: the FX pair if
    // present (deals AND financing rows both mention it), else the company
    // name with IG's noise stripped ("(24 Hours)", commission/financing
    // suffixes). So "NVIDIA Corp (24 Hours) COMM …" and the matching deal
    // both group under "NVIDIA Corp".
    internal static string NormaliseInstrument(string? s)
    {
        if (string.IsNullOrWhiteSpace(s)) return "—";
        var fx = Regex.Match(s.ToUpperInvariant(), @"\b([A-Z]{3}/[A-Z]{3})\b");
        if (fx.Success) return fx.Groups[1].Value;
        var name = s;
        foreach (var marker in new[] { " (24 Hours)", " COMM", " converted at", " - FX Interest", " Commission", " Admin Fee", " Financing" })
        {
            var idx = name.IndexOf(marker, StringComparison.OrdinalIgnoreCase);
            if (idx > 0) name = name[..idx];
        }
        return name.Trim();
    }

    public static IEndpointRouteBuilder MapIntegrationsEndpoints(this IEndpointRouteBuilder app)
    {
        // Surfaces both T212 connections in one envelope — live (reads)
        // and demo (writes). The frontend uses this to render a single
        // "Reading from LIVE • Orders execute on DEMO" banner, and to
        // gate the Approve button on demo.authenticated being true.
        app.MapGet("/integrations/trading212/status",
            async (Trading212Client live, Trading212DemoClient demo,
                   CancellationToken ct) =>
            {
                var liveStatus = await live.GetStatusAsync(ct);
                var demoStatus = await demo.GetStatusAsync(ct);
                // Top-level fields mirror the legacy single-mode shape
                // (consumers reading `mode` / `authenticated` keep
                // working — they get the LIVE side, since reads are
                // the historical default). The `live` and `demo`
                // sub-objects are the new structured view.
                return Results.Ok(new
                {
                    liveStatus.Configured,
                    liveStatus.Mode,
                    liveStatus.Reachable,
                    liveStatus.Authenticated,
                    liveStatus.Detail,
                    liveStatus.RateLimitRemaining,
                    live = liveStatus,
                    demo = demoStatus,
                });
            });

        // Cached T212 instruments registry — loads from
        // /equity/metadata/instruments on first access, refreshes every
        // 24h. Honours the T212 1-req-per-50s rate limit by holding the
        // result in a singleton service and persisting to disk so a
        // restart doesn't wipe the cache.
        app.MapGet("/integrations/trading212/instruments",
            async (
                string? q,
                int? limit,
                Trading212InstrumentsService svc,
                CancellationToken ct) =>
            {
                if (!svc.IsEnabled)
                {
                    return Results.Ok(new
                    {
                        enabled = false,
                        message = "Trading212 integration is disabled. Set Trading212:Mode and credentials.",
                        cachedCount = 0,
                        items = Array.Empty<Trading212Instrument>(),
                    });
                }
                if (string.IsNullOrWhiteSpace(q))
                {
                    var all = await svc.GetAllAsync(ct);
                    return Results.Ok(new
                    {
                        enabled = true,
                        cachedCount = svc.CachedCount,
                        loadedAtUtc = svc.LoadedAtUtc,
                        items = all.Take(Math.Clamp(limit ?? 50, 1, 500)),
                    });
                }
                var hits = await svc.SearchAsync(q, Math.Clamp(limit ?? 25, 1, 100), ct);
                return Results.Ok(new
                {
                    enabled = true,
                    query = q,
                    cachedCount = svc.CachedCount,
                    loadedAtUtc = svc.LoadedAtUtc,
                    items = hits,
                });
            });

        // Open T212 positions with computed unrealised P&L per row
        // and totals. T212's currentPrice is included so the operator
        // can reconcile against the Yahoo close that drives our
        // indicators (handy when the two diverge after a corporate
        // action or a stale Yahoo bar). Also surfaces `mode` on
        // every response — `demo` for paper trading, `live` for real
        // money — so every consumer (UI, email, MCP) can show the
        // user which world they're looking at.
        // Account cash snapshot for the chosen account. Used on the
        // Portfolio header so the operator sees "free £49,500 ·
        // invested £500" before deciding to place an order. T212
        // Invest only — CFD (FX + leveraged) uses different endpoints
        // and is a follow-up task (#39 + cfd cash).

        // GET /api/integrations/ig/positions — IG demo/live open
        // positions. Authoritative source for the Mac strategy-seed
        // path so paper-fx starts with the broker's real position
        // counts, not the OMS-derived projection (which can drift).
        app.MapGet("/integrations/ig/positions", async (
            TradePro.Api.Providers.IG.IGClient ig,
            CancellationToken ct) =>
        {
            if (!ig.IsEnabled)
            {
                return Results.Ok(new
                {
                    enabled = false,
                    mode = "disabled",
                    positions = Array.Empty<object>(),
                });
            }
            var result = await ig.GetPositionsAsync(ct);
            // Pull the per-instrument CONTRACT SIZE (units-per-contract) from the
            // broker — golden source, not a hardcoded constant — to scale P&L to
            // money. /positions only carries lotSize (1 for FX), which is NOT the
            // multiplier, so the dollar pairs showed ~$0. Cached process-wide, so
            // this is one /markets call per epic per process, then free.
            var contractSize = new Dictionary<string, decimal?>();
            foreach (var p in result.Positions)
                if (!contractSize.ContainsKey(p.Epic))
                    contractSize[p.Epic] = await ig.GetContractSizeAsync(p.Epic, ct);
            // FX P&L comes out in the QUOTE currency (the pair's last 3 letters
            // for a CS.D.<6-char>.MINI.IP epic). Convert to USD via IG rates so a
            // JPY-quoted pair isn't shown as if its yen P&L were dollars.
            static string? QuoteCcy(string epic)
            {
                var parts = epic.Split('.');
                return parts.Length >= 4 && parts[2].Length == 6 ? parts[2][3..6] : null;
            }
            var usdRate = new Dictionary<string, decimal?>();
            foreach (var p in result.Positions)
            {
                var q = QuoteCcy(p.Epic);
                if (q != null && !usdRate.ContainsKey(q))
                    usdRate[q] = await ig.GetUsdRateAsync(q, ct);
            }
            var rows = result.Positions.Select(p =>
            {
                var signedQty = p.Direction == "SELL" ? -p.Size : p.Size;
                // Live mark = mid of the broker-provided bid/offer. P&L scales by
                // the broker's contractSize (units/contract); fall back to lotSize
                // then 1 so a missing size never zeroes the row. Same uniform shape
                // (currentPrice/unrealisedAbs/Pct) the desk reads for T212 → any
                // broker that fills FX plugs in identically. NOTE: P&L is in the
                // QUOTE currency; cross/account-ccy conversion is a follow-up.
                decimal? mid = (p.Bid is decimal b && p.Offer is decimal o) ? (b + o) / 2m : null;
                decimal lot = contractSize.GetValueOrDefault(p.Epic) ?? p.LotSize ?? 1m;
                var q = QuoteCcy(p.Epic);
                decimal rate = (q != null ? usdRate.GetValueOrDefault(q) : null) ?? 1m;
                decimal? unrealisedAbs = null, unrealisedPct = null;
                if (mid is decimal m && p.EntryLevel > 0m)
                {
                    // (price move) × size × contractSize → quote-ccy P&L; × rate → USD.
                    unrealisedAbs = (m - p.EntryLevel) * signedQty * lot * rate;
                    unrealisedPct = (m - p.EntryLevel) / p.EntryLevel * 100m
                                    * (p.Direction == "SELL" ? -1m : 1m);
                }
                return new
                {
                    ticker = p.Epic,                              // IG epic
                    quantity = signedQty,
                    averagePricePaid = (decimal?)p.EntryLevel,
                    currentPrice = mid,
                    unrealisedAbs,
                    unrealisedPct,
                    lotSize = p.LotSize,
                    contractSize = contractSize.GetValueOrDefault(p.Epic),
                    quoteCcy = q,
                    usdRate = q != null ? usdRate.GetValueOrDefault(q) : null,
                    instrumentName = p.InstrumentName,
                    dealId = p.DealId,
                };
            }).ToArray();
            return Results.Ok(new
            {
                enabled = true,
                mode = ig.BrokerLabel,
                count = rows.Length,
                positions = rows,
                error = result.Error,
            });
        });

        // POST /api/integrations/ig/positions/flatten — close (net to
        // flat) open IG deals. The duplicate-order bug left many stacked
        // EUR/USD deals; this nets them by closing each deal at market.
        // Body: { "symbol": "EURUSD" } to flatten one instrument, or
        // {} / no body to flatten ALL open IG deals. Mutating + broker-
        // facing, so it's behind the Firebase-auth /api group and the
        // UI gates it with a confirm. Returns a per-deal result so the
        // cockpit can show what closed and what didn't.
        app.MapPost("/integrations/ig/positions/flatten", async (
            TradePro.Api.Providers.IG.IGClient ig,
            FlattenRequest? body,
            CancellationToken ct) =>
        {
            if (!ig.IsEnabled)
            {
                return Results.BadRequest(new { error = "IG client is disabled" });
            }
            var symbol = body?.Symbol?.Trim().ToUpperInvariant();
            var dealId = body?.DealId?.Trim();
            var snapshot = await ig.GetPositionsAsync(ct);
            if (snapshot.Error is not null)
            {
                return Results.Json(new { error = $"could not read IG positions: {snapshot.Error}" }, statusCode: 502);
            }

            // Precedence: a specific dealId closes just that deal; else a
            // symbol closes every deal for that bare pair (CS.D.<pair>.*
            // regardless of contract size); else flatten everything.
            bool Matches(TradePro.Api.Providers.IG.IGPosition p)
            {
                if (!string.IsNullOrEmpty(dealId)) return p.DealId == dealId;
                if (string.IsNullOrEmpty(symbol)) return true;
                var epic = p.Epic.ToUpperInvariant();
                var parts = epic.Split('.');
                var pair = parts.Length >= 4 && (epic.StartsWith("CS.D.") || epic.StartsWith("IX.D."))
                    ? parts[2] : epic;
                return pair == symbol || epic.Contains(symbol);
            }

            var targets = snapshot.Positions.Where(Matches).ToArray();
            var details = new List<object>();
            int closed = 0, failed = 0;
            foreach (var p in targets)
            {
                if (string.IsNullOrEmpty(p.DealId))
                {
                    failed++;
                    details.Add(new { epic = p.Epic, ok = false, error = "no dealId" });
                    continue;
                }
                var r = await ig.CloseDealAsync(p.DealId, p.Direction, p.Size, ct);
                // CloseDealAsync only means IG ACCEPTED the close REQUEST and
                // returned a deal reference — it does NOT mean the position
                // actually closed. Confirm the deal so we report the truth
                // (e.g. weekend FX → "MARKET_CLOSED", position stays open).
                var ok = false;
                string? reason = r.StatusReason ?? r.Status;
                if (r.Status == "ACCEPTED" && r.DealReference is not null)
                {
                    var conf = await ig.ConfirmDealAsync(r.DealReference, ct);
                    ok = conf.Status == "ACCEPTED";
                    reason = ok ? null : (conf.StatusReason ?? conf.Status);
                }
                if (ok) closed++; else failed++;
                details.Add(new
                {
                    epic = p.Epic,
                    dealId = p.DealId,
                    direction = p.Direction,
                    size = p.Size,
                    ok,
                    dealReference = r.DealReference,
                    error = ok ? null : reason,
                });
            }
            return Results.Ok(new
            {
                symbol = symbol ?? "ALL",
                requested = targets.Length,
                closed,
                failed,
                details,
            });
        });

        // GET /api/integrations/cash-summary — cash across every
        // connected broker so the cockpit can render a single strip
        // (T212 demo · T212 live · IG demo · future IBKR …). Each
        // tile is independent; one broker down doesn't black out the
        // others. Always 200 with a status field per row so the UI
        // can render disabled/unreachable as info, not error.
        app.MapGet("/integrations/cash-summary", async (
            Trading212Client t212Live,
            Trading212DemoClient t212Demo,
            Trading212DemoCashCache t212DemoCache,
            TradePro.Api.Providers.IG.IGClient ig,
            TradePro.Api.Providers.IBKR.IBKRClient ibkr,
            NpgsqlDataSource db,
            CancellationToken ct) =>
        {
            var rows = new List<object>();
            // Captured for the daily account-value snapshot (equity curve).
            var snap = new List<(string Broker, string? Ccy, decimal Value)>();

            // T212 LIVE — cash isn't exposed via Trading212Client yet;
            // surface as "read-only mode" with status known + a note.
            // The actual /equity/account/cash endpoint is wired on the
            // demo client; live is the same shape and follows soon.
            if (t212Live.IsEnabled)
            {
                rows.Add(new { broker = "T212_LIVE", label = "Trading 212 LIVE",
                    status = "degraded",
                    note = "Live read-only mode — cash fetch wires up next iteration. Connection is up.",
                    mode = t212Live.Mode });
            }
            else
            {
                rows.Add(new { broker = "T212_LIVE", label = "Trading 212 LIVE",
                    status = "disabled",
                    note = "Set TRADEPRO_T212_MODE=live + TRADEPRO_T212_API_KEY to enable." });
            }

            // T212 DEMO — algo's primary equity broker.
            try
            {
                if (t212Demo.IsEnabled)
                {
                    var cash = await t212DemoCache.GetAsync(ct);
                    rows.Add(new
                    {
                        broker = "T212_DEMO", label = "Trading 212 DEMO (algo equity)",
                        status = cash.Error is null ? "ok" : "down",
                        currency = cash.Currency ?? "USD",
                        free = cash.Free, invested = cash.Invested,
                        total = cash.Total, openPnl = cash.Ppl,
                        error = cash.Error,
                    });
                    if (cash.Error is null && cash.Total is { } t212Total)
                        snap.Add(("T212_DEMO", cash.Currency ?? "USD", t212Total));
                }
                else
                {
                    rows.Add(new { broker = "T212_DEMO", label = "Trading 212 DEMO (algo equity)",
                        status = "disabled",
                        note = "Set TRADEPRO_T212_DEMO_API_KEY to enable." });
                }
            }
            catch (Exception ex)
            {
                rows.Add(new { broker = "T212_DEMO", label = "Trading 212 DEMO (algo equity)",
                    status = "down", error = ex.Message });
            }

            // IG DEMO/LIVE — FX + equities + CFD. Sleeve for FX strategy.
            try
            {
                if (ig.IsEnabled)
                {
                    var cash = await ig.GetCashAsync(ct);
                    // "Are we making money" for IG = openPnl (IG's OWN running P&L
                    // on open positions — golden source, sane). We deliberately do
                    // NOT derive netSinceStart from (balance − deposit): IG demo
                    // accounts are funded with a huge notional and `deposit` isn't a
                    // real cost baseline, so it came out ~£9.9M (nonsense). Realised
                    // life-to-date P&L needs IG /history (roadmap), not the balance.
                    rows.Add(new
                    {
                        broker = ig.BrokerLabel,
                        label = $"IG {(ig.BrokerLabel.EndsWith("LIVE") ? "LIVE" : "DEMO")} (FX + equities)",
                        status = cash.Error is null ? "ok" : "down",
                        currency = cash.Currency,
                        available = cash.Available,
                        balance = cash.Balance,
                        openPnl = cash.ProfitLoss,
                        error = cash.Error,
                    });
                    if (cash.Error is null && cash.Balance is { } igBal)
                        snap.Add((ig.BrokerLabel, cash.Currency, igBal));
                }
                else
                {
                    rows.Add(new { broker = "IG", label = "IG (FX + equities)",
                        status = "disabled",
                        note = "Populate AWS Secrets Manager tradepro/ig + restart." });
                }
            }
            catch (Exception ex)
            {
                rows.Add(new { broker = "IG", label = "IG (FX + equities)",
                    status = "down", error = ex.Message });
            }

            // IBKR — OAuth2 Web API. Reports live enabled/disabled from the
            // client's IsEnabled gate (driven by the tradepro/ibkr secret +
            // IBKR:Mode). When enabled, pull cash/net-liquidation from the
            // ledger; when disabled, surface the slot so the UI sets the
            // expectation that it's wired + waiting on secrets.
            try
            {
                if (ibkr.IsEnabled)
                {
                    var cash = await ibkr.GetCashAsync(ct);
                    rows.Add(new
                    {
                        broker = ibkr.BrokerLabel,
                        label = $"IBKR {(ibkr.BrokerLabel.EndsWith("LIVE") ? "LIVE" : "PAPER")} (equities)",
                        status = cash.Error is null ? "ok" : "down",
                        currency = cash.Currency,
                        free = cash.Cash,
                        total = cash.NetLiquidation,
                        openPnl = cash.UnrealizedPnl,
                        error = cash.Error,
                    });
                    if (cash.Error is null && cash.NetLiquidation is { } ibkrNlv)
                        snap.Add((ibkr.BrokerLabel, cash.Currency, ibkrNlv));
                }
                else
                {
                    rows.Add(new
                    {
                        broker = "IBKR_PAPER",
                        label = "IBKR Paper (equities)",
                        status = "disabled",
                        note = "Populate AWS Secrets Manager tradepro/ibkr (set mode=paper|live) + restart.",
                    });
                }
            }
            catch (Exception ex)
            {
                rows.Add(new { broker = "IBKR", label = "IBKR (equities)",
                    status = "down", error = ex.Message });
            }

            // Persist today's account value per broker (equity-curve history).
            // Upsert keeps the LATEST value per day, so by EOD the row holds the
            // day's closing value. Best-effort — never fail the cash-summary read.
            if (snap.Count > 0)
            {
                try
                {
                    await using var conn = await db.OpenConnectionAsync(ct);
                    foreach (var (broker, ccy, value) in snap)
                    {
                        await conn.ExecuteAsync(@"
                            INSERT INTO account_value_history (broker, as_of_date, currency, total_value, captured_at_utc)
                            VALUES (@broker, (NOW() AT TIME ZONE 'UTC')::date, @ccy, @value, NOW())
                            ON CONFLICT (broker, as_of_date) DO UPDATE
                                SET total_value = EXCLUDED.total_value,
                                    currency = EXCLUDED.currency,
                                    captured_at_utc = NOW();",
                            new { broker, ccy, value = (double)value });
                    }
                }
                catch { /* non-fatal: the cash summary still returns */ }
            }

            return Results.Ok(new
            {
                utc = DateTime.UtcNow,
                brokers = rows,
            });
        });

        // GET /api/account-value/history?days=N — daily account value per
        // broker for the equity curve. Series starts when capture shipped
        // (we can't backfill un-stored history). Combined curve converts on
        // the read side; here we return per-broker raw value + currency.
        app.MapGet("/account-value/history", async (
            int? days,
            NpgsqlDataSource db,
            CancellationToken ct) =>
        {
            var window = Math.Clamp(days ?? 30, 1, 365);
            try
            {
                await using var conn = await db.OpenConnectionAsync(ct);
                var rows = (await conn.QueryAsync(@"
                    SELECT broker, as_of_date::text AS date, currency, total_value AS value
                    FROM account_value_history
                    WHERE as_of_date >= (NOW() AT TIME ZONE 'UTC')::date - @window
                    ORDER BY as_of_date ASC, broker ASC;",
                    new { window })).AsList();
                return Results.Ok(new { from = window, points = rows });
            }
            catch (Exception ex)
            {
                return Results.Ok(new { from = window, points = Array.Empty<object>(), error = ex.Message });
            }
        });

        // GET /api/integrations/ig/status — IG broker connectivity check.
        // GET /api/integrations/ig/history?days=7 — REALISED P&L from IG's own
        // closed-deal history (golden source; nets spread + financing). Answers
        // "what did we make on each day" — which the OMS can't (pre-2026-06-02
        // IG fills were booked at price 0; today the OMS holds ZERO IG fills).
        // Returns per-day totals, a per-STRATEGY split, and the raw transactions.
        //
        // Per-strategy attribution (the OMS can't help — it has no IG fills):
        //   1. read which strategies route to IG from strategy_broker_map,
        //   2. tag each transaction with an asset class from its instrument
        //      (FX vs EQUITY — broker data, not a hardcoded strategy map),
        //   3. derive each IG strategy's asset class from the symbols it has
        //      actually emitted (orders table); the lone IG strategy with no
        //      derivable symbols absorbs the remaining asset class.
        // This is config-driven: add an IG strategy and it attributes itself
        // as soon as it emits a recognisable symbol.
        app.MapGet("/integrations/ig/history", async (
            int? days,
            TradePro.Api.Providers.IG.IGClient ig,
            NpgsqlDataSource db,
            CancellationToken ct) =>
        {
            if (!ig.IsEnabled)
                return Results.Ok(new { enabled = false, byDay = Array.Empty<object>(), transactions = Array.Empty<object>() });
            var window = Math.Clamp(days ?? 7, 1, 90);

            // Serve a fresh-enough cached payload without touching IG at all.
            if (_igHistoryCache.TryGetValue(window, out var cached)
                && DateTime.UtcNow - cached.At < _igHistoryTtl)
                return Results.Ok(cached.Payload);

            var to = DateOnly.FromDateTime(DateTime.UtcNow);
            var from = to.AddDays(-window);
            var hist = await ig.GetTransactionHistoryAsync(from, to, ct);

            // IG throttled (or otherwise errored) AND we have a prior good
            // payload → serve that rather than flash an empty strip. The
            // realised P&L is closed-deal history; a 60-second-stale copy is
            // honest, an empty one is misleading.
            if ((hist.Error is not null || hist.Transactions.Count == 0)
                && _igHistoryCache.TryGetValue(window, out var stale))
                return Results.Ok(stale.Payload);

            // Realised P&L = TRADING transactions only. IG's full history also
            // carries account FUNDING (DEPO — e.g. the ~£10M demo deposit),
            // which is NOT P&L; summing it produced a nonsense £9.99M
            // "realised". Keep DEAL (trade P&L) + WITH (financing / commission /
            // admin costs on trades); exclude DEPO and any cash movement.
            bool IsTradingTxn(string? type) =>
                string.Equals(type, "DEAL", StringComparison.OrdinalIgnoreCase)
                || string.Equals(type, "WITH", StringComparison.OrdinalIgnoreCase);
            var realisedTxns = hist.Transactions.Where(t => IsTradingTxn(t.Type)).ToList();

            // Realised P&L per UTC date (only rows that carry a P&L).
            var byDay = realisedTxns
                .Where(t => t.Date is not null)
                .GroupBy(t => t.Date!.Length >= 10 ? t.Date![..10] : t.Date!)
                .Select(g => new { date = g.Key, realised = g.Sum(t => t.ProfitAndLoss), trades = g.Count() })
                .OrderBy(x => x.date)
                .ToArray();

            // ── per-strategy attribution ──────────────────────────────
            // assetClass → owning IG strategy. Built from config + emitted
            // symbols; never from a hardcoded strategy list.
            var classToStrategy = new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase);
            try
            {
                await using var conn = await db.OpenConnectionAsync(ct);
                // Strategies that route to an IG broker (config-driven).
                var igStrategies = (await conn.QueryAsync<string>(
                    "SELECT strategy_id FROM strategy_broker_map WHERE broker ILIKE 'IG%'"))
                    .ToList();
                // Each IG strategy's asset classes, derived from the distinct
                // symbols it has emitted (e.g. EURUSD → FX). Strategies that
                // have emitted nothing recognisable stay "unknown".
                var derived = new Dictionary<string, HashSet<string>>();
                foreach (var sid in igStrategies)
                {
                    var syms = await conn.QueryAsync<string>(
                        "SELECT DISTINCT symbol FROM orders WHERE strategy_name = @sid", new { sid });
                    var classes = syms.Select(AssetClassOf).Where(c => c is not null)
                        .Select(c => c!).ToHashSet(StringComparer.OrdinalIgnoreCase);
                    derived[sid] = classes;
                    foreach (var c in classes)
                        classToStrategy.TryAdd(c, sid);  // first claimant wins
                }
                // The lone IG strategy with no derivable symbols absorbs every
                // asset class no symbol-derived strategy claimed (here:
                // intraday_flat → EQUITY, since it has no OMS fills/orders).
                var unknownStrategies = derived.Where(kv => kv.Value.Count == 0)
                    .Select(kv => kv.Key).ToList();
                var seenClasses = hist.Transactions.Select(t => AssetClassOf(t.Instrument))
                    .Where(c => c is not null).Select(c => c!).ToHashSet(StringComparer.OrdinalIgnoreCase);
                var unclaimed = seenClasses.Where(c => !classToStrategy.ContainsKey(c)).ToList();
                if (unknownStrategies.Count == 1)
                    foreach (var c in unclaimed) classToStrategy.TryAdd(c, unknownStrategies[0]);
            }
            catch { /* attribution is best-effort; byDay/total still ship */ }

            string? StrategyFor(string? instrument)
            {
                var cls = AssetClassOf(instrument);
                return cls is not null && classToStrategy.TryGetValue(cls, out var sid) ? sid : null;
            }

            var byStrategy = realisedTxns
                .Select(t => new { t, sid = StrategyFor(t.Instrument), cls = AssetClassOf(t.Instrument) })
                .GroupBy(x => new { Strategy = x.sid ?? "unattributed", x.cls })
                .Select(g => new
                {
                    strategyId = g.Key.Strategy,
                    assetClass = g.Key.cls,
                    realised = g.Sum(x => x.t.ProfitAndLoss),
                    trades = g.Count(),
                })
                .OrderByDescending(x => Math.Abs(x.realised))
                .ToArray();

            // Per-desk, per-symbol GROSS → COST → NET (the trade-analysis core):
            // DEAL transactions = gross trade P&L; everything else (WITH:
            // financing, admin, commission) = cost. Net = gross + cost. So a
            // desk's churn shows as "gross ~0, big negative cost" per name.
            var byStrategySymbol = realisedTxns
                .Select(t => new {
                    t,
                    sid = StrategyFor(t.Instrument) ?? "unattributed",
                    sym = NormaliseInstrument(t.Instrument),
                    isDeal = string.Equals(t.Type, "DEAL", StringComparison.OrdinalIgnoreCase),
                })
                .GroupBy(x => new { x.sid, x.sym })
                .Select(g => new
                {
                    strategyId = g.Key.sid,
                    symbol = g.Key.sym,
                    gross = g.Where(x => x.isDeal).Sum(x => x.t.ProfitAndLoss),
                    cost = g.Where(x => !x.isDeal).Sum(x => x.t.ProfitAndLoss),
                    net = g.Sum(x => x.t.ProfitAndLoss),
                    trades = g.Count(x => x.isDeal),
                })
                .OrderBy(x => x.net)  // biggest losers first — what needs attention
                .ToArray();

            var payload = new
            {
                enabled = true,
                from = from.ToString("yyyy-MM-dd"),
                to = to.ToString("yyyy-MM-dd"),
                totalRealised = realisedTxns.Sum(t => t.ProfitAndLoss),
                byDay,
                byStrategy,
                byStrategySymbol,
                attributionBasis = "instrument asset-class → IG strategy (strategy_broker_map + emitted symbols); OMS has no IG fills",
                transactions = hist.Transactions.Take(200),
                error = hist.Error,
            };
            // Only cache a GOOD payload (no error, has rows) so a throttled
            // empty never becomes the served-stale copy.
            if (hist.Error is null && hist.Transactions.Count > 0)
                _igHistoryCache[window] = (DateTime.UtcNow, payload);
            return Results.Ok(payload);
        });

        app.MapGet("/integrations/ig/status", async (
            TradePro.Api.Providers.IG.IGClient ig,
            Microsoft.Extensions.Options.IOptions<TradePro.Api.Providers.IG.IGOptions> opts,
            CancellationToken ct) =>
        {
            if (!ig.IsEnabled)
            {
                // Disambiguate the failure so the operator doesn't have
                // to dig logs: report which specific IGOptions fields are
                // missing when IsEnabled is false. Mode/ApiKey/Username/
                // Password are required; any missing → disabled.
                var o = opts.Value;
                var missing = new List<string>();
                if (string.Equals(o.Mode, "disabled", StringComparison.OrdinalIgnoreCase)
                    || string.IsNullOrWhiteSpace(o.Mode)) missing.Add("Mode");
                if (string.IsNullOrWhiteSpace(o.ApiKey))   missing.Add("ApiKey");
                if (string.IsNullOrWhiteSpace(o.Username)) missing.Add("Username");
                if (string.IsNullOrWhiteSpace(o.Password)) missing.Add("Password");
                return Results.Ok(new
                {
                    enabled = false,
                    mode = string.IsNullOrWhiteSpace(o.Mode) ? "disabled" : o.Mode,
                    reachable = false,
                    missingConfig = missing,
                    note = missing.Count > 0
                        ? $"IG disabled — missing config: {string.Join(", ", missing)}. "
                        + $"Populate AWS Secrets Manager `tradepro/ig` and restart the api container."
                        : "IG disabled — populate AWS Secrets Manager `tradepro/ig` and restart.",
                });
            }
            try
            {
                var cash = await ig.GetCashAsync(ct);
                var ok = cash.Error is null;
                return Results.Ok(new
                {
                    enabled = true,
                    mode = ig.BrokerLabel,
                    reachable = ok,
                    authenticated = ok,
                    available = cash.Available,
                    balance = cash.Balance,
                    currency = cash.Currency,
                    error = cash.Error,
                });
            }
            catch (Exception ex)
            {
                return Results.Ok(new
                {
                    enabled = true,
                    mode = ig.BrokerLabel,
                    reachable = false,
                    authenticated = false,
                    error = ex.Message,
                });
            }
        });

        // GET /api/integrations/ibkr/status — IBKR broker connectivity
        // check. READ-ONLY: runs the full OAuth bring-up (token →
        // sso-sessions → tickle → ssodh/init → iserver/accounts) the moment
        // the tradepro/ibkr secret lands, and returns the visible accounts —
        // WITHOUT placing any order. Reuses the cached session (no per-call
        // re-auth; IBKR rate-limits that). When disabled it does NO network
        // call and reports {enabled:false, reason} so the operator can
        // confirm the dormant guard is intact before secrets are added.
        // On failure the verbatim IBKR error body (e.g. a 401/403 with the
        // IP/credential complaint) is surfaced so the operator can debug
        // against IBKR's error guide.
        app.MapGet("/integrations/ibkr/status", async (
            TradePro.Api.Providers.IBKR.IBKRClient ibkr,
            Microsoft.Extensions.Options.IOptions<TradePro.Api.Providers.IBKR.IBKROptions> opts,
            CancellationToken ct) =>
        {
            var o = opts.Value;
            if (!ibkr.IsEnabled)
            {
                // Disambiguate WHY it's disabled so the operator knows which
                // secret field is missing — mirror the IG status pattern.
                var missing = new List<string>();
                if (string.Equals(o.Mode, "disabled", StringComparison.OrdinalIgnoreCase)
                    || string.IsNullOrWhiteSpace(o.Mode)) missing.Add("Mode");
                // Report on the ACTIVE (mode-resolved) triple so the operator
                // sees which environment's field is missing, not the legacy one.
                if (string.IsNullOrWhiteSpace(o.ActiveClientId))   missing.Add("ClientId");
                if (string.IsNullOrWhiteSpace(o.ClientKeyId))      missing.Add("ClientKeyId");
                if (string.IsNullOrWhiteSpace(o.ActiveCredential)) missing.Add("Credential");
                if (string.IsNullOrWhiteSpace(o.PrivateKey))       missing.Add("PrivateKey");
                if (string.IsNullOrWhiteSpace(o.ActiveAccountId))  missing.Add("AccountId");
                return Results.Ok(new
                {
                    enabled = false,
                    authenticated = false,
                    mode = string.IsNullOrWhiteSpace(o.Mode) ? "disabled" : o.Mode,
                    clientIdInUse = RedactClientId(o.ActiveClientId),
                    accounts = Array.Empty<string>(),
                    accountIdInUse = string.IsNullOrWhiteSpace(o.ActiveAccountId)
                        ? (string?)null : o.ActiveAccountId,
                    missingConfig = missing,
                    reason = missing.Count > 0
                        ? $"IBKR disabled — missing config: {string.Join(", ", missing)}. "
                          + "Populate AWS Secrets Manager `tradepro/ibkr` (set mode=paper|live) + restart the api."
                        : "IBKR disabled — populate AWS Secrets Manager `tradepro/ibkr` and restart.",
                });
            }
            var status = await ibkr.GetStatusAsync(ct);
            return Results.Ok(new
            {
                enabled = status.Enabled,
                authenticated = status.Authenticated,
                mode = status.Mode,
                brokerLabel = status.BrokerLabel,
                // Active (mode-resolved) client id, redacted to a hint so the
                // operator can confirm WHICH environment is live without the
                // raw id leaking into logs / the public dashboard.
                clientIdInUse = RedactClientId(o.ActiveClientId),
                accounts = status.Accounts,
                accountIdInUse = status.AccountIdInUse,
                useX5c = o.UseX5c,
                certificatePresent = !string.IsNullOrWhiteSpace(o.Certificate),
                error = status.Error,
            });
        });

        app.MapGet("/integrations/trading212/cash",
            async (
                string? account,
                Trading212DemoClient demoClient,
                Trading212DemoCashCache demoCashCache,
                CancellationToken ct) =>
            {
                var useDemo = !string.Equals(account, "live", StringComparison.OrdinalIgnoreCase);
                if (!useDemo)
                {
                    // Live cash via Trading212Client is a separate plug;
                    // for now operators using live should curl directly.
                    return Results.Ok(new
                    {
                        enabled = false,
                        mode = "live",
                        message = "Live cash fetch not wired yet; use demo.",
                    });
                }
                if (!demoClient.IsEnabled)
                {
                    return Results.Ok(new
                    {
                        enabled = false,
                        mode = "demo",
                        message = "Set Trading212Demo:ApiKey to enable.",
                    });
                }
                // Go through the cache so concurrent renders / poll loops
                // don't each hit T212 — the bucket is ~1 req/2s and the
                // second uncached call always trips 429. Cache TTL is
                // 30s (configurable via Trading212Demo:CashCacheSeconds);
                // on 429 it serves the last good snapshot rather than
                // surfacing an angry red error to the user.
                var cash = await demoCashCache.GetAsync(ct);
                var cachedAt = demoCashCache.CachedAtUtc ?? DateTime.UtcNow;
                var ageSeconds = (DateTime.UtcNow - cachedAt).TotalSeconds;
                return Results.Ok(new
                {
                    enabled = true,
                    mode = "demo",
                    fetchedAtUtc = cachedAt,
                    ageSeconds,
                    fromCache = ageSeconds > 1.0,
                    free = cash.Free,
                    invested = cash.Invested,
                    total = cash.Total,
                    blocked = cash.Blocked,
                    ppl = cash.Ppl,
                    currency = cash.Currency,
                    error = cash.Error,
                    httpStatus = cash.HttpStatus,
                });
            });

        app.MapGet("/integrations/trading212/positions",
            async (
                string? account,
                Trading212Client liveClient,
                Trading212DemoClient demoClient,
                Trading212PositionsCache liveCache,
                Trading212DemoPositionsCache demoCache,
                CancellationToken ct) =>
            {
                // ?account=live|demo. Demo is the default because that's
                // what every operator looks at unless they explicitly
                // switched the platform into Live mode. Stops the
                // Portfolio page showing real-money positions by accident
                // when only the demo account has trades in it.
                var useDemo = !string.Equals(account, "live", StringComparison.OrdinalIgnoreCase);
                var isEnabled = useDemo ? demoClient.IsEnabled : liveClient.IsEnabled;
                var modeLabel = useDemo ? demoClient.Mode : liveClient.Mode;

                if (!isEnabled)
                {
                    return Results.Ok(new
                    {
                        enabled = false,
                        mode = modeLabel,
                        message = useDemo
                            ? "T212 demo client is disabled. Set Trading212Demo:ApiKey to enable."
                            : "Trading212 integration is disabled. Set Trading212:Mode and credentials.",
                        positions = Array.Empty<object>(),
                    });
                }
                // Both paths now cache — T212's 1 req/sec rate limit
                // hit demo when the drift panel + Portfolio fetch raced,
                // producing 429s on the trader's screen. Same TTL
                // contract for both modes via parallel cache services.
                var result = useDemo
                    ? await demoCache.GetAsync(ct)
                    : await liveCache.GetAsync(ct);
                var rows = result.Positions.Select(p =>
                {
                    decimal? unrealisedPct = null;
                    // Prefer T212's OWN per-position P&L (golden — sums to the
                    // account Ppl and prices delisted holdings like LUK
                    // correctly). Only recompute from (currentPrice − avg) when
                    // the broker omits it — and a missing/zero quote there marks
                    // the holding to $0, fabricating a phantom loss, so guard it.
                    decimal? unrealisedAbs = p.Ppl;
                    if (p.AveragePricePaid is decimal avg && avg > 0
                        && p.CurrentPrice is decimal cur)
                    {
                        unrealisedPct = (cur - avg) / avg * 100m;
                        unrealisedAbs ??= (cur - avg) * p.Quantity;
                    }
                    // Derive % from the broker's P&L when we used it (so the %
                    // and $ agree, instead of a −100% on a $0 phantom mark).
                    if (p.Ppl is decimal ppl && p.AveragePricePaid is decimal a
                        && a > 0 && p.Quantity != 0)
                    {
                        unrealisedPct = ppl / (a * p.Quantity) * 100m;
                    }
                    // T212 nests the ticker inside `instrument` on the
                    // /equity/portfolio response; the top-level Ticker
                    // we modelled isn't populated, hence the null seen
                    // in the wild. Fall back to it just in case a future
                    // shape change moves it back.
                    var t212Ticker = p.Instrument?.Ticker ?? p.Ticker;
                    return new
                    {
                        ticker = t212Ticker,
                        // Best-effort Yahoo-symbol derivation. T212
                        // tickers look like "AMZN_US_EQ"; we split on
                        // underscore and take the first part for US
                        // tickers (verified mapping). Other venues need
                        // explicit mapping; null tells the caller to
                        // not cross-reference against the compare cache.
                        yahooSymbol = DeriveYahooSymbol(t212Ticker),
                        instrumentName = p.Instrument?.Name,
                        currency = p.Instrument?.Currency,
                        isin = p.Instrument?.Isin,
                        quantity = p.Quantity,
                        averagePricePaid = p.AveragePricePaid,
                        currentPrice = p.CurrentPrice,
                        unrealisedPct,
                        unrealisedAbs,
                        createdAt = p.CreatedAt,
                    };
                }).ToList();
                return Results.Ok(new
                {
                    enabled = true,
                    mode = modeLabel,
                    fetchedAtUtc = DateTime.UtcNow,
                    positionCount = rows.Count,
                    positions = rows,
                    // Surfaces the underlying T212 failure so the UI
                    // doesn't silently render "0 positions" when the
                    // real story is "401 Unauthorized" or "404 not
                    // found". Null when the call succeeded.
                    error = result.Error,
                    httpStatus = result.HttpStatus,
                    fromCache = result.FromCache,
                    ageSeconds = result.AgeSeconds,
                });
            });

        // Finnhub forward earnings calendar (next ~30 days by default,
        // overridable via `days`). Off by default — returns
        // {enabled: false} until Finnhub__ApiKey is set in config.
        // Used to flag "MSFT reports in 5 days" so the digest can warn
        // the user about position-into-earnings volatility risk.
        app.MapGet("/integrations/finnhub/earnings-calendar",
            async (
                string? symbol,
                int? days,
                FinnhubClient client,
                CancellationToken ct) =>
            {
                if (!client.IsEnabled)
                {
                    return Results.Ok(new
                    {
                        enabled = false,
                        message = "Finnhub integration is disabled. Set Finnhub:ApiKey in config (free tier signup at finnhub.io).",
                        events = Array.Empty<FinnhubEarningsEvent>(),
                    });
                }
                if (string.IsNullOrWhiteSpace(symbol))
                {
                    return Results.BadRequest(new { error = "symbol is required" });
                }
                var from = DateOnly.FromDateTime(DateTime.UtcNow.Date);
                var to = from.AddDays(Math.Clamp(days ?? 30, 1, 90));
                var events = await client.GetEarningsCalendarAsync(symbol, from, to, ct);
                return Results.Ok(new
                {
                    enabled = true,
                    symbol = symbol.ToUpperInvariant(),
                    from = from.ToString("yyyy-MM-dd"),
                    to = to.ToString("yyyy-MM-dd"),
                    eventCount = events.Count,
                    events,
                });
            });

        // Analyst recommendation trends — monthly buy/hold/sell counts
        // from Finnhub's free tier. Pre-computes the headline "month-
        // over-month bullish shift" (rolling 2-month delta of buy +
        // strongBuy minus sell + strongSell) so the worker doesn't
        // have to redo the math per symbol.
        app.MapGet("/integrations/finnhub/recommendations",
            async (
                string? symbol,
                FinnhubClient client,
                CancellationToken ct) =>
            {
                if (!client.IsEnabled)
                {
                    return Results.Ok(new
                    {
                        enabled = false,
                        message = "Finnhub integration is disabled. Set Finnhub:ApiKey in config (free tier signup at finnhub.io).",
                        periods = Array.Empty<FinnhubRecommendationTrend>(),
                    });
                }
                if (string.IsNullOrWhiteSpace(symbol))
                {
                    return Results.BadRequest(new { error = "symbol is required" });
                }
                var periods = await client.GetRecommendationTrendsAsync(symbol, ct);
                int BullScore(FinnhubRecommendationTrend t) =>
                    (t.StrongBuy ?? 0) + (t.Buy ?? 0) - (t.Sell ?? 0) - (t.StrongSell ?? 0);
                int momChange = 0;
                if (periods.Count >= 2)
                    momChange = BullScore(periods[0]) - BullScore(periods[1]);
                var latest = periods.FirstOrDefault();
                return Results.Ok(new
                {
                    enabled = true,
                    symbol = symbol.ToUpperInvariant(),
                    periodCount = periods.Count,
                    latestPeriod = latest?.Period,
                    latestStrongBuy = latest?.StrongBuy ?? 0,
                    latestBuy = latest?.Buy ?? 0,
                    latestHold = latest?.Hold ?? 0,
                    latestSell = latest?.Sell ?? 0,
                    latestStrongSell = latest?.StrongSell ?? 0,
                    bullScoreLatest = latest is null ? 0 : BullScore(latest),
                    momChange,    // positive = analysts getting MORE bullish vs prior month
                    periods,      // newest-first; up to ~12 months
                });
            });

        // Analyst upgrade / downgrade events. Surfaces "Goldman raised
        // BUY → STRONG_BUY on AAPL 3 days ago" type events. `days`
        // defaults to 30; capped 1..180.
        //
        // ⚠ PLAN NOTE: /stock/upgrade-downgrade requires a PAID Finnhub
        // plan. Free-tier API keys always return an empty list (HTTP 200
        // with []). See FinnhubClient.GetRecommendationTrendsAsync for
        // the free-tier alternative (monthly buy/hold/sell counts).
        // When events come back empty the response includes
        // plan_gated=true so callers can surface an honest explanation
        // rather than showing a misleading "0 upgrades" figure.
        app.MapGet("/integrations/finnhub/upgrades",
            async (
                string? symbol,
                int? days,
                FinnhubClient client,
                CancellationToken ct) =>
            {
                if (!client.IsEnabled)
                {
                    return Results.Ok(new
                    {
                        enabled = false,
                        message = "Finnhub integration is disabled. Set Finnhub:ApiKey in config (free tier signup at finnhub.io).",
                        planGated = false,
                        events = Array.Empty<FinnhubUpgradeDowngrade>(),
                    });
                }
                if (string.IsNullOrWhiteSpace(symbol))
                {
                    return Results.BadRequest(new { error = "symbol is required" });
                }
                var to = DateOnly.FromDateTime(DateTime.UtcNow.Date);
                var from = to.AddDays(-Math.Clamp(days ?? 30, 1, 180));
                var events = await client.GetUpgradeDowngradesAsync(symbol, from, to, ct);
                // Compact summary so the worker doesn't have to do
                // anything to derive "net upgrades last 30d".
                var upCount = events.Count(e => string.Equals(e.Action, "up", StringComparison.OrdinalIgnoreCase));
                var downCount = events.Count(e => string.Equals(e.Action, "down", StringComparison.OrdinalIgnoreCase));
                var initCount = events.Count(e => string.Equals(e.Action, "init", StringComparison.OrdinalIgnoreCase));
                // Empty results on a named symbol almost always mean the
                // free-tier plan gate — not genuine zero analyst coverage.
                // Flag it so the UI/MCP can say "not available on free plan"
                // rather than showing a misleading "0 upgrades" figure.
                var planGated = events.Count == 0;
                return Results.Ok(new
                {
                    enabled = true,
                    symbol = symbol.ToUpperInvariant(),
                    from = from.ToString("yyyy-MM-dd"),
                    to = to.ToString("yyyy-MM-dd"),
                    eventCount = events.Count,
                    upgradeCount = upCount,
                    downgradeCount = downCount,
                    initCount,
                    netDelta = upCount - downCount,
                    planGated,
                    events,
                });
            });

        return app;
    }

    /// <summary>
    /// T212 ticker → Yahoo Finance symbol for cross-reference against
    /// the compare cache. T212 uses a few formats:
    ///
    ///   AMZN_US_EQ   → AMZN          (US equity / ETF)
    ///   VUKEl_EQ     → VUKE.L        (LSE — trailing lowercase 'l' is
    ///                                  T212's London exchange marker)
    ///   VOD_L_EQ     → VOD.L         (older LSE format, separate _L_)
    ///   ABCd_EQ      → ABC.DE        (Xetra, lowercase 'd')
    ///   ABCp_EQ      → ABC.PA        (Paris, lowercase 'p')
    ///
    /// The lowercase-suffix shape covers the modern T212 format users
    /// see for European listings; the underscore-segment shape covers
    /// the older format. Returns null for unrecognised venues so the
    /// caller skips the lookup rather than fabricating a wrong symbol.
    /// </summary>
    /// <summary>
    /// Redact an IBKR OAuth client_id down to a short hint so the operator
    /// can confirm WHICH per-env credential is active (paper vs live) from
    /// the /ibkr/status endpoint, WITHOUT the raw id leaking into the public
    /// dashboard or logs (repo is public). Shows the first 4 chars + a
    /// length-masked tail; null/empty in → null out.
    /// </summary>
    private static string? RedactClientId(string? clientId)
    {
        if (string.IsNullOrWhiteSpace(clientId)) return null;
        var id = clientId.Trim();
        if (id.Length <= 4) return new string('*', id.Length);
        return id.Substring(0, 4) + new string('*', Math.Min(id.Length - 4, 8));
    }

    private static string? DeriveYahooSymbol(string? t212Ticker)
    {
        if (string.IsNullOrWhiteSpace(t212Ticker)) return null;
        var parts = t212Ticker.Split('_');
        if (parts.Length < 1) return null;
        var head = parts[0];

        // Modern format: trailing lowercase letter on the head encodes
        // the venue. Example: VUKEl_EQ — root is VUKE, venue is L.
        // Skip when head is already all-caps (US stocks like AMZN, NVDA).
        if (head.Length > 1)
        {
            var lastChar = head[^1];
            if (char.IsLower(lastChar))
            {
                var root = head[..^1];
                var suffix = char.ToUpperInvariant(lastChar);
                return suffix switch
                {
                    'L' => $"{root}.L",     // London Stock Exchange
                    'D' => $"{root}.DE",    // Xetra
                    'P' => $"{root}.PA",    // Paris (Euronext)
                    'F' => $"{root}.AS",    // Amsterdam — heuristic; verify per ticker
                    _ => null,
                };
            }
        }

        // Legacy underscore-segment format: AMZN_US_EQ, VOD_L_EQ.
        if (parts.Length >= 2)
        {
            var venue = parts[1].ToUpperInvariant();
            return venue switch
            {
                "US" => head,            // AMZN_US_EQ → AMZN
                "L"  => $"{head}.L",     // VOD_L_EQ → VOD.L
                "DE" => $"{head}.DE",    // Xetra alternate
                "PA" => $"{head}.PA",    // Paris alternate
                "AS" => $"{head}.AS",    // Amsterdam alternate
                _ => null,
            };
        }
        return null;
    }
}

/// Body for POST /integrations/ig/positions/flatten.
///   DealId set  → close just that one deal (per-row close).
///   Symbol set  → close every deal for that bare pair (e.g. "EURUSD").
///   neither     → flatten every open IG deal.
public sealed record FlattenRequest(string? Symbol, string? DealId);

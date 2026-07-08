using System.Collections.Concurrent;
using System.Text.Json;
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
        // Options / barriers (IG "Weekly EURUSD 11800 PUT", "Daily … CALL") are
        // neither the equity-CFD desk (intraday_flat) nor the spot-FX desk
        // (ichimoku_fx_mr). Without this they fall through to EQUITY →
        // intraday_flat and inflate its loss (one EURUSD PUT was −£1,565).
        // Return null = unattributed so they're excluded from per-strategy P&L.
        if (Regex.IsMatch(u, @"\b(PUT|CALL)\b")) return null;
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
            Trading212LiveCashCache t212LiveCache,
            Trading212DemoCashCache t212DemoCache,
            TradePro.Api.Providers.IG.IGClient ig,
            TradePro.Api.Providers.IBKR.IBKRClient ibkr,
            IConfiguration config,
            NpgsqlDataSource db,
            CancellationToken ct) =>
        {
            var rows = new List<object>();
            // Captured for the daily account-value snapshot (equity curve).
            var snap = new List<(string Broker, string? Ccy, decimal Value)>();

            // T212 LIVE — cash fetched via the live cache (TTL 30s) so the
            // cockpit's poll loop doesn't hammer T212's /account/cash bucket.
            // On 429 the cache serves the last good snapshot; on first call
            // it fetches fresh. When the live client is disabled we surface
            // the disabled slot so the UI knows it's wired and waiting.
            try
            {
                if (t212Live.IsEnabled)
                {
                    var liveCash = await t212LiveCache.GetAsync(ct);
                    rows.Add(new
                    {
                        broker = "T212_LIVE", label = "Trading 212 LIVE",
                        status = liveCash.Error is null ? "ok" : "down",
                        currency = TradePro.Api.Configuration.BrokerCurrencies.Resolve(config, "T212_LIVE", liveCash.Currency),
                        free = liveCash.Free, invested = liveCash.Invested,
                        total = liveCash.Total, openPnl = liveCash.Ppl,
                        mode = t212Live.Mode,
                        error = liveCash.Error,
                    });
                    if (liveCash.Error is null && liveCash.Total is { } liveTotal)
                        snap.Add(("T212_LIVE", TradePro.Api.Configuration.BrokerCurrencies.Resolve(config, "T212_LIVE", liveCash.Currency), liveTotal));
                }
                else
                {
                    rows.Add(new { broker = "T212_LIVE", label = "Trading 212 LIVE",
                        status = "disabled",
                        note = "Set TRADEPRO_T212_MODE=live + TRADEPRO_T212_API_KEY to enable." });
                }
            }
            catch (Exception ex)
            {
                rows.Add(new { broker = "T212_LIVE", label = "Trading 212 LIVE",
                    status = "down", error = ex.Message });
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
                        currency = TradePro.Api.Configuration.BrokerCurrencies.Resolve(config, "T212_DEMO", cash.Currency),
                        free = cash.Free, invested = cash.Invested,
                        total = cash.Total, openPnl = cash.Ppl,
                        error = cash.Error,
                    });
                    if (cash.Error is null && cash.Total is { } t212Total)
                        snap.Add(("T212_DEMO", TradePro.Api.Configuration.BrokerCurrencies.Resolve(config, "T212_DEMO", cash.Currency), t212Total));
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
                        currency = TradePro.Api.Configuration.BrokerCurrencies.Resolve(config, ig.BrokerLabel, cash.Currency),
                        available = cash.Available,
                        balance = cash.Balance,
                        openPnl = cash.ProfitLoss,
                        error = cash.Error,
                    });
                    if (cash.Error is null && cash.Balance is { } igBal)
                        snap.Add((ig.BrokerLabel, TradePro.Api.Configuration.BrokerCurrencies.Resolve(config, ig.BrokerLabel, cash.Currency), igBal));
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
                        currency = TradePro.Api.Configuration.BrokerCurrencies.Resolve(config, ibkr.BrokerLabel, cash.Currency),
                        free = cash.Cash,
                        total = cash.NetLiquidation,
                        openPnl = cash.UnrealizedPnl,
                        error = cash.Error,
                    });
                    if (cash.Error is null && cash.NetLiquidation is { } ibkrNlv)
                        snap.Add((ibkr.BrokerLabel, TradePro.Api.Configuration.BrokerCurrencies.Resolve(config, ibkr.BrokerLabel, cash.Currency), ibkrNlv));
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
        // GET /api/integrations/ibkr/price-history?symbol=AAPL&period=1y&bar=1d
        // Central IBKR historical bars via the OAuth Web API — the SINGLE IBKR data
        // path. The Python bar_cache provider consumes THIS over HTTP (no duplicate
        // OAuth). FAIL-LOUD: an unresolved symbol / empty history returns an explicit
        // 502 + reason, NEVER an empty 200 — so the cockpit can FLAG the symbol
        // instead of silently showing "no data".
        app.MapGet("/integrations/ibkr/price-history", async (
            string symbol, string? period, string? bar,
            TradePro.Api.Providers.IBKR.IBKRClient ibkr, CancellationToken ct) =>
        {
            if (string.IsNullOrWhiteSpace(symbol))
                return Results.BadRequest(new { error = "symbol is required" });
            var p = string.IsNullOrWhiteSpace(period) ? "1y" : period;
            var b = string.IsNullOrWhiteSpace(bar) ? "1d" : bar;
            var r = await ibkr.GetPriceHistoryAsync(symbol, p, b, ct);
            if (r.Error is not null || r.Bars.Count == 0)
            {
                return Results.Json(new
                {
                    symbol = symbol.ToUpperInvariant(), period = p, bar = b,
                    conid = r.ConId, error = r.Error ?? "no bars returned",
                    bars = Array.Empty<object>(),
                }, statusCode: 502);
            }
            return Results.Ok(new
            {
                symbol = symbol.ToUpperInvariant(), period = p, bar = b, conid = r.ConId,
                count = r.Bars.Count,
                // epoch-ms UTC + OHLCV; the Python provider maps straight to the
                // bar_cache schema.
                bars = r.Bars.Select(x => new
                {
                    t = x.TimeMs, o = x.Open, h = x.High, l = x.Low, c = x.Close, v = x.Volume,
                }),
            });
        });

        // POST /api/integrations/ibkr/orders — CONTROLLED single market order to
        // the PAPER account, to validate the confirmed order path end-to-end
        // (auth → place → reply-confirm → real order id) BEFORE the daemon routes
        // through it. Guarded THREE ways: (1) IsEnabled, (2) the AllowOrders
        // kill-switch (403 when off), (3) the mode-resolved account (a paper
        // secret can only ever place to the paper account). Places exactly ONE
        // order and returns the FULL result — the real broker order id on
        // success, or the rejection reason. FAIL-LOUD: never a silent success.
        app.MapPost("/integrations/ibkr/orders", async (
            IBKROrderRequest req,
            TradePro.Api.Providers.IBKR.IBKRClient ibkr,
            Microsoft.Extensions.Options.IOptions<TradePro.Api.Providers.IBKR.IBKROptions> opts,
            ILoggerFactory lf, CancellationToken ct) =>
        {
            var log = lf.CreateLogger("IBKROrderTest");
            if (!ibkr.IsEnabled)
                return Results.Json(new { error = "IBKR disabled — populate tradepro/ibkr + restart" }, statusCode: 503);
            if (!ibkr.AllowOrders)
                return Results.Json(new
                {
                    error = "IBKR order placement is disabled (read-only kill-switch). "
                          + "Set IBKR:AllowOrders=true on the paper secret to enable.",
                    allowOrders = false, mode = opts.Value.Mode, account = opts.Value.AccountId,
                }, statusCode: 403);
            if (req is null || string.IsNullOrWhiteSpace(req.Symbol)
                || string.IsNullOrWhiteSpace(req.Side) || req.Quantity <= 0)
                return Results.BadRequest(new { error = "symbol, side (BUY/SELL), quantity>0 required" });
            var side = req.Side.Trim().ToUpperInvariant();
            if (side is not ("BUY" or "SELL"))
                return Results.BadRequest(new { error = "side must be BUY or SELL" });
            var secType = string.IsNullOrWhiteSpace(req.SecType) ? "STK" : req.SecType!.Trim().ToUpperInvariant();
            log.LogWarning(
                "IBKR TEST ORDER: {Side} {Qty} {Sym} ({SecType}) → mode={Mode} account={Account}",
                side, req.Quantity, req.Symbol, secType, opts.Value.Mode, opts.Value.AccountId);
            var r = await ibkr.PlaceMarketOrderBySymbolAsync(req.Symbol, side, req.Quantity, secType, ct);
            var ok = r.Status == "ACCEPTED" && r.OrderId is not null;
            return Results.Json(new
            {
                placed = ok,
                orderId = r.OrderId,
                status = r.Status,
                reason = r.StatusReason,
                mode = opts.Value.Mode,
                account = opts.Value.AccountId,
                symbol = req.Symbol.ToUpperInvariant(), side, quantity = req.Quantity, secType,
            }, statusCode: ok ? 200 : 502);
        })
        .WithName("PlaceIBKRTestOrder");

        // POST /api/integrations/ibkr/orders/{orderId}/cancel — pull a working
        // order at the broker (DELETE /iserver/.../order/{id}). Behind the same
        // kill-switch. Broker is golden source, so cancelling here is the
        // authoritative action; the OMS row is reflected to CANCELLED afterwards.
        app.MapPost("/integrations/ibkr/orders/{orderId}/cancel", async (
            string orderId,
            TradePro.Api.Providers.IBKR.IBKRClient ibkr,
            CancellationToken ct) =>
        {
            if (!ibkr.IsEnabled)
                return Results.Json(new { error = "IBKR disabled" }, statusCode: 503);
            if (!ibkr.AllowOrders)
                return Results.Json(new { error = "read-only kill-switch (AllowOrders=false)" }, statusCode: 403);
            var r = await ibkr.CancelOrderAsync(orderId, ct);
            var ok = r.Status == "CANCELLED";
            return Results.Json(new { orderId, status = r.Status, reason = r.StatusReason },
                statusCode: ok ? 200 : 502);
        })
        .WithName("CancelIBKROrder");

        // POST /api/integrations/ibkr/reconcile-oms — BROKER IS GOLDEN SOURCE.
        // Fetch the broker's live order blotter and sync the OMS to match: a
        // broker-Filled order → RecordFillAsync (OMS→FILLED), a broker-Cancelled
        // order → CancelAsync. The OMS FOLLOWS the broker, never leads — so an
        // order can't go stale in the OMS (the exact bug that left the old clone
        // orders stuck SUBMITTED). Read-only fetch; the OMS writes go through the
        // proper state machine. Idempotent (records only the fill DELTA).
        app.MapPost("/integrations/ibkr/reconcile-oms", async (
            TradePro.Api.Providers.IBKR.IBKRClient ibkr,
            TradePro.Api.Oms.IOmsService oms,
            ILoggerFactory lf, CancellationToken ct) =>
        {
            var log = lf.CreateLogger("IBKRReconcile");
            if (!ibkr.IsEnabled)
                return Results.Json(new { error = "IBKR disabled" }, statusCode: 503);
            var res = await ibkr.GetLiveOrdersAsync(ct);
            if (res.Error is not null)
                return Results.Json(new { error = $"broker order fetch failed: {res.Error}" }, statusCode: 502);

            var byId = new Dictionary<string, TradePro.Api.Providers.IBKR.IBKRLiveOrder>(StringComparer.OrdinalIgnoreCase);
            foreach (var bo in res.Orders)
                if (!string.IsNullOrWhiteSpace(bo.OrderId)) byId[bo.OrderId!] = bo;

            var open = (await oms.ListAsync(new[] { "SUBMITTED", "WORKING", "PARTIALLY_FILLED" }, 500))
                .Where(o => (o.Broker is "IBKR_PAPER" or "IBKR_LIVE") && !string.IsNullOrWhiteSpace(o.BrokerOrderId))
                .ToList();

            var applied = new List<object>();
            foreach (var o in open)
            {
                if (!byId.TryGetValue(o.BrokerOrderId!, out var bo))
                {
                    applied.Add(new { o.Symbol, o.BrokerOrderId, action = "no-broker-match (aged out of blotter)" });
                    continue;
                }
                var status = (bo.Status ?? "").ToLowerInvariant();
                try
                {
                    if (status.Contains("fill"))
                    {
                        var totalFilled = bo.FilledQty ?? bo.TotalSize ?? o.Qty;
                        var delta = totalFilled - o.FilledQty;   // record only the NEW fill (idempotent)
                        var px = bo.AvgPrice ?? 0m;
                        if (delta > 0m && px > 0m)
                        {
                            await oms.RecordFillAsync(o.Id, delta, px, 0m, "USD", $"recon:{o.BrokerOrderId}", "broker:reconcile");
                            applied.Add(new { o.Symbol, o.BrokerOrderId, action = "FILLED from broker", qty = delta, price = px });
                        }
                        else
                        {
                            applied.Add(new { o.Symbol, o.BrokerOrderId, action = "broker Filled — no new qty/price yet" });
                        }
                    }
                    else if (status.Contains("cancel"))
                    {
                        await oms.CancelAsync(o.Id, "broker:reconcile", "cancelled at broker");
                        applied.Add(new { o.Symbol, o.BrokerOrderId, action = "CANCELLED from broker" });
                    }
                    // else still working at the broker → leave the OMS as-is
                }
                catch (Exception ex)
                {
                    log.LogWarning(ex, "reconcile failed for OMS {Id} / broker {Bid}", o.Id, o.BrokerOrderId);
                    applied.Add(new { o.Symbol, o.BrokerOrderId, action = $"reconcile error: {ex.Message}" });
                }
            }
            return Results.Ok(new
            {
                brokerOrders = res.Orders.Count,
                omsOpen = open.Count,
                appliedCount = applied.Count,
                applied,
            });
        })
        .WithName("ReconcileIBKROms");

        // GET /api/integrations/ibkr/orders — the broker's live order blotter
        // (read-only, golden source). The Python position-seed reads this +
        // /positions to build the EFFECTIVE book (held + pending) via the Web
        // API instead of the desktop Gateway :7500.
        app.MapGet("/integrations/ibkr/orders", async (
            TradePro.Api.Providers.IBKR.IBKRClient ibkr, CancellationToken ct) =>
        {
            if (!ibkr.IsEnabled)
                return Results.Ok(new { enabled = false, note = "Populate tradepro/ibkr + restart", orders = Array.Empty<object>() });
            var res = await ibkr.GetLiveOrdersAsync(ct);
            if (res.Error is not null)
                return Results.Json(new { enabled = true, error = res.Error, orders = Array.Empty<object>() }, statusCode: 502);
            return Results.Ok(new
            {
                enabled = true,
                count = res.Orders.Count,
                orders = res.Orders.Select(o => new
                {
                    orderId = o.OrderId, status = o.Status, symbol = o.Symbol, side = o.Side,
                    totalSize = o.TotalSize, filledQty = o.FilledQty,
                    remainingQty = o.RemainingQty, avgPrice = o.AvgPrice,
                }),
            });
        })
        .WithName("GetIBKRLiveOrders");

        // GET /api/integrations/ibkr/harvester-status — visibility into the
        // continuous bar harvester: enabled, cadence, ticks, bars written,
        // ibkr-vs-yahoo source split, backfill progress, last error. Reads the
        // singleton status holder (no DB / no IBKR call). Fail-loud observability:
        // a dead/stale harvester is visible here instead of silently not running.
        app.MapGet("/integrations/ibkr/harvester-status",
            (TradePro.Api.Providers.IBKR.IBKRHarvesterStatus status) => Results.Ok(status));

        // GET /api/integrations/ibkr/bars?symbol=AAPL&resolution=1m&limit=200 —
        // latest harvested bars from ibkr_price_bars, chronological. Powers charts
        // + lets us VERIFY the harvest is landing real bars (and their source).
        app.MapGet("/integrations/ibkr/bars", async (
            string symbol, string? resolution, int? limit,
            Npgsql.NpgsqlDataSource db, CancellationToken ct) =>
        {
            var res = string.IsNullOrWhiteSpace(resolution) ? "1m" : resolution;
            var n = Math.Clamp(limit ?? 200, 1, 5000);
            await using var conn = await db.OpenConnectionAsync(ct);
            var rows = (await conn.QueryAsync(@"
                SELECT ts, open, high, low, close, volume, source
                FROM ibkr_price_bars
                WHERE symbol = @symbol AND resolution = @res
                ORDER BY ts DESC
                LIMIT @n;",
                new { symbol, res, n })).AsList();
            rows.Reverse(); // chronological for charts
            var latest = rows.Count > 0 ? (object)rows[^1] : null;
            return Results.Ok(new { symbol, resolution = res, count = rows.Count, latest, bars = rows });
        });

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
                    // HARD kill-switch state (default false): order placement is
                    // disabled unless IBKR:AllowOrders=true. Surfaced so the
                    // read-only guarantee is visible/auditable.
                    allowOrders = ibkr.AllowOrders,
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
                // HARD kill-switch state (default false): order placement is
                // disabled unless IBKR:AllowOrders=true. Surfaced so the
                // read-only guarantee on the live account is visible/auditable.
                allowOrders = ibkr.AllowOrders,
                mode = status.Mode,
                brokerLabel = status.BrokerLabel,
                // Active (mode-resolved) client id, redacted to a hint so the
                // operator can confirm WHICH environment is live without the
                // raw id leaking into logs / the public dashboard.
                clientIdInUse = RedactClientId(o.ActiveClientId),
                accounts = status.Accounts,
                accountIdInUse = status.AccountIdInUse,
                // Which IP went into the sso-sessions claim + whether it was
                // the IBKR:SourceIp override or auto-detected from the egress
                // probe — so the operator can confirm the right IP was sent
                // (and that omitting `ip` from the secret worked).
                ipInUse = status.IpInUse,
                ipSource = status.IpSource,
                useX5c = o.UseX5c,
                certificatePresent = !string.IsNullOrWhiteSpace(o.Certificate),
                error = status.Error,
            });
        });

        // GET /api/integrations/ibkr/positions — IBKR open equity
        // positions, READ-ONLY. Mirrors the IG/T212 positions shape so the
        // desk + cockpit can render IBKR holdings with a per-row broker tag,
        // exactly like T212 live positions. Reuses the same cached session as
        // /ibkr/status (no per-call re-auth; IBKR rate-limits that) and
        // places NO order. When the tradepro/ibkr secret is absent the client
        // is disabled and we report {enabled:false, note} so the UI degrades
        // gracefully (renders nothing) rather than erroring the panel.
        app.MapGet("/integrations/ibkr/positions", async (
            TradePro.Api.Providers.IBKR.IBKRClient ibkr,
            CancellationToken ct) =>
        {
            if (!ibkr.IsEnabled)
            {
                // Mirror the /status disabled branch — no network call.
                return Results.Ok(new
                {
                    enabled = false,
                    note = "Populate tradepro/ibkr secret + restart",
                    positions = Array.Empty<object>(),
                });
            }
            var result = await ibkr.GetPositionsAsync(ct);
            if (result.Error is not null)
            {
                // Surface the verbatim IBKR error like the other broker
                // endpoints so the UI shows the real failure, not "0 positions".
                return Results.Ok(new
                {
                    enabled = true,
                    broker = ibkr.BrokerLabel,
                    error = result.Error,
                    positions = Array.Empty<object>(),
                });
            }
            var rows = result.Positions.Select(p =>
            {
                // Compute unrealised % from (mktPrice − avgCost); IBKR carries
                // unrealizedPnl directly (golden) for the absolute. Guard the %
                // against a missing/zero avgCost so we never fabricate a phantom
                // move. Same uniform shape the desk reads for T212/IG.
                decimal? unrealisedPct = null;
                if (p.AvgCost is decimal avg && avg > 0 && p.MarketPrice is decimal cur)
                    unrealisedPct = (cur - avg) / avg * 100m;
                return new
                {
                    ticker = p.Symbol,                 // IBKR ticker / contractDesc
                    instrumentName = p.Symbol,         // IBKR positions carry no separate long name
                    quantity = p.Quantity,
                    averagePricePaid = p.AvgCost,
                    currentPrice = p.MarketPrice,
                    unrealisedAbs = p.UnrealizedPnl,
                    unrealisedPct,
                    currency = p.Currency,
                };
            }).ToArray();
            return Results.Ok(new
            {
                enabled = true,
                broker = ibkr.BrokerLabel,
                mode = ibkr.BrokerLabel,
                count = rows.Length,
                positions = rows,
            });
        });

        // GET /api/integrations/account-state — broker account snapshots the
        // Mac daemons pushed into broker_account_state. This is how the cockpit
        // sees an algo clone's OWN account (e.g. the IBKR PAPER clone DUP656969):
        // net-liquidation, cash, unrealised/daily P&L + the position book. The
        // live IBKRClient (/ibkr/positions) only sees the personal IBKR_LIVE
        // account, so this read is the golden source for the clone's row.
        app.MapGet("/integrations/account-state", async (
            NpgsqlDataSource db, CancellationToken ct) =>
        {
            await using var conn = await db.OpenConnectionAsync(ct);
            var rows = (await conn.QueryAsync(@"
                SELECT broker, account_id, currency, net_liquidation, total_cash,
                       unrealised_pnl, daily_pnl, positions::text AS positions_json,
                       updated_at_utc
                FROM broker_account_state
                ORDER BY broker;")).ToList();
            var accounts = rows.Select(r =>
            {
                string posJson = (string?)r.positions_json ?? "[]";
                using var posDoc = JsonDocument.Parse(
                    string.IsNullOrWhiteSpace(posJson) ? "[]" : posJson);
                return new
                {
                    broker = (string)r.broker,
                    accountId = (string?)r.account_id,
                    currency = (string?)r.currency,
                    netLiquidation = (decimal?)r.net_liquidation,
                    totalCash = (decimal?)r.total_cash,
                    unrealisedPnl = (decimal?)r.unrealised_pnl,
                    dailyPnl = (decimal?)r.daily_pnl,
                    positions = posDoc.RootElement.Clone(),
                    updatedAtUtc = (DateTime)r.updated_at_utc,
                };
            }).ToList();
            return Results.Ok(new { accounts });
        });

        app.MapGet("/integrations/trading212/cash",
            async (
                string? account,
                Trading212Client liveClient,
                Trading212DemoClient demoClient,
                Trading212LiveCashCache liveCashCache,
                Trading212DemoCashCache demoCashCache,
                CancellationToken ct) =>
            {
                var useDemo = !string.Equals(account, "live", StringComparison.OrdinalIgnoreCase);
                if (!useDemo)
                {
                    // Live cash — go through the cache so cockpit polls
                    // don't each hit T212's /account/cash rate-limit
                    // bucket (1 req/1s observed). TTL 30s (configurable
                    // via Trading212:CashCacheSeconds); on 429 the cache
                    // serves the last good snapshot + age footer instead
                    // of surfacing a red error to the user.
                    if (!liveClient.IsEnabled)
                    {
                        return Results.Ok(new
                        {
                            enabled = false,
                            mode = "live",
                            message = "Set Trading212:Mode=live + Trading212:ApiKey to enable.",
                        });
                    }
                    var liveCash = await liveCashCache.GetAsync(ct);
                    var liveCachedAt = liveCashCache.CachedAtUtc ?? DateTime.UtcNow;
                    var liveAge = (DateTime.UtcNow - liveCachedAt).TotalSeconds;
                    return Results.Ok(new
                    {
                        enabled = true,
                        mode = "live",
                        fetchedAtUtc = liveCachedAt,
                        ageSeconds = liveAge,
                        fromCache = liveAge > 1.0,
                        free = liveCash.Free,
                        invested = liveCash.Invested,
                        total = liveCash.Total,
                        blocked = liveCash.Blocked,
                        ppl = liveCash.Ppl,
                        currency = liveCash.Currency,
                        error = liveCash.Error,
                        httpStatus = liveCash.HttpStatus,
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

/// Body for POST /integrations/ibkr/orders — a CONTROLLED single market order
/// used to validate the confirmed IBKR order path end-to-end. SecType defaults
/// to "STK" (equities); pass "CASH" for FX pairs.
public sealed record IBKROrderRequest(string Symbol, string Side, decimal Quantity, string? SecType);

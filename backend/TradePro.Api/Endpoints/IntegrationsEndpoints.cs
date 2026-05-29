using TradePro.Api.Providers.Finnhub;
using TradePro.Api.Providers.Trading212;

namespace TradePro.Api.Endpoints;

public static class IntegrationsEndpoints
{
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
            var rows = result.Positions.Select(p => new
            {
                ticker = p.Epic,                                  // IG epic
                quantity = p.Direction == "SELL" ? -p.Size : p.Size,
                averagePricePaid = (decimal?)p.EntryLevel,
                instrumentName = p.InstrumentName,
                dealId = p.DealId,
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
            CancellationToken ct) =>
        {
            var rows = new List<object>();

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
                    rows.Add(new
                    {
                        broker = ig.BrokerLabel,
                        label = $"IG {(ig.BrokerLabel.EndsWith("LIVE") ? "LIVE" : "DEMO")} (FX + equities)",
                        status = cash.Error is null ? "ok" : "down",
                        currency = cash.Currency,
                        available = cash.Available,
                        balance = cash.Balance,
                        error = cash.Error,
                    });
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

            // IBKR placeholder so the UI shows the slot even before it's
            // wired — sets user expectation that it's coming.
            rows.Add(new
            {
                broker = "IBKR_PAPER",
                label = "IBKR Paper (planned)",
                status = "disabled",
                note = "Not yet integrated — roadmap.",
            });

            return Results.Ok(new
            {
                utc = DateTime.UtcNow,
                brokers = rows,
            });
        });

        // GET /api/integrations/ig/status — IG broker connectivity check.
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
                    decimal? unrealisedAbs = null;
                    if (p.AveragePricePaid is decimal avg && avg > 0
                        && p.CurrentPrice is decimal cur)
                    {
                        unrealisedPct = (cur - avg) / avg * 100m;
                        unrealisedAbs = (cur - avg) * p.Quantity;
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

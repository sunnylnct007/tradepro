using System.Text.Json.Serialization;
using Microsoft.AspNetCore.Diagnostics;
using TradePro.Api.Auth;
using TradePro.Api.Data;
using TradePro.Api.Data.Stores;
using TradePro.Api.Endpoints;
using TradePro.Api.Providers;
using TradePro.Api.Providers.Finnhub;
using TradePro.Api.Providers.Trading212;
using TradePro.Api.Simulation;
using TradePro.Api.Watchlists;

var builder = WebApplication.CreateBuilder(args);

// Fold the shared AWS Secrets Manager bundle (`tradepro/all`) into
// IConfiguration before anything binds. Env vars + appsettings.json
// still win, so local dev with no AWS creds keeps working — see
// SecretsBundleLoader for the kebab-case → Config:Key mapping.
SecretsBundleLoader.LoadInto(builder.Configuration, builder.Configuration);

builder.Services.AddEndpointsApiExplorer();
builder.Services.AddSwaggerGen();

// Serialise enums as their string names everywhere — the frontend
// matches PendingOrder.state via string equality (`o.state ===
// "Pending"`); int serialisation made every record look like 0/1/2/3.
builder.Services.ConfigureHttpJsonOptions(opts =>
{
    opts.SerializerOptions.Converters.Add(new JsonStringEnumConverter());
});

builder.Services.AddCors(options =>
{
    options.AddDefaultPolicy(policy =>
    {
        var origins = builder.Configuration
            .GetSection("Cors:AllowedOrigins")
            .Get<string[]>() ?? ["http://localhost:5173"];
        policy.WithOrigins(origins).AllowAnyHeader().AllowAnyMethod();
    });
});

// Postgres data layer + startup migration runner. Migrations live
// in db/migrations/*.sql and apply once each — running twice is safe.
// See VISION.md Phase 5 for why this exists.
builder.Services.AddPostgresDataLayer(builder.Configuration);

builder.Services.AddFirebaseAuth(builder.Configuration, builder.Environment);

// Typed HttpClients — one per upstream provider, all configurable.
builder.Services.AddHttpClient<YahooFinanceProvider>(c =>
{
    c.DefaultRequestHeaders.UserAgent.ParseAdd("tradepro/0.1");
    c.Timeout = TimeSpan.FromSeconds(15);
});
// Stooq + Binance temporarily unregistered — Stooq now requires an API key
// and Binance only supports crypto which we don't surface yet. The classes
// are kept in the codebase so we can wire them back in with a click.
builder.Services.AddHttpClient<StooqProvider>(c =>
{
    c.DefaultRequestHeaders.UserAgent.ParseAdd("tradepro/0.1");
    c.Timeout = TimeSpan.FromSeconds(15);
});
builder.Services.AddHttpClient<BinanceProvider>(c =>
{
    c.DefaultRequestHeaders.UserAgent.ParseAdd("tradepro/0.1");
    c.Timeout = TimeSpan.FromSeconds(15);
});
builder.Services.AddHttpClient<YahooSearchProvider>(c =>
{
    c.DefaultRequestHeaders.UserAgent.ParseAdd("tradepro/0.1");
    c.Timeout = TimeSpan.FromSeconds(8);
});

// Only Yahoo Finance is advertised to the registry today.
builder.Services.AddScoped<IMarketDataProvider>(sp => sp.GetRequiredService<YahooFinanceProvider>());
builder.Services.AddScoped<IMarketDataRegistry, MarketDataRegistry>();

// Trading 212 integration — read-only portfolio + instruments registry.
// Off by default; enable by setting Trading212:Mode=demo|live and supplying
// API key/secret via env (TRADEPRO_T212_API_KEY / TRADEPRO_T212_API_SECRET).
// T212 has no OHLC endpoint, so this is *not* a Yahoo replacement.
builder.Services
    .AddOptions<Trading212Options>()
    .Bind(builder.Configuration.GetSection(Trading212Options.SectionName));
builder.Services.AddHttpClient<Trading212Client>(c =>
{
    c.DefaultRequestHeaders.UserAgent.ParseAdd("tradepro/0.1");
});
// Sibling DEMO client — separate type (not just a different config
// section) so the type system enforces "this code path can never
// hit live.trading212.com". Used by the Approve handler on pending
// paper orders + the upcoming demo orderbook view. Binds to the
// Trading212Demo section so the live key/secret pair stays untouched.
builder.Services
    .AddOptions<Trading212DemoOptions>()
    .Bind(builder.Configuration.GetSection(Trading212DemoOptions.SectionName));
builder.Services.AddHttpClient<Trading212DemoClient>(c =>
{
    c.DefaultRequestHeaders.UserAgent.ParseAdd("tradepro/0.1");
});
// Singleton — the instruments cache lives for the life of the
// process. The cache loads from disk on construction and refreshes
// lazily on first access if older than 24h.
builder.Services.AddSingleton<Trading212InstrumentsService>();
// Caches /equity/positions for 30s by default (Trading212:PositionsCacheSeconds
// to override). Stops dashboard + portfolio page from independently
// tripping T212's 1 req/1s rate limit on every navigation. On 429
// the cache serves the last successful response with FromCache=true.
builder.Services.AddSingleton<Trading212PositionsCache>();
builder.Services.AddSingleton<Trading212DemoPositionsCache>();
builder.Services.AddSingleton<Trading212DemoCashCache>();
builder.Services.AddScoped<TradePro.Api.Positions.PositionReconciler>();
builder.Services.AddScoped<TradePro.Api.Positions.TradePlanService>();
builder.Services.AddScoped<TradePro.Api.Risk.RiskGate>();

// Finnhub — off-by-default earnings-calendar provider. Free tier
// signup gives 60 req/min which is plenty for occasional checks.
// Set Finnhub__ApiKey in env / appsettings to enable.
builder.Services
    .AddOptions<FinnhubOptions>()
    .Bind(builder.Configuration.GetSection(FinnhubOptions.SectionName));
builder.Services.AddHttpClient<FinnhubClient>(c =>
{
    c.DefaultRequestHeaders.UserAgent.ParseAdd("tradepro/0.1");
});

builder.Services.AddScoped<ISignalStrategy, BuyAndHoldStrategy>();
builder.Services.AddScoped<ISignalStrategy, SmaCrossoverStrategy>();
builder.Services.AddScoped<ISignalStrategy, RsiMeanReversionStrategy>();
builder.Services.AddScoped<ISignalStrategy, MacdSignalCrossStrategy>();
builder.Services.AddScoped<ISignalStrategy, DonchianBreakoutStrategy>();
builder.Services.AddScoped<ISignalStrategy, IchimokuCloudStrategy>();
builder.Services.AddScoped<ISignalStrategy, BollingerBounceStrategy>();
builder.Services.AddScoped<IStrategyRegistry, StrategyRegistry>();
builder.Services.AddScoped<ISimulator, Simulator>();
builder.Services.AddScoped<ISignalEngine, SignalEngine>();
builder.Services.AddScoped<ISignalScanner, SignalScanner>();
builder.Services.AddScoped<IHitRateEngine, HitRateEngine>();
// Phase 5 — every store now backed by Postgres. Survives redeploys.
// In-memory + file-backed implementations are kept in the tree for
// reference and tests but no longer registered. See VISION.md
// Principle 1: "No new in-memory store."
builder.Services.AddSingleton<IWatchlistStore, PostgresWatchlistStore>();
builder.Services.AddSingleton<ICompareStore, PostgresCompareStore>();
builder.Services.AddSingleton<IHeartbeatStore, PostgresHeartbeatStore>();
builder.Services.AddSingleton<ISettingsStore, PostgresSettingsStore>();
builder.Services.AddSingleton<IDocumentStore, PostgresDocumentStore>();
builder.Services.AddSingleton<IPaperBacktestStore, PostgresPaperBacktestStore>();
builder.Services.AddSingleton<IPaperStrategiesStore, PostgresPaperStrategiesStore>();
builder.Services.AddSingleton<IPaperSnapshotStore, PostgresPaperSnapshotStore>();
builder.Services.AddSingleton<IPendingOrdersStore, PostgresPendingOrdersStore>();
builder.Services.AddSingleton<ISessionRequestsStore, PostgresSessionRequestsStore>();
builder.Services.AddSingleton<IPaperStrategyStatusStore, PostgresPaperStrategyStatusStore>();
// OMS Phase 1 — order persistence + lifecycle. Mode service depends on
// the OmsService for the cancel-on-flip path, so register OmsService
// first. Mode service is Singleton so the in-memory "current mode"
// state survives across requests (Phase 1; Phase 2 swaps to a
// persisted impl).
// OmsService factory so it can resolve Trading212DemoClient per-call
// (transient HttpClient — singleton can't hold it without leaking the
// HttpMessageHandler). The ILogger comes from the standard provider.
builder.Services.AddSingleton<TradePro.Api.Oms.IOmsService>(sp =>
    new TradePro.Api.Oms.PostgresOmsService(
        sp.GetRequiredService<Npgsql.NpgsqlDataSource>(),
        sp,
        sp.GetRequiredService<ILogger<TradePro.Api.Oms.PostgresOmsService>>()));
builder.Services.AddSingleton<TradePro.Api.Oms.IOmsModeService, TradePro.Api.Oms.InMemoryOmsModeService>();
// Background poller that polls T212 demo for fills/cancellations on
// SUBMITTED orders and transitions OMS accordingly. Closes the
// SUBMITTED → FILLED loop without operator intervention.
builder.Services.AddHostedService<TradePro.Api.Oms.OmsFillPoller>();
builder.Services.AddSingleton<IIntradayLeaderboardStore, PostgresIntradayLeaderboardStore>();
// Phase 6 — event-sourced orders + fills + domain events. Pending-orders
// queue becomes a *projection* of this log; risk decisions and fills
// all leave a trail. See VISION.md Principle 3.
builder.Services.AddSingleton<SqsTriggerService>();
builder.Services.AddSingleton<OrdersRepository>();
builder.Services.AddSingleton<EventStream>();

var app = builder.Build();

// Global exception handler — turn unhandled exceptions into JSON the
// frontend can render. Without this, ASP.NET returns 500 with an empty
// body, so the user just sees "Error: 500 :" with no signal. With it,
// they see the actual exception type + message in the UI, and the
// container log carries the full stack via the ILogger call below.
app.UseExceptionHandler(eb => eb.Run(async ctx =>
{
    var ex = ctx.Features.Get<IExceptionHandlerFeature>()?.Error;
    var logger = ctx.RequestServices.GetRequiredService<ILogger<Program>>();
    logger.LogError(ex, "Unhandled exception on {Method} {Path}",
        ctx.Request.Method, ctx.Request.Path);
    ctx.Response.StatusCode = StatusCodes.Status500InternalServerError;
    ctx.Response.ContentType = "application/json";
    await ctx.Response.WriteAsJsonAsync(new
    {
        error = ex?.Message ?? "unhandled exception",
        type = ex?.GetType().Name,
        path = ctx.Request.Path.Value,
        traceId = ctx.TraceIdentifier,
    });
}));

if (app.Environment.IsDevelopment())
{
    app.UseSwagger();
    app.UseSwaggerUI();
}

app.UseCors();
app.UseAuthentication();
app.UseAuthorization();

// /health stays public so uptime pings don't need a token.
app.MapHealthEndpoints();
// /health/integrations probes T212 + Finnhub live, derives Yahoo +
// Ollama state from compare-cache freshness. Public so the Health
// page can poll without auth, same as the rest of /health.
app.MapIntegrationsHealthEndpoints();

// Everything under /api requires a verified Firebase ID token from one of
// the allow-listed UIDs. In dev, leaving Firebase:AllowedUserIds empty lets
// any signed-in user through (handy for testing).
// User-facing routes (frontend, signed-in via Firebase).
var api = app.MapGroup("/api").RequireAuthorization("AllowedUsers");
api.MapMarketDataEndpoints();
api.MapSimulationEndpoints();
api.MapSignalEndpoints();
api.MapWatchlistEndpoints();
api.MapCompareEndpoints();
api.MapWorkerHealthEndpoints();
api.MapSettingsEndpoints();
api.MapDocumentEndpoints();
api.MapSymbolAnalysisEndpoints();
api.MapIntegrationsEndpoints();
api.MapInstrumentEndpoints();
api.MapPaperBacktestEndpoints();
api.MapOrdersEndpoints();
api.MapOmsEndpoints();
// User-facing ops queue (run-intraday + list / cancel). Mac worker
// routes mount on the ingest group below — same store, different
// trust boundary.
api.MapOpsUserEndpoints();
api.MapSettingsKvEndpoints();
api.MapUniverseUserEndpoints();
api.MapEquityPipelineUserEndpoints();
api.MapLivePortfolioUserEndpoints();
api.MapPositionsEndpoints();
api.MapTradePlanEndpoints();
api.MapSystemStateEndpoints();
api.MapRiskEndpoints();
api.MapCostFeedbackEndpoints();
// /api/quant/backtest/* — UI-triggered quant backtests. Same
// session_requests queue, kind="backtest". Worker poll/complete
// routes mount on the ingest group below.
api.MapQuantEndpoints();
api.MapAdminEndpoints();

// SSE event stream — AllowAnonymous (EventSource can't send auth headers).
app.MapEventsEndpoints();

// Mac-pushed ingest routes (no human, static Bearer token).
var ingest = app.MapGroup("/api");
ingest.MapIngestEndpoints();
ingest.MapOpsWorkerEndpoints();
ingest.MapQuantWorkerEndpoints();
ingest.MapUniverseWorkerEndpoints();
ingest.MapEquityPipelineIngestEndpoints();
ingest.MapLivePortfolioIngestEndpoints();

app.Run();

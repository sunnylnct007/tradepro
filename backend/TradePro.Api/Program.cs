using System.Text.Json.Serialization;
using TradePro.Api.Auth;
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
// Singleton — the instruments cache lives for the life of the
// process. The cache loads from disk on construction and refreshes
// lazily on first access if older than 24h.
builder.Services.AddSingleton<Trading212InstrumentsService>();
// Caches /equity/positions for 30s by default (Trading212:PositionsCacheSeconds
// to override). Stops dashboard + portfolio page from independently
// tripping T212's 1 req/1s rate limit on every navigation. On 429
// the cache serves the last successful response with FromCache=true.
builder.Services.AddSingleton<Trading212PositionsCache>();

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
builder.Services.AddSingleton<IWatchlistStore, InMemoryWatchlistStore>();
// File-backed compare store survives API restarts + deploys. The path
// (Compare:StorePath, default /data/compare in containers, ~/.tradepro/
// server-cache locally) is mounted into the container by compose.
builder.Services.AddSingleton<ICompareStore, FileCompareStore>();
builder.Services.AddSingleton<IHeartbeatStore, InMemoryHeartbeatStore>();
builder.Services.AddSingleton<ISettingsStore, FileSettingsStore>();
builder.Services.AddSingleton<IDocumentStore, FileDocumentStore>();
// Paper-trading backtest reports pushed from the Mac. In-memory for
// now — the UI only needs "show me recent runs" and history-across-
// restarts isn't a v1 requirement.
builder.Services.AddSingleton<IPaperBacktestStore, InMemoryPaperBacktestStore>();
builder.Services.AddSingleton<IPaperStrategiesStore, InMemoryPaperStrategiesStore>();
// Per-session ledger snapshots pushed at the end of every
// `tradepro-paper --push` run. In-memory, capped at 100 sessions.
builder.Services.AddSingleton<IPaperSnapshotStore, InMemoryPaperSnapshotStore>();
// Pending paper orders awaiting human Approve / Reject on the UI
// (T212 manual-placement mode). In-memory + capped at 200.
builder.Services.AddSingleton<IPendingOrdersStore, InMemoryPendingOrdersStore>();

var app = builder.Build();

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
api.MapIntegrationsEndpoints();
api.MapInstrumentEndpoints();
api.MapPaperBacktestEndpoints();

// Mac-pushed ingest routes (no human, static Bearer token).
var ingest = app.MapGroup("/api");
ingest.MapIngestEndpoints();

app.Run();

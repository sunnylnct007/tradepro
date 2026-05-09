using TradePro.Api.Auth;
using TradePro.Api.Endpoints;
using TradePro.Api.Providers;
using TradePro.Api.Providers.Finnhub;
using TradePro.Api.Providers.Trading212;
using TradePro.Api.Simulation;
using TradePro.Api.Watchlists;

var builder = WebApplication.CreateBuilder(args);

builder.Services.AddEndpointsApiExplorer();
builder.Services.AddSwaggerGen();

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

// Mac-pushed ingest routes (no human, static Bearer token).
var ingest = app.MapGroup("/api");
ingest.MapIngestEndpoints();

app.Run();

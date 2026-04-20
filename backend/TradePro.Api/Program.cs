using TradePro.Api.Auth;
using TradePro.Api.Endpoints;
using TradePro.Api.Providers;
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

// Only Yahoo Finance is advertised to the registry today.
builder.Services.AddScoped<IMarketDataProvider>(sp => sp.GetRequiredService<YahooFinanceProvider>());
builder.Services.AddScoped<IMarketDataRegistry, MarketDataRegistry>();

builder.Services.AddScoped<ISignalStrategy, BuyAndHoldStrategy>();
builder.Services.AddScoped<ISignalStrategy, SmaCrossoverStrategy>();
builder.Services.AddScoped<ISignalStrategy, RsiMeanReversionStrategy>();
builder.Services.AddScoped<IStrategyRegistry, StrategyRegistry>();
builder.Services.AddScoped<ISimulator, Simulator>();
builder.Services.AddScoped<ISignalEngine, SignalEngine>();
builder.Services.AddScoped<ISignalScanner, SignalScanner>();
builder.Services.AddSingleton<IWatchlistStore, InMemoryWatchlistStore>();

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
var api = app.MapGroup("/api").RequireAuthorization("AllowedUsers");
api.MapMarketDataEndpoints();
api.MapSimulationEndpoints();
api.MapSignalEndpoints();
api.MapWatchlistEndpoints();

app.Run();

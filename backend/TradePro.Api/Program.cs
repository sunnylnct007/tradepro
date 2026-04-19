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

// Typed HttpClients — one per upstream provider, all configurable.
builder.Services.AddHttpClient<YahooFinanceProvider>(c =>
{
    c.DefaultRequestHeaders.UserAgent.ParseAdd("tradepro/0.1");
    c.Timeout = TimeSpan.FromSeconds(15);
});
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

// Register each provider as IMarketDataProvider so the registry can resolve by name.
builder.Services.AddScoped<IMarketDataProvider>(sp => sp.GetRequiredService<YahooFinanceProvider>());
builder.Services.AddScoped<IMarketDataProvider>(sp => sp.GetRequiredService<StooqProvider>());
builder.Services.AddScoped<IMarketDataProvider>(sp => sp.GetRequiredService<BinanceProvider>());
builder.Services.AddScoped<IMarketDataRegistry, MarketDataRegistry>();

builder.Services.AddScoped<ISignalStrategy, BuyAndHoldStrategy>();
builder.Services.AddScoped<ISignalStrategy, SmaCrossoverStrategy>();
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

app.MapHealthEndpoints();
app.MapMarketDataEndpoints();
app.MapSimulationEndpoints();
app.MapSignalEndpoints();
app.MapWatchlistEndpoints();

app.Run();

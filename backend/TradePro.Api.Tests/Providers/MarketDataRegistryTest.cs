using Microsoft.Extensions.Configuration;
using TradePro.Api.Models;
using TradePro.Api.Providers;
using Xunit;

namespace TradePro.Api.Tests.Providers;

/// <summary>Candles come from the golden bar store and nowhere else (21 Nov
/// 2026). The registry used to default to whichever provider registered first
/// — Yahoo — and Yahoo was registered as a candles provider, which is how the
/// production chart read Yahoo for six months.</summary>
public class MarketDataRegistryTest
{
    private sealed class Fake(string name) : IMarketDataProvider
    {
        public string Name => name;
        public Task<CandleSeries> GetCandlesAsync(string symbol, string interval, DateTime from, DateTime to, CancellationToken ct)
            => throw new NotSupportedException();
    }

    private static IConfiguration Empty() => new ConfigurationBuilder().Build();

    [Fact]
    public void The_default_is_the_bar_store_even_when_another_provider_registered_first()
    {
        var reg = new MarketDataRegistry(new IMarketDataProvider[] { new Fake("something_else"), new Fake("bar_cache") }, Empty());
        Assert.Equal("bar_cache", reg.Resolve(null).Name);
        Assert.Equal("bar_cache", reg.Resolve("").Name);
    }

    [Fact]
    public void Yahoo_cannot_be_asked_for_candles()
    {
        var reg = new MarketDataRegistry(new IMarketDataProvider[] { new Fake("bar_cache") }, Empty());
        var ex = Assert.Throws<ArgumentException>(() => reg.Resolve("yahoo"));
        Assert.Contains("Unknown provider 'yahoo'", ex.Message);
    }

    [Fact]
    public void Program_does_not_register_yahoo_as_a_candles_provider()
    {
        // Source-level pin: the registration line is the fault, so the test reads the file.
        var root = new DirectoryInfo(AppContext.BaseDirectory);
        while (root != null && !File.Exists(Path.Combine(root.FullName, "TradePro.Api", "Program.cs"))) root = root.Parent;
        Assert.NotNull(root);
        var src = File.ReadAllText(Path.Combine(root!.FullName, "TradePro.Api", "Program.cs"));
        Assert.DoesNotContain("AddScoped<IMarketDataProvider>(sp => sp.GetRequiredService<YahooFinanceProvider>())", src);
    }
}

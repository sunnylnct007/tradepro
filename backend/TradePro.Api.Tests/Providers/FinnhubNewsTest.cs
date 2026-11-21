using System.Net;
using Microsoft.Extensions.Logging.Abstractions;
using Microsoft.Extensions.Options;
using TradePro.Api.Providers.Finnhub;
using Xunit;

namespace TradePro.Api.Tests.Providers;

/// <summary>
/// A news read that FAILED must never look like "no news". On 20 Nov 2026 the
/// desk's feed returned zero headlines for NVDA, AMD and SPY in the worst AI
/// sell-off week of the year, because every failure was swallowed into [].
/// </summary>
public class FinnhubNewsTest
{
    private sealed class Stub(HttpStatusCode code, string body) : HttpMessageHandler
    {
        public Uri? Last;
        protected override Task<HttpResponseMessage> SendAsync(HttpRequestMessage r, CancellationToken ct)
        {
            Last = r.RequestUri;
            return Task.FromResult(new HttpResponseMessage(code) { Content = new StringContent(body) });
        }
    }

    private static FinnhubClient Client(Stub h, string key = "k") =>
        new(new HttpClient(h), Options.Create(new FinnhubOptions { ApiKey = key }),
            NullLogger<FinnhubClient>.Instance);

    [Fact]
    public async Task Headlines_parse_and_carry_no_error()
    {
        var h = new Stub(HttpStatusCode.OK,
            """[{"id":1,"datetime":1763600000,"headline":"Nvidia slides","source":"Reuters","summary":"s","url":"u","related":"NVDA","category":"company"}]""");
        var (items, error) = await Client(h).GetCompanyNewsAsync("nvda", 7, CancellationToken.None);
        Assert.Null(error);
        Assert.Single(items);
        Assert.Equal("Nvidia slides", items[0].Headline);
        Assert.Contains("symbol=NVDA", h.Last!.Query);
    }

    [Fact]
    public async Task A_failed_read_says_so_instead_of_returning_a_quiet_empty_list()
    {
        var (items, error) = await Client(new Stub(HttpStatusCode.TooManyRequests, "limit"))
            .GetCompanyNewsAsync("NVDA", 7, CancellationToken.None);
        Assert.Empty(items);
        Assert.Contains("429", error);
        Assert.DoesNotContain("token", error);   // never the credential
    }

    [Fact]
    public async Task No_key_is_an_error_not_an_empty_feed()
    {
        var (items, error) = await Client(new Stub(HttpStatusCode.OK, "[]"), key: "")
            .GetMarketNewsAsync("general", CancellationToken.None);
        Assert.Empty(items);
        Assert.NotNull(error);
    }
}

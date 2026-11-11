using System.Net.Http;
using System.Threading;
using System.Threading.Tasks;
using Microsoft.Extensions.Http;
using TradePro.Api.Providers.IBKR;
using Xunit;

namespace TradePro.Api.Tests.Providers;

/// <summary>
/// The ssodh/init verdict (authenticated / competing / connected) must survive
/// the request that produced it.
///
/// THE DEFECT, 11 Nov 2026. IBKRClient is a transient typed HttpClient — one
/// instance per request — and the verdict lived on it. The one instance that
/// ran the bring-up held the flags; the instance serving /integrations/ibkr/status
/// read null. On a live box, through a three-hour dark feed, the status endpoint
/// said competing=null connected=null authStatusRaw=null all day, which a reader
/// takes as "nothing to report". The answer existed only in the server log.
/// </summary>
public sealed class IBKRSessionVerdictTest
{
    [Fact]
    public void A_fresh_client_instance_reads_the_verdict_another_instance_recorded()
    {
        var shared = new IBKRSessionCache();
        var first = NewClient(shared);
        var second = NewClient(shared);          // the /status request's instance

        Assert.Null(second.LastConnected);         // nothing has happened yet: unknown, not false
        shared.SetInitVerdict(authenticated: true, competing: false, connected: false,
                              raw: "{\"authenticated\":true,\"competing\":false,\"connected\":false}");

        Assert.True(second.LastAuthenticated);
        Assert.False(second.LastCompeting);
        Assert.False(second.LastConnected);        // the discriminator a dark feed needs
        Assert.NotNull(second.LastAuthStatusAtUtc);
        Assert.Contains("connected", second.LastAuthStatusRaw);
        Assert.Equal(first.LastConnected, second.LastConnected);
    }

    [Fact]
    public void The_account_bind_verdict_is_shared_the_same_way()
    {
        var shared = new IBKRSessionCache();
        shared.SetAccountSelect(false, "no account selected");
        var client = NewClient(shared);
        Assert.False(client.LastAccountSelectOk);
        Assert.Equal("no account selected", client.LastAccountSelectRaw);
    }

    [Fact]
    public void Separate_caches_do_not_leak_into_each_other()
    {
        var a = new IBKRSessionCache();
        var b = new IBKRSessionCache();
        a.SetInitVerdict(true, true, true, "{}");
        Assert.Null(NewClient(b).LastConnected);
        Assert.True(NewClient(a).LastConnected);
    }

    private static IBKRClient NewClient(IBKRSessionCache session)
    {
        var http = new HttpClient(new NoopHandler()) { BaseAddress = new System.Uri("https://api.ibkr.test/") };
        var ipResolver = new IBKREgressIpResolver(
            new SingleClientFactory(new HttpClient(new NoopHandler())),
            Microsoft.Extensions.Logging.Abstractions.NullLogger<IBKREgressIpResolver>.Instance);
        return new IBKRClient(
            http,
            Microsoft.Extensions.Options.Options.Create(new IBKROptions()),
            session,
            ipResolver,
            new IBKRPauseState(),
            new IBKRMarketDataLines(10),
            Microsoft.Extensions.Logging.Abstractions.NullLogger<IBKRClient>.Instance);
    }

    private sealed class NoopHandler : HttpMessageHandler
    {
        protected override Task<HttpResponseMessage> SendAsync(HttpRequestMessage request, CancellationToken ct)
            => Task.FromResult(new HttpResponseMessage(System.Net.HttpStatusCode.OK));
    }

    private sealed class SingleClientFactory : IHttpClientFactory
    {
        private readonly HttpClient _client;
        public SingleClientFactory(HttpClient client) => _client = client;
        public HttpClient CreateClient(string name) => _client;
    }
}

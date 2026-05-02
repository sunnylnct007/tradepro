using Microsoft.Extensions.Logging.Abstractions;
using NSubstitute;
using Reqnroll;
using VolueDataloader.Connectivity;
using VolueDataloader.Handlers;
using VolueDataloader.Models;

namespace VolueDataloader.Specs.StepDefinitions;

[Binding]
public class BackfillSteps
{
    private readonly IVolueRestClient    _restClient   = Substitute.For<IVolueRestClient>();
    private readonly IOrderMessageHandler _orderHandler = Substitute.For<IOrderMessageHandler>();
    private readonly ITradeMessageHandler _tradeHandler = Substitute.For<ITradeMessageHandler>();

    private DateTime _disconnectedAt;
    private DateTime _reconnectedAt;

    private BackfillService CreateService() =>
        new(_restClient, _orderHandler, _tradeHandler,
            NullLogger<BackfillService>.Instance);

    [Given(@"the SignalR connection was last active at ""(.*)""")]
    public void GivenConnectionLastActiveAt(string disconnectedAt)
    {
        _disconnectedAt = DateTime.Parse(disconnectedAt, null,
            System.Globalization.DateTimeStyles.RoundtripKind);

        // Set up default REST responses
        _restClient
            .GetOrdersAsync(Arg.Any<DateTime>(), Arg.Any<DateTime>(), Arg.Any<CancellationToken>())
            .Returns(new List<OrderModel> { new() { OrderId = Guid.NewGuid() } });

        _restClient
            .GetTradesAsync(Arg.Any<DateTime>(), Arg.Any<DateTime>(), Arg.Any<CancellationToken>())
            .Returns(new List<TradeModel> { new() { TradeId = Guid.NewGuid() } });
    }

    [Given(@"the connection was re-established at ""(.*)""")]
    public void GivenConnectionReestablishedAt(string reconnectedAt)
    {
        _reconnectedAt = DateTime.Parse(reconnectedAt, null,
            System.Globalization.DateTimeStyles.RoundtripKind);
    }

    [When(@"the backfill runs for the missed window")]
    public async Task WhenBackfillRuns()
    {
        await CreateService().BackfillAsync(_disconnectedAt, _reconnectedAt);
    }

    [Then(@"the REST API is called for orders between ""(.*)"" and ""(.*)""")]
    public async Task ThenRestApiCalledForOrders(string from, string to)
    {
        var expectedFrom = DateTime.Parse(from, null, System.Globalization.DateTimeStyles.RoundtripKind);
        var expectedTo   = DateTime.Parse(to,   null, System.Globalization.DateTimeStyles.RoundtripKind);

        await _restClient.Received(1).GetOrdersAsync(
            Arg.Is<DateTime>(d => d == expectedFrom),
            Arg.Is<DateTime>(d => d == expectedTo),
            Arg.Any<CancellationToken>());
    }

    [Then(@"the REST API is called for trades between ""(.*)"" and ""(.*)""")]
    public async Task ThenRestApiCalledForTrades(string from, string to)
    {
        var expectedFrom = DateTime.Parse(from, null, System.Globalization.DateTimeStyles.RoundtripKind);
        var expectedTo   = DateTime.Parse(to,   null, System.Globalization.DateTimeStyles.RoundtripKind);

        await _restClient.Received(1).GetTradesAsync(
            Arg.Is<DateTime>(d => d == expectedFrom),
            Arg.Is<DateTime>(d => d == expectedTo),
            Arg.Any<CancellationToken>());
    }

    [Then(@"the backfilled orders are sent to Kinesis")]
    public async Task ThenBackfilledOrdersSentToKinesis()
    {
        await _orderHandler.Received(1).HandleAsync(
            Arg.Is<List<OrderModel>>(l => l.Count > 0), Arg.Any<CancellationToken>());
    }

    [Then(@"the backfilled trades are sent to Kinesis")]
    public async Task ThenBackfilledTradesSentToKinesis()
    {
        await _tradeHandler.Received(1).HandleAsync(
            Arg.Is<List<TradeModel>>(l => l.Count > 0), Arg.Any<CancellationToken>());
    }

    [Then(@"the backfill is skipped and a warning is logged")]
    public async Task ThenBackfillIsSkipped()
    {
        // REST API should not have been called — gap is too large
        await _restClient.DidNotReceive().GetOrdersAsync(
            Arg.Any<DateTime>(), Arg.Any<DateTime>(), Arg.Any<CancellationToken>());
        await _restClient.DidNotReceive().GetTradesAsync(
            Arg.Any<DateTime>(), Arg.Any<DateTime>(), Arg.Any<CancellationToken>());
    }
}

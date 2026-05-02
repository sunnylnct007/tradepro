using Microsoft.Extensions.Logging.Abstractions;
using Microsoft.Extensions.Options;
using NSubstitute;
using Reqnroll;
using VolueDataloader.Configuration;
using VolueDataloader.Handlers;
using VolueDataloader.Models;
using VolueDataloader.Models.Enums;
using VolueDataloader.Sinks;

namespace VolueDataloader.Specs.StepDefinitions;

[Binding]
public class TradeStreamingSteps
{
    private IEventSink       _kinesisSink = Substitute.For<IEventSink>();
    private StreamingConfig  _cfg         = new();
    private List<TradeModel> _trades      = new();

    private TradeMessageHandler CreateHandler() =>
        new(_kinesisSink,
            Options.Create(_cfg),
            NullLogger<TradeMessageHandler>.Instance);

    // Background step shared with OrderStreamingSteps via [Binding] on the same step text.

    [Given(@"a sell trade for contract ""(.*)""")]
    public void GivenASellTradeForContract(string _)
    {
        _trades = new List<TradeModel>
        {
            new()
            {
                TradeId  = Guid.NewGuid(),
                Exchange = Exchange.EPEX,
                Side     = Side.Sell,
                Price    = 95.0,
                Quantity = 5,
                Contract = new ContractModel
                {
                    DeliveryStartTime = new DateTime(2025, 1, 15, 12, 0, 0, DateTimeKind.Utc),
                    DeliveryEndTime   = new DateTime(2025, 1, 15, 12, 30, 0, DateTimeKind.Utc),
                },
            }
        };
    }

    [Given(@"(\d+) trade events")]
    public void GivenNTradeEvents(int count)
    {
        _trades = Enumerable.Range(0, count).Select(_ => new TradeModel
        {
            TradeId  = Guid.NewGuid(),
            Exchange = Exchange.EPEX,
            Side     = Side.Sell,
            Price    = 95.0,
            Quantity = 5,
            Contract = new ContractModel
            {
                DeliveryStartTime = new DateTime(2025, 1, 15, 12, 0, 0, DateTimeKind.Utc),
                DeliveryEndTime   = new DateTime(2025, 1, 15, 12, 30, 0, DateTimeKind.Utc),
            },
        }).ToList();
    }

    [Given(@"an empty trades message")]
    public void GivenAnEmptyTradesMessage() => _trades = new List<TradeModel>();

    [When(@"the trade event is received via SignalR")]
    [When(@"the trade message is received via SignalR")]
    public async Task WhenTradeMessageReceived()
    {
        _cfg = new StreamingConfig { KinesisEnabled = true };
        await CreateHandler().HandleAsync(_trades);
    }

    [Then(@"the trade is sent to the Kinesis trades stream")]
    public async Task ThenTradeSentToKinesis()
    {
        await _kinesisSink.Received(1).SendAsync(Arg.Any<object>(), Arg.Any<CancellationToken>());
    }

    [Then(@"(\d+) records are sent to the Kinesis trades stream")]
    public async Task ThenNRecordsSentToKinesisTrades(int count)
    {
        await _kinesisSink.Received(count).SendAsync(Arg.Any<object>(), Arg.Any<CancellationToken>());
    }

    [Then(@"no trade records are sent to Kinesis")]
    public async Task ThenNoTradeRecordsSentToKinesis()
    {
        await _kinesisSink.DidNotReceive().SendAsync(Arg.Any<object>(), Arg.Any<CancellationToken>());
    }
}

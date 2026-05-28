using Microsoft.Extensions.Logging.Abstractions;
using Microsoft.Extensions.Options;
using NSubstitute;
using NSubstitute.ExceptionExtensions;
using Reqnroll;
using VolueDataloader.Configuration;
using VolueDataloader.Handlers;
using VolueDataloader.Models;
using VolueDataloader.Models.Enums;
using VolueDataloader.Sinks;

namespace VolueDataloader.Specs.StepDefinitions;

[Binding]
public class OrderStreamingSteps
{
    private IEventSink      _kinesisSink = Substitute.For<IEventSink>();
    private StreamingConfig _cfg         = new();
    private List<OrderModel> _orders     = new();

    private OrderMessageHandler CreateHandler() =>
        new(_kinesisSink,
            Options.Create(_cfg),
            NullLogger<OrderMessageHandler>.Instance);

    [Given(@"Kinesis streaming is enabled")]
    public void GivenKinesisStreamingIsEnabled()
    {
        _cfg = new StreamingConfig { KinesisEnabled = true };
    }

    [Given(@"a buy order for contract ""(.*)""")]
    public void GivenABuyOrderForContract(string _)
    {
        _orders = new List<OrderModel>
        {
            new()
            {
                OrderId       = Guid.NewGuid(),
                Exchange      = Exchange.EPEX,
                Side          = Side.Buy,
                Price         = 100.5,
                TotalQuantity = 10,
                Contract      = new ContractModel
                {
                    DeliveryStartTime = new DateTime(2025, 1, 15, 12, 0, 0, DateTimeKind.Utc),
                    DeliveryEndTime   = new DateTime(2025, 1, 15, 12, 30, 0, DateTimeKind.Utc),
                },
            }
        };
    }

    [Given(@"(\d+) order updates")]
    public void GivenNOrderUpdates(int count)
    {
        _orders = Enumerable.Range(0, count).Select(_ => new OrderModel
        {
            OrderId       = Guid.NewGuid(),
            Exchange      = Exchange.EPEX,
            Side          = Side.Buy,
            Price         = 99.0,
            TotalQuantity = 5,
            Contract      = new ContractModel
            {
                DeliveryStartTime = new DateTime(2025, 1, 15, 12, 0, 0, DateTimeKind.Utc),
                DeliveryEndTime   = new DateTime(2025, 1, 15, 12, 30, 0, DateTimeKind.Utc),
            },
        }).ToList();
    }

    [Given(@"an empty orders message")]
    public void GivenAnEmptyOrdersMessage() => _orders = new List<OrderModel>();

    [Given(@"the Kinesis stream is unavailable")]
    public void GivenKinesisIsUnavailable()
    {
        _kinesisSink.SendAsync(Arg.Any<object>(), Arg.Any<CancellationToken>())
                    .ThrowsAsync(new Exception("Kinesis unavailable"));
    }

    [When(@"the order update is received via SignalR")]
    [When(@"the order message is received via SignalR")]
    public async Task WhenOrderMessageReceived()
    {
        await CreateHandler().HandleAsync(_orders);
    }

    [Then(@"the order is sent to the Kinesis orders stream")]
    public async Task ThenOrderSentToKinesis()
    {
        await _kinesisSink.Received(1).SendAsync(Arg.Any<object>(), Arg.Any<CancellationToken>());
    }

    [Then(@"(\d+) records are sent to the Kinesis orders stream")]
    public async Task ThenNRecordsSentToKinesis(int count)
    {
        await _kinesisSink.Received(count).SendAsync(Arg.Any<object>(), Arg.Any<CancellationToken>());
    }

    [Then(@"no order records are sent to Kinesis")]
    public async Task ThenNoOrderRecordsSentToKinesis()
    {
        await _kinesisSink.DidNotReceive().SendAsync(Arg.Any<object>(), Arg.Any<CancellationToken>());
    }
}

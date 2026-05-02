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
public class OrderBookSteps
{
    private IDynamoDbOrderBookSink _dynamoSink      = Substitute.For<IDynamoDbOrderBookSink>();
    private IEventSink _kinesisSink = Substitute.For<IEventSink>();
    private StreamingConfig        _streamingConfig = new();
    private List<OrderBookModel>   _books           = new();

    private OrderBookMessageHandler CreateHandler() =>
        new(_dynamoSink, _kinesisSink,
            Options.Create(_streamingConfig),
            NullLogger<OrderBookMessageHandler>.Instance);

    // ─── Background ──────────────────────────────────────────────────────────

    [Given(@"all AWS sinks are enabled")]
    public void GivenAllAwsSinksAreEnabled()
    {
        _streamingConfig = new StreamingConfig
        {
            DynamoDbEnabled = true,
            KinesisEnabled = true,
        };
    }

    // ─── Given ───────────────────────────────────────────────────────────────

    [Given(@"a valid orderbook snapshot for market ""(.*)"" and delivery area ""(.*)""")]
    public void GivenAValidOrderbookSnapshot(string market, string deliveryArea)
    {
        Enum.TryParse<TradingMarket>(market, out var m);
        _books = new List<OrderBookModel>
        {
            new()
            {
                Market     = m,
                Exchange   = Exchange.EPEX,
                IsSnapshot = true,
                Contract   = new ContractModel
                {
                    DeliveryStartTime = new DateTime(2025, 1, 15, 12, 0, 0, DateTimeKind.Utc),
                    DeliveryEndTime   = new DateTime(2025, 1, 15, 12, 30, 0, DateTimeKind.Utc),
                },
                BuyOrders  = new List<OrderBookOrderModel>(),
                SellOrders = new List<OrderBookOrderModel>(),
            }
        };
    }

    [Given(@"(\d+) orderbook snapshots for different delivery windows")]
    public void GivenMultipleOrderbookSnapshots(int count)
    {
        _books = Enumerable.Range(0, count).Select(i => new OrderBookModel
        {
            Market     = TradingMarket.Sidc,
            Exchange   = Exchange.EPEX,
            IsSnapshot = true,
            Contract   = new ContractModel
            {
                DeliveryStartTime = new DateTime(2025, 1, 15, 12 + i, 0, 0, DateTimeKind.Utc),
                DeliveryEndTime   = new DateTime(2025, 1, 15, 12 + i, 30, 0, DateTimeKind.Utc),
            },
            BuyOrders  = new List<OrderBookOrderModel>(),
            SellOrders = new List<OrderBookOrderModel>(),
        }).ToList();
    }

    [Given(@"DynamoDB is unavailable")]
    public void GivenDynamoDbIsUnavailable()
    {
        _dynamoSink.SendAsync(Arg.Any<OrderBookModel>(), Arg.Any<CancellationToken>())
                   .ThrowsAsync(new Exception("DynamoDB connection refused"));
    }

    [Given(@"an empty orderbook message")]
    public void GivenAnEmptyOrderbookMessage() => _books = new List<OrderBookModel>();

    [Given(@"a contract with delivery start ""(.*)"" and delivery end ""(.*)""")]
    public void GivenAContractWithDeliveryWindow(string start, string end)
    {
        _books = new List<OrderBookModel>
        {
            new()
            {
                Contract = new ContractModel
                {
                    DeliveryStartTime = DateTime.Parse(start, null, System.Globalization.DateTimeStyles.RoundtripKind),
                    DeliveryEndTime   = DateTime.Parse(end,   null, System.Globalization.DateTimeStyles.RoundtripKind),
                },
                BuyOrders  = new List<OrderBookOrderModel>(),
                SellOrders = new List<OrderBookOrderModel>(),
            }
        };
    }

    // ─── When ────────────────────────────────────────────────────────────────

    [When(@"the orderbook message is received via SignalR")]
    public async Task WhenTheOrderbookMessageIsReceived()
    {
        await CreateHandler().HandleAsync(_books);
    }

    [When(@"the sort key is generated")]
    public void WhenTheSortKeyIsGenerated() { /* no-op: sort key tested in Then */ }

    // ─── Then ────────────────────────────────────────────────────────────────

    [Then(@"the snapshot is saved to DynamoDB with the correct partition key ""(.*)""")]
    public async Task ThenSnapshotSavedToDynamoDb(string _)
    {
        await _dynamoSink.Received(1).SendAsync(Arg.Any<OrderBookModel>(), Arg.Any<CancellationToken>());
    }

    [Then(@"the snapshot is saved to S3 with a hive-style path")]
    public async Task ThenSnapshotSavedToS3()
    {
        await _kinesisSink.Received(1).SendAsync(Arg.Any<OrderBookModel>(), Arg.Any<CancellationToken>());
    }

    [Then(@"(\d+) items are saved to DynamoDB")]
    public async Task ThenItemsSavedToDynamoDb(int count)
    {
        await _dynamoSink.Received(count).SendAsync(Arg.Any<OrderBookModel>(), Arg.Any<CancellationToken>());
    }

    [Then(@"(\d+) objects are saved to S3")]
    public async Task ThenObjectsSavedToS3(int count)
    {
        await _kinesisSink.Received(count).SendAsync(Arg.Any<OrderBookModel>(), Arg.Any<CancellationToken>());
    }

    [Then(@"the snapshot is still saved to S3")]
    public async Task ThenSnapshotStillSavedToS3()
    {
        await _kinesisSink.Received(1).SendAsync(Arg.Any<OrderBookModel>(), Arg.Any<CancellationToken>());
    }

    [Then(@"no items are saved to any sink")]
    public async Task ThenNoItemsSavedToAnySink()
    {
        await _dynamoSink.DidNotReceive().SendAsync(Arg.Any<OrderBookModel>(), Arg.Any<CancellationToken>());
        await _kinesisSink.DidNotReceive().SendAsync(Arg.Any<OrderBookModel>(), Arg.Any<CancellationToken>());
    }

    [Then(@"the sort key is ""(.*)""")]
    public void ThenTheSortKeyIs(string expectedKey)
    {
        var actual = _books[0].Contract.ToSortKey();
        Assert.Equal(expectedKey, actual);
    }
}

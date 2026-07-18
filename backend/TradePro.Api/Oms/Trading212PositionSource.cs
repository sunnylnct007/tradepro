using TradePro.Api.Providers.Trading212;

namespace TradePro.Api.Oms;

/// <summary>T212 golden-source position accessor for the unified reconciler.
/// Wraps the demo client's positions + order-status (for the real fill price).</summary>
public sealed class Trading212PositionSource : IBrokerPositionSource
{
    private readonly Trading212DemoClient _client;

    public Trading212PositionSource(Trading212DemoClient client) => _client = client;

    public string BrokerLabel => "T212_DEMO";

    public async Task<BrokerPositionsRead> ReadPositionsAsync(CancellationToken ct)
    {
        if (!_client.IsEnabled) return BrokerPositionsRead.Fail("T212 demo disabled");
        try
        {
            var r = await _client.GetPositionsAsync(ct);
            if (r.Error is not null) return BrokerPositionsRead.Fail(r.Error);
            return BrokerPositionsRead.From(
                r.Positions.Select(p => (p.Ticker, p.Quantity)).ToList());
        }
        catch (Exception ex) when (ex is not OperationCanceledException)
        {
            return BrokerPositionsRead.Fail(ex.Message);
        }
    }

    public async Task<decimal?> TryGetFillPriceAsync(string brokerOrderId, CancellationToken ct)
    {
        if (!long.TryParse(brokerOrderId, out var id)) return null;
        var s = await _client.GetOrderStatusAsync(id, ct);
        if (s is null) return null;
        if (s.FillPrice is decimal fp && fp > 0m) return fp;
        if (s.FilledValue is decimal v && s.FilledQuantity is decimal q && q > 0) return v / q;
        return null;
    }
}

using TradePro.Api.Providers.IBKR;

namespace TradePro.Api.Oms;

/// <summary>IBKR golden-source position accessor for the unified reconciler.
/// IBKR never had a fill-confirmation loop, so its clone orders sat unconfirmed
/// forever — this plugs it into the SAME reconcile path as T212/IG.
/// <para/>
/// Fill-price lookup returns null for now (no IBKR execution-history path yet),
/// so IBKR settlements are recorded 0-flagged and surface as "price
/// unconfirmed" drift rather than a fabricated number. When the account is
/// PAUSED the client throws — caught here and reported as a failed read, so the
/// reconciler correctly does NOT treat a paused account as "flat".</summary>
public sealed class IBKRPositionSource : IBrokerPositionSource
{
    private readonly IBKRClient _client;

    public IBKRPositionSource(IBKRClient client) => _client = client;

    public string BrokerLabel => "IBKR_PAPER";

    public async Task<BrokerPositionsRead> ReadPositionsAsync(CancellationToken ct)
    {
        try
        {
            var r = await _client.GetPositionsAsync(ct);
            if (r.Error is not null) return BrokerPositionsRead.Fail(r.Error);
            return BrokerPositionsRead.From(
                r.Positions
                 .Where(p => !string.IsNullOrWhiteSpace(p.Symbol))
                 .Select(p => (p.Symbol!, p.Quantity)).ToList());
        }
        catch (Exception ex) when (ex is not OperationCanceledException)
        {
            // Includes the "IBKR is PAUSED" guard — a paused/unauth account is
            // NOT flat, so this becomes a failed read (surfaced, not settled).
            return BrokerPositionsRead.Fail(ex.Message);
        }
    }

    // No IBKR execution-price history path yet — never fabricate; 0-flagged.
    public Task<decimal?> TryGetFillPriceAsync(string brokerOrderId, CancellationToken ct)
        => Task.FromResult<decimal?>(null);
}

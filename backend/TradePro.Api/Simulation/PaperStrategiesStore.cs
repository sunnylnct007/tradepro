using System.Text.Json;

namespace TradePro.Api.Simulation;

/// <summary>
/// Holds the most-recent paper-trading strategy catalog the Mac
/// pushed via /ingest/paper-strategies. The frontend Paper page reads
/// this so users can see "what strategies are available" without the
/// API box needing a Python install.
///
/// One-slot store — pushing a new catalog overwrites the prior. That
/// matches the rate at which the catalog actually changes (rare,
/// roughly once per deploy when a new @register_strategy class lands).
/// </summary>
public interface IPaperStrategiesStore
{
    void Put(JsonElement payload);
    JsonElement? Get();
}

public sealed class InMemoryPaperStrategiesStore : IPaperStrategiesStore
{
    private JsonElement? _current;
    private readonly object _gate = new();

    public void Put(JsonElement payload)
    {
        lock (_gate)
        {
            // Clone so the framework can dispose the request-backing JsonDocument.
            _current = payload.Clone();
        }
    }

    public JsonElement? Get()
    {
        lock (_gate) return _current;
    }
}

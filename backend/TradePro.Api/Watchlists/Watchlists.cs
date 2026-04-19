namespace TradePro.Api.Watchlists;

public record WatchlistItem(string Symbol, string Label, string Kind);

public record WatchlistDto(string Name, string Currency, string Region, IReadOnlyList<WatchlistItem> Items);

public interface IWatchlistStore
{
    WatchlistDto? Get(string name);
    IEnumerable<string> Keys { get; }
}

/// In-memory watchlists. Move to config / DB in Phase 1.
public sealed class InMemoryWatchlistStore : IWatchlistStore
{
    private readonly Dictionary<string, WatchlistDto> _byKey = new(StringComparer.OrdinalIgnoreCase)
    {
        ["uk"] = new WatchlistDto(
            Name: "UK — Large Caps & Index",
            Currency: "GBP",
            Region: "UK",
            Items: new List<WatchlistItem>
            {
                new("^FTSE",  "FTSE 100 Index",        "index"),
                new("^FTMC",  "FTSE 250 Index",        "index"),
                new("BARC.L", "Barclays",              "equity"),
                new("LLOY.L", "Lloyds Banking Group",  "equity"),
                new("HSBA.L", "HSBC Holdings",         "equity"),
                new("SHEL.L", "Shell",                 "equity"),
                new("AZN.L",  "AstraZeneca",           "equity"),
                new("ULVR.L", "Unilever",              "equity"),
                new("GSK.L",  "GSK",                   "equity"),
                new("BP.L",   "BP",                    "equity"),
            }),
    };

    public WatchlistDto? Get(string name) => _byKey.TryGetValue(name, out var w) ? w : null;
    public IEnumerable<string> Keys => _byKey.Keys;
}

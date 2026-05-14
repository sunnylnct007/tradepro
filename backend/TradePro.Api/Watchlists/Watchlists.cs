namespace TradePro.Api.Watchlists;

public record WatchlistItem(string Symbol, string Label, string Kind);

public record WatchlistDto(string Name, string Currency, string Region, IReadOnlyList<WatchlistItem> Items);

public interface IWatchlistStore
{
    WatchlistDto? Get(string name);
    IEnumerable<string> Keys { get; }
}

/// In-memory watchlists. Mirrors the Python-side WATCHLISTS in
/// tradepro_strategies/watchlists.py so the Scanner page sees the
/// same universes the worker pushes compare data for. Phase 1 will
/// replace this with a synced store (worker pushes universe membership
/// alongside compare data), eliminating the duplicate definition.
public sealed class InMemoryWatchlistStore : IWatchlistStore
{
    private static WatchlistItem Idx(string sym, string label)    => new(sym, label, "index");
    private static WatchlistItem Eq(string sym, string label)     => new(sym, label, "equity");
    private static WatchlistItem Etf(string sym, string label)    => new(sym, label, "etf");

    private readonly Dictionary<string, WatchlistDto> _byKey = new(StringComparer.OrdinalIgnoreCase)
    {
        ["uk"] = new WatchlistDto(
            Name: "UK - Large Caps & Index",
            Currency: "GBP",
            Region: "UK",
            Items: new List<WatchlistItem>
            {
                Idx("^FTSE",  "FTSE 100 Index"),
                Idx("^FTMC",  "FTSE 250 Index"),
                Eq("BARC.L", "Barclays"),
                Eq("LLOY.L", "Lloyds Banking Group"),
                Eq("HSBA.L", "HSBC Holdings"),
                Eq("SHEL.L", "Shell"),
                Eq("AZN.L",  "AstraZeneca"),
                Eq("ULVR.L", "Unilever"),
                Eq("GSK.L",  "GSK"),
                Eq("BP.L",   "BP"),
            }),
        ["uk_ftse100_sample"] = new WatchlistDto(
            Name: "UK - FTSE 100 sample",
            Currency: "GBP",
            Region: "UK",
            Items: new List<WatchlistItem>
            {
                Idx("^FTSE", "FTSE 100 Index"),
                Eq("BARC.L", "Barclays"),
                Eq("LLOY.L", "Lloyds Banking Group"),
                Eq("NWG.L",  "NatWest Group"),
                Eq("HSBA.L", "HSBC Holdings"),
                Eq("STAN.L", "Standard Chartered"),
                Eq("SHEL.L", "Shell"),
                Eq("BP.L",   "BP"),
                Eq("RIO.L",  "Rio Tinto"),
                Eq("GLEN.L", "Glencore"),
                Eq("AAL.L",  "Anglo American"),
                Eq("AZN.L",  "AstraZeneca"),
                Eq("GSK.L",  "GSK"),
                Eq("HLMA.L", "Halma"),
                Eq("ULVR.L", "Unilever"),
                Eq("DGE.L",  "Diageo"),
                Eq("RKT.L",  "Reckitt"),
                Eq("TSCO.L", "Tesco"),
                Eq("SBRY.L", "Sainsbury's"),
                Eq("VOD.L",  "Vodafone"),
                Eq("BT-A.L", "BT Group"),
            }),
        ["us_megacap_sample"] = new WatchlistDto(
            Name: "US - Mega-cap sample",
            Currency: "USD",
            Region: "US",
            Items: new List<WatchlistItem>
            {
                Eq("AAPL",  "Apple"),
                Eq("MSFT",  "Microsoft"),
                Eq("GOOGL", "Alphabet"),
                Eq("AMZN",  "Amazon"),
                Eq("META",  "Meta Platforms"),
                Eq("NVDA",  "Nvidia"),
                Eq("TSLA",  "Tesla"),
            }),
        ["etf_uk_core"] = new WatchlistDto(
            Name: "ETF UK - Core (LSE UCITS)",
            Currency: "GBP",
            Region: "UK",
            Items: new List<WatchlistItem>
            {
                Etf("VWRP.L", "Vanguard FTSE All-World (acc)"),
                Etf("VWRL.L", "Vanguard FTSE All-World (dist)"),
                Etf("VUSA.L", "Vanguard S&P 500"),
                Etf("CSPX.L", "iShares Core S&P 500 (acc)"),
                Etf("SWDA.L", "iShares Core MSCI World (acc)"),
                Etf("HMWO.L", "HSBC MSCI World"),
                Etf("SWLD.L", "SPDR MSCI World"),
                Etf("VUKE.L", "Vanguard FTSE 100"),
                Etf("VMID.L", "Vanguard FTSE 250"),
                Etf("ISF.L",  "iShares Core FTSE 100"),
                Etf("IUKD.L", "iShares UK Dividend"),
                Etf("IGLT.L", "iShares Core UK Gilts"),
                Etf("VEUR.L", "Vanguard FTSE Developed Europe ex-UK"),
                Etf("VJPN.L", "Vanguard FTSE Japan"),
                Etf("VFEM.L", "Vanguard FTSE Emerging Markets"),
                Etf("EIMI.L", "iShares Core MSCI EM IMI"),
                Etf("VAGP.L", "Vanguard Global Aggregate Bond (GBP-hedged)"),
                Etf("IGLN.L", "iShares Physical Gold"),
                Etf("INRG.L", "iShares Global Clean Energy"),
            }),
        ["etf_us_core"] = new WatchlistDto(
            Name: "ETF US - Core",
            Currency: "USD",
            Region: "US",
            Items: new List<WatchlistItem>
            {
                Etf("VOO",  "Vanguard S&P 500"),
                Etf("IVV",  "iShares Core S&P 500"),
                Etf("VTI",  "Vanguard Total US Stock Market"),
                Etf("VXUS", "Vanguard Total International Stock ex-US"),
                Etf("QQQ",  "Invesco Nasdaq 100"),
                Etf("IWM",  "iShares Russell 2000 (small-cap)"),
                Etf("SCHD", "Schwab US Dividend Equity"),
                Etf("EFA",  "iShares MSCI EAFE (developed ex-US)"),
                Etf("EEM",  "iShares MSCI Emerging Markets"),
                Etf("AGG",  "iShares Core US Aggregate Bond"),
                Etf("TLT",  "iShares 20+ Year Treasury"),
                Etf("GLD",  "SPDR Gold"),
            }),
        ["etf_us_sector"] = new WatchlistDto(
            Name: "ETF US - Sector SPDRs",
            Currency: "USD",
            Region: "US",
            Items: new List<WatchlistItem>
            {
                Etf("XLK", "Technology"),
                Etf("XLV", "Health Care"),
                Etf("XLF", "Financials"),
                Etf("XLE", "Energy"),
                Etf("XLY", "Consumer Discretionary"),
                Etf("XLP", "Consumer Staples"),
                Etf("XLI", "Industrials"),
                Etf("XLU", "Utilities"),
                Etf("XLB", "Materials"),
                Etf("XLRE", "Real Estate"),
                Etf("XLC", "Communication Services"),
            }),
        ["etf_factor"] = new WatchlistDto(
            Name: "ETF US - Factor tilts",
            Currency: "USD",
            Region: "US",
            Items: new List<WatchlistItem>
            {
                Etf("MTUM", "iShares MSCI USA Momentum"),
                Etf("VLUE", "iShares MSCI USA Value"),
                Etf("QUAL", "iShares MSCI USA Quality"),
                Etf("USMV", "iShares MSCI USA Min Vol"),
                Etf("SIZE", "iShares MSCI USA Size"),
            }),
    };

    public WatchlistDto? Get(string name) => _byKey.TryGetValue(name, out var w) ? w : null;
    public IEnumerable<string> Keys => _byKey.Keys;
}

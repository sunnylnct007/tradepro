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
                Eq("AMD",   "AMD"),
                Eq("AVGO",  "Broadcom"),
                Eq("MU",    "Micron Technology"),
                Eq("CRM",   "Salesforce"),
                Eq("LLY",   "Eli Lilly"),
            }),
        ["us_sp100_sample"] = new WatchlistDto(
            Name: "US - S&P 100 sample",
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
                Eq("AVGO",  "Broadcom"),
                Eq("ORCL",  "Oracle"),
                Eq("ADBE",  "Adobe"),
                Eq("CRM",   "Salesforce"),
                Eq("NFLX",  "Netflix"),
                Eq("AMD",   "AMD"),
                Eq("INTC",  "Intel"),
                Eq("CSCO",  "Cisco"),
                Eq("IBM",   "IBM"),
                Eq("BRK-B", "Berkshire Hathaway"),
                Eq("JPM",   "JPMorgan Chase"),
                Eq("BAC",   "Bank of America"),
                Eq("GS",    "Goldman Sachs"),
                Eq("MS",    "Morgan Stanley"),
                Eq("V",     "Visa"),
                Eq("MA",    "Mastercard"),
                Eq("AXP",   "American Express"),
                Eq("LLY",   "Eli Lilly"),
                Eq("JNJ",   "Johnson & Johnson"),
                Eq("UNH",   "UnitedHealth"),
                Eq("PFE",   "Pfizer"),
                Eq("ABBV",  "AbbVie"),
                Eq("MRK",   "Merck"),
                Eq("WMT",   "Walmart"),
                Eq("COST",  "Costco"),
                Eq("KO",    "Coca-Cola"),
                Eq("PEP",   "PepsiCo"),
                Eq("MCD",   "McDonald's"),
                Eq("NKE",   "Nike"),
                Eq("DIS",   "Walt Disney"),
                Eq("PG",    "Procter & Gamble"),
                Eq("BA",    "Boeing"),
                Eq("CAT",   "Caterpillar"),
                Eq("XOM",   "ExxonMobil"),
            }),
        ["us_semis"] = new WatchlistDto(
            Name: "US - Semiconductors",
            Currency: "USD",
            Region: "US",
            Items: new List<WatchlistItem>
            {
                Eq("NVDA",  "Nvidia"),
                Eq("AMD",   "AMD"),
                Eq("AVGO",  "Broadcom"),
                Eq("MU",    "Micron Technology"),
                Eq("INTC",  "Intel"),
                Eq("TSM",   "Taiwan Semiconductor"),
                Eq("ASML",  "ASML Holding"),
                Eq("AMAT",  "Applied Materials"),
                Eq("LRCX",  "Lam Research"),
                Eq("KLAC",  "KLA"),
                Eq("TXN",   "Texas Instruments"),
                Eq("ADI",   "Analog Devices"),
                Eq("QCOM",  "Qualcomm"),
                Etf("SOXX", "iShares Semiconductor ETF"),
            }),
        ["us_growth_tech"] = new WatchlistDto(
            Name: "US - Growth / Cloud / AI tech",
            Currency: "USD",
            Region: "US",
            Items: new List<WatchlistItem>
            {
                Eq("PLTR", "Palantir"),
                Eq("SNOW", "Snowflake"),
                Eq("ANET", "Arista Networks"),
                Eq("NOW",  "ServiceNow"),
                Eq("CRM",  "Salesforce"),
                Eq("ORCL", "Oracle"),
                Eq("SHOP", "Shopify"),
                Eq("MELI", "MercadoLibre"),
                Eq("UBER", "Uber"),
                Eq("DASH", "DoorDash"),
                Eq("NET",  "Cloudflare"),
                Eq("CRWD", "CrowdStrike"),
                Eq("DDOG", "Datadog"),
                Eq("MDB",  "MongoDB"),
            }),
        ["asia_majors"] = new WatchlistDto(
            Name: "Asia / Pacific majors",
            Currency: "USD",  // mixed JPY/HKD/AUD — UI normalises
            Region: "APAC",
            Items: new List<WatchlistItem>
            {
                Idx("^N225",    "Nikkei 225"),
                Idx("^HSI",     "Hang Seng"),
                Eq("7203.T",    "Toyota"),
                Eq("6758.T",    "Sony"),
                Eq("9984.T",    "SoftBank"),
                Eq("8306.T",    "Mitsubishi UFJ Financial"),
                Eq("6861.T",    "Keyence"),
                Eq("9983.T",    "Fast Retailing (Uniqlo)"),
                Eq("0700.HK",   "Tencent"),
                Eq("9988.HK",   "Alibaba HK"),
                Eq("3690.HK",   "Meituan"),
                Eq("1810.HK",   "Xiaomi"),
                Eq("BHP.AX",    "BHP"),
                Eq("CBA.AX",    "Commonwealth Bank"),
            }),
        ["europe_majors"] = new WatchlistDto(
            Name: "Europe (ex-UK) majors",
            Currency: "EUR",  // mixed EUR/CHF — UI normalises
            Region: "EU",
            Items: new List<WatchlistItem>
            {
                Idx("^GDAXI",   "DAX"),
                Idx("^STOXX",   "Stoxx 600"),
                Eq("MC.PA",     "LVMH"),
                Eq("OR.PA",     "L'Oréal"),
                Eq("AIR.PA",    "Airbus"),
                Eq("SAN.PA",    "Sanofi"),
                Eq("TTE.PA",    "TotalEnergies"),
                Eq("ASML.AS",   "ASML"),
                Eq("INGA.AS",   "ING Groep"),
                Eq("SAP.DE",    "SAP"),
                Eq("SIE.DE",    "Siemens"),
                Eq("ALV.DE",    "Allianz"),
                Eq("ITX.MC",    "Inditex"),
                Eq("NESN.SW",   "Nestlé"),
                Eq("ROG.SW",    "Roche"),
                Eq("NOVN.SW",   "Novartis"),
            }),
        ["crypto_majors"] = new WatchlistDto(
            Name: "Crypto majors",
            Currency: "USD",
            Region: "Crypto",
            Items: new List<WatchlistItem>
            {
                Eq("BTC-USD",   "Bitcoin"),
                Eq("ETH-USD",   "Ethereum"),
                Eq("SOL-USD",   "Solana"),
                Eq("BNB-USD",   "BNB"),
                Eq("XRP-USD",   "XRP"),
                Eq("ADA-USD",   "Cardano"),
                Eq("AVAX-USD",  "Avalanche"),
                Eq("DOT-USD",   "Polkadot"),
                Eq("LINK-USD",  "Chainlink"),
                Eq("MATIC-USD", "Polygon"),
            }),
        ["commodities_broad"] = new WatchlistDto(
            Name: "Commodities (broad)",
            Currency: "USD",
            Region: "Commodities",
            Items: new List<WatchlistItem>
            {
                Eq("NG=F",  "Natural Gas (Henry Hub)"),
                Eq("BZ=F",  "Brent Crude"),
                Eq("CL=F",  "WTI Crude"),
                Eq("GC=F",  "Gold"),
                Eq("SI=F",  "Silver"),
                Eq("HG=F",  "Copper"),
                Eq("PL=F",  "Platinum"),
                Eq("PA=F",  "Palladium"),
                Eq("ZC=F",  "Corn"),
                Eq("ZW=F",  "Wheat"),
                Eq("ZS=F",  "Soybeans"),
                Eq("KC=F",  "Coffee"),
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

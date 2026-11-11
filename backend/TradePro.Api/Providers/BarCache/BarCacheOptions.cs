namespace TradePro.Api.Providers.BarCache;

/// <summary>
/// Where the golden bar store is. See docs/DATA_ARCHITECTURE.md.
///
/// The store is the parquet tree the Python harvester writes:
/// <c>{Prefix}/{assetClass}/{SYMBOL}/{resolution}/{yyyy-MM}.parquet</c>, one
/// partition per month, mirrored to S3. The API READS it and never writes.
///
/// Bound from the <c>BarCache</c> section. With <see cref="Bucket"/> set the
/// API reads S3 (the deployed shape, EC2 instance role, GetObject only); with
/// <see cref="LocalDir"/> set it reads a directory (the Mac, and tests).
/// Neither set = provider registered but every read answers empty with
/// <c>Provider = "bar_cache"</c>, so a misconfiguration is visible, not a
/// silent fallback to a live feed.
/// </summary>
public sealed class BarCacheOptions
{
    public const string SectionName = "BarCache";

    /// <summary>S3 bucket holding the mirror. Empty = not using S3.</summary>
    public string? Bucket { get; set; }

    /// <summary>Key prefix inside the bucket. The harvester's default is <c>bar_cache</c>.</summary>
    public string Prefix { get; set; } = "bar_cache";

    /// <summary>AWS region of the bucket. Empty = SDK default (AWS_DEFAULT_REGION).</summary>
    public string? Region { get; set; }

    /// <summary>Local root of the store (the Mac's <c>~/.tradepro/bar_cache</c>, or a test dir). Empty = not using disk.</summary>
    public string? LocalDir { get; set; }

    /// <summary>Asset class folder for symbols with no explicit mapping. The US equity/ETF tree.</summary>
    public string DefaultAssetClass { get; set; } = "us_etf";

    /// <summary>
    /// How long a fetched partition is reused before being re-read. The CURRENT
    /// month changes every harvest run (every 30 min intraday); past months do
    /// not change except on a re-source. Seconds.
    /// </summary>
    public int CurrentMonthTtlSeconds { get; set; } = 60;
    public int PastMonthTtlSeconds { get; set; } = 6 * 3600;
}

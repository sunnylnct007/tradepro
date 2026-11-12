using System.Collections.Concurrent;
using Amazon;
using Amazon.S3;
using Amazon.S3.Model;
using Microsoft.Extensions.Options;

namespace TradePro.Api.Providers.BarCache;

/// <summary>
/// Read-only access to one partition file of the golden bar store, by its
/// relative path (<c>{assetClass}/{SYMBOL}/{resolution}/{yyyy-MM}.parquet</c>).
/// Two implementations: a directory (Mac, tests) and S3 (deployed). The
/// provider never needs to LIST — partitions are addressed by month, so a
/// missing month is simply a null read. That keeps the IAM footprint to
/// GetObject on the prefix.
/// </summary>
public interface IBarCacheBlobs
{
    /// <summary>Whole file bytes, or null when the partition does not exist.</summary>
    Task<byte[]?> ReadAsync(string relativePath, CancellationToken ct);

    /// <summary>Where this instance reads from, for the series provenance and /status.</summary>
    string Location { get; }
}

public sealed class LocalDirectoryBarCacheBlobs : IBarCacheBlobs
{
    private readonly string _root;
    public LocalDirectoryBarCacheBlobs(string root) => _root = root;
    public string Location => _root;

    public async Task<byte[]?> ReadAsync(string relativePath, CancellationToken ct)
    {
        var path = Path.Combine(_root, relativePath.Replace('/', Path.DirectorySeparatorChar));
        if (!File.Exists(path)) return null;
        return await File.ReadAllBytesAsync(path, ct);
    }
}

public sealed class S3BarCacheBlobs : IBarCacheBlobs, IDisposable
{
    private readonly IAmazonS3 _s3;
    private readonly string _bucket;
    private readonly string _prefix;

    public S3BarCacheBlobs(string bucket, string prefix, string? region, string? credentialSource = "instance-profile")
    {
        _bucket = bucket;
        _prefix = prefix.Trim('/');
        var endpoint = string.IsNullOrWhiteSpace(region) ? null : RegionEndpoint.GetBySystemName(region);
        var useRole = !string.Equals(credentialSource, "default-chain", StringComparison.OrdinalIgnoreCase);
        _s3 = (useRole, endpoint) switch
        {
            // The instance role is the one identity allowed to read the store;
            // never let static SES keys in the environment shadow it.
            (true, null) => new AmazonS3Client(new Amazon.Runtime.InstanceProfileAWSCredentials()),
            (true, _) => new AmazonS3Client(new Amazon.Runtime.InstanceProfileAWSCredentials(), endpoint),
            (false, null) => new AmazonS3Client(),
            (false, _) => new AmazonS3Client(endpoint),
        };
    }

    public string Location => $"s3://{_bucket}/{_prefix}";

    public async Task<byte[]?> ReadAsync(string relativePath, CancellationToken ct)
    {
        var key = string.IsNullOrEmpty(_prefix) ? relativePath : $"{_prefix}/{relativePath}";
        try
        {
            using var resp = await _s3.GetObjectAsync(new GetObjectRequest { BucketName = _bucket, Key = key }, ct);
            using var ms = new MemoryStream();
            await resp.ResponseStream.CopyToAsync(ms, ct);
            return ms.ToArray();
        }
        catch (AmazonS3Exception ex) when (ex.StatusCode == System.Net.HttpStatusCode.NotFound)
        {
            return null;
        }
    }

    public void Dispose() => _s3.Dispose();
}

/// <summary>
/// Answers every read with null and says why. Registered when neither Bucket
/// nor LocalDir is configured, so the provider still exists (the registry can
/// resolve "bar_cache") and a chart shows an EMPTY labelled series rather than
/// quietly getting Yahoo.
/// </summary>
public sealed class UnconfiguredBarCacheBlobs : IBarCacheBlobs
{
    public string Location => "UNCONFIGURED — set BarCache:Bucket (S3) or BarCache:LocalDir";
    public Task<byte[]?> ReadAsync(string relativePath, CancellationToken ct) => Task.FromResult<byte[]?>(null);
}

/// <summary>
/// Caches partition bytes for a TTL. A chart load asks for the same few month
/// files repeatedly; the harvest rewrites the current month at most every 30
/// minutes. Past months practically never change, so they live longer.
/// Misses (null) are cached too, briefly, so a symbol with no 1m tree does not
/// hammer S3 on every render.
/// </summary>
public sealed class CachingBarCacheBlobs : IBarCacheBlobs
{
    private sealed record Entry(byte[]? Bytes, DateTime ExpiresUtc);
    private readonly IBarCacheBlobs _inner;
    private readonly TimeSpan _currentTtl, _pastTtl;
    private readonly ConcurrentDictionary<string, Entry> _cache = new();
    private readonly Func<DateTime> _now;

    public CachingBarCacheBlobs(IBarCacheBlobs inner, IOptions<BarCacheOptions> opts, Func<DateTime>? now = null)
    {
        _inner = inner;
        _currentTtl = TimeSpan.FromSeconds(Math.Max(1, opts.Value.CurrentMonthTtlSeconds));
        _pastTtl = TimeSpan.FromSeconds(Math.Max(1, opts.Value.PastMonthTtlSeconds));
        _now = now ?? (() => DateTime.UtcNow);
    }

    public string Location => _inner.Location;

    public async Task<byte[]?> ReadAsync(string relativePath, CancellationToken ct)
    {
        var now = _now();
        if (_cache.TryGetValue(relativePath, out var hit) && hit.ExpiresUtc > now)
            return hit.Bytes;
        var bytes = await _inner.ReadAsync(relativePath, ct);
        var ttl = relativePath.EndsWith($"{now:yyyy-MM}.parquet", StringComparison.Ordinal) ? _currentTtl : _pastTtl;
        if (bytes is null) ttl = _currentTtl;          // a miss may be filled by the next harvest
        _cache[relativePath] = new Entry(bytes, now + ttl);
        return bytes;
    }
}

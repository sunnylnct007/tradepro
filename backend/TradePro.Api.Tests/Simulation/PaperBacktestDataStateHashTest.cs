using System.Text.Json;
using Dapper;
using TradePro.Api.Data.Stores;
using TradePro.Api.Simulation;
using TradePro.Api.Tests.Infrastructure;
using Xunit;

namespace TradePro.Api.Tests.Simulation;

/// <summary>
/// Phase D-2 coverage. Asserts that the data_state_hash plumbing
/// works end-to-end against ephemeral Postgres:
///
///   * The migration 034 column accepts NULL + arbitrary hashes.
///   * Put() reads the hash from the payload (both top-level shape
///     and nested under data_state.hash).
///   * Get() / List() / ListByDataStateHash() round-trip the value.
///   * The sentinel "EMPTY_DATA_STATE" is excluded from the
///     reproducibility-match lookup.
///   * The InMemoryPaperBacktestStore implements the same contract
///     (sharing the same interface is the design intent).
///
/// We exercise both stores so a Postgres-vs-InMemory drift fails
/// here, not after a deploy.
/// </summary>
[Collection("postgres")]
public sealed class PaperBacktestDataStateHashTest
{
    private readonly PostgresFixture _fx;

    public PaperBacktestDataStateHashTest(PostgresFixture fx)
    {
        _fx = fx;
        using var conn = _fx.Db.OpenConnection();
        conn.Execute("DELETE FROM paper_backtests;");
    }

    private PostgresPaperBacktestStore NewPostgresStore()
        => new(_fx.Db);

    private static JsonElement Payload(
        string reportId, string symbol = "SPY",
        string? dataStateHash = null,
        bool nest = false)
    {
        var obj = new Dictionary<string, object?>
        {
            ["report_id"] = reportId,
            ["kind"] = "backtest",
            ["symbol"] = symbol,
            ["start"] = "2024-01-02",
            ["end"]   = "2024-12-31",
            ["entries"] = new[] { new { } },
        };
        if (dataStateHash is not null)
        {
            if (nest)
                obj["data_state"] = new { hash = dataStateHash };
            else
                obj["data_state_hash"] = dataStateHash;
        }
        var json = JsonSerializer.Serialize(obj);
        using var doc = JsonDocument.Parse(json);
        return doc.RootElement.Clone();
    }

    [Fact]
    public void Postgres_Put_reads_top_level_data_state_hash()
    {
        var store = NewPostgresStore();
        var hash = "28ef746f55608f8fa8753f906337db1f610823412d0a52caacffa7cdf3f1c47f";
        var envelope = store.Put(Payload("r-top", dataStateHash: hash));
        Assert.Equal(hash, envelope.DataStateHash);

        var fetched = store.Get("r-top");
        Assert.NotNull(fetched);
        Assert.Equal(hash, fetched!.DataStateHash);
    }

    [Fact]
    public void Postgres_Put_reads_nested_data_state_hash()
    {
        var store = NewPostgresStore();
        var hash = "9215e3ff479572e999e741aa7283ba7506d1f0692648015dbb359114bc7e12c1";
        var envelope = store.Put(Payload("r-nested", dataStateHash: hash, nest: true));
        Assert.Equal(hash, envelope.DataStateHash);
    }

    [Fact]
    public void Postgres_Put_tolerates_missing_data_state_hash()
    {
        var store = NewPostgresStore();
        var envelope = store.Put(Payload("r-no-hash"));
        Assert.Null(envelope.DataStateHash);

        var fetched = store.Get("r-no-hash");
        Assert.NotNull(fetched);
        Assert.Null(fetched!.DataStateHash);
    }

    [Fact]
    public void Postgres_List_carries_data_state_hash()
    {
        var store = NewPostgresStore();
        var hash = "abc" + new string('0', 61);
        store.Put(Payload("r-list-1", dataStateHash: hash));
        store.Put(Payload("r-list-2"));   // no hash

        var rows = store.List();
        var first = rows.Single(r => r.ReportId == "r-list-1");
        var second = rows.Single(r => r.ReportId == "r-list-2");
        Assert.Equal(hash, first.DataStateHash);
        Assert.Null(second.DataStateHash);
    }

    [Fact]
    public void Postgres_ListByDataStateHash_returns_matching_reports()
    {
        var store = NewPostgresStore();
        var sharedHash = "1234567890abcdef" + new string('a', 48);
        store.Put(Payload("r-share-a", "SPY", dataStateHash: sharedHash));
        store.Put(Payload("r-share-b", "QQQ", dataStateHash: sharedHash));
        store.Put(Payload("r-other", "SPY", dataStateHash: "fed" + new string('c', 61)));

        var matches = store.ListByDataStateHash(sharedHash);
        Assert.Equal(2, matches.Count);
        Assert.All(matches, m => Assert.Equal(sharedHash, m.DataStateHash));
    }

    [Fact]
    public void Postgres_ListByDataStateHash_excludes_sentinel()
    {
        var store = NewPostgresStore();
        store.Put(Payload("r-empty", dataStateHash: "EMPTY_DATA_STATE"));
        var matches = store.ListByDataStateHash("EMPTY_DATA_STATE");
        Assert.Empty(matches);
    }

    [Fact]
    public void Postgres_ListByDataStateHash_excludes_blank()
    {
        var store = NewPostgresStore();
        store.Put(Payload("r-blank", dataStateHash: ""));
        var matches = store.ListByDataStateHash("");
        Assert.Empty(matches);
    }

    [Fact]
    public void InMemory_Put_and_lookup_round_trip_hash()
    {
        var store = new InMemoryPaperBacktestStore();
        var hash = "deadbeef" + new string('1', 56);
        store.Put(Payload("r-mem-1", "SPY", dataStateHash: hash));
        store.Put(Payload("r-mem-2", "QQQ", dataStateHash: hash));
        store.Put(Payload("r-mem-3", "IWM"));

        var rows = store.List();
        Assert.Contains(rows, r => r.ReportId == "r-mem-1" && r.DataStateHash == hash);
        Assert.Contains(rows, r => r.ReportId == "r-mem-3" && r.DataStateHash is null);

        var matches = store.ListByDataStateHash(hash);
        Assert.Equal(2, matches.Count);
        Assert.All(matches, m => Assert.Equal(hash, m.DataStateHash));
    }

    [Fact]
    public void InMemory_Put_reads_nested_data_state_hash()
    {
        var store = new InMemoryPaperBacktestStore();
        var hash = "cafebabe" + new string('2', 56);
        var envelope = store.Put(Payload("r-mem-nested", dataStateHash: hash, nest: true));
        Assert.Equal(hash, envelope.DataStateHash);
    }
}

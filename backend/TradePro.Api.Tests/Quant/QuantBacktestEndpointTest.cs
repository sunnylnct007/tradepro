using System.Text.Json;
using Dapper;
using TradePro.Api.Data.Stores;
using TradePro.Api.Endpoints;
using TradePro.Api.Simulation;
using TradePro.Api.Tests.Infrastructure;
using Xunit;

namespace TradePro.Api.Tests.Quant;

/// <summary>
/// Integration tests for the QuantEndpoints contract — backed by a
/// real Postgres via the shared PostgresFixture so a schema regression
/// in the session_requests migration fails here, not in production.
///
/// Tests exercise the store directly (same pattern as OmsServiceTest)
/// because the endpoints are pure plumbing on top of
/// PostgresSessionRequestsStore. A future end-to-end test could spin
/// up a WebApplicationFactory; today's surface is small enough that
/// store-level coverage + the worker-side behave test catches
/// regressions before they ship.
/// </summary>
[Collection("postgres")]
public sealed class QuantBacktestEndpointTest
{
    private readonly PostgresFixture _fx;

    public QuantBacktestEndpointTest(PostgresFixture fx)
    {
        _fx = fx;
        // Wipe the session_requests rows so list/claim assertions are
        // deterministic across runs. Other tables stay intact so the
        // OMS / paper / intraday tests don't trip on each other.
        using var conn = _fx.Db.OpenConnection();
        conn.Execute("DELETE FROM session_requests;");
    }

    private PostgresSessionRequestsStore NewStore() => new(_fx.Db);

    private static JsonElement SamplePayload(string strategy = "ichimoku_equity") =>
        JsonDocument.Parse("""
            {
              "kind": "backtest",
              "strategy": "STRAT_NAME",
              "symbols": ["AAPL", "MSFT"],
              "start": "2020-01-01",
              "end": "2024-12-31",
              "initial_capital": 100000.0,
              "monte_carlo": {"n_sims": 100, "years": 3, "seed": 7},
              "label": "test-backtest"
            }
            """.Replace("STRAT_NAME", strategy)).RootElement.Clone();

    [Fact]
    public void Put_backtest_creates_pending_row_with_correct_kind()
    {
        var store = NewStore();
        var row = store.Put(QuantEndpoints.Kind, SamplePayload());

        Assert.Equal("backtest", row.Kind);
        Assert.Equal(SessionRequestState.Pending, row.State);
        Assert.False(string.IsNullOrEmpty(row.RequestId));
        Assert.Null(row.ClaimedAtUtc);
        Assert.Null(row.CompletedAtUtc);

        // Round-trip the params so a JSONB encoding bug surfaces here.
        Assert.Equal(JsonValueKind.Object, row.Params.ValueKind);
        Assert.Equal("ichimoku_equity",
            row.Params.GetProperty("strategy").GetString());
        var syms = row.Params.GetProperty("symbols");
        Assert.Equal(2, syms.GetArrayLength());
    }

    [Fact]
    public void List_with_kind_backtest_filters_paper_sessions_out()
    {
        var store = NewStore();
        store.Put(QuantEndpoints.Kind, SamplePayload("strat_a"));
        store.Put(QuantEndpoints.Kind, SamplePayload("strat_b"));
        store.Put("paper_session", SamplePayload("paper_only"));

        var backtests = store.List(QuantEndpoints.Kind, limit: 50);

        Assert.Equal(2, backtests.Count);
        Assert.All(backtests, r => Assert.Equal("backtest", r.Kind));
    }

    [Fact]
    public void Claim_backtest_returns_only_backtest_rows_and_skips_paper_sessions()
    {
        var store = NewStore();
        // Mix kinds; Claim("backtest", ...) must skip the paper row even
        // though it's also Pending. Without the WHERE kind = filter the
        // daemon would pick up paper requests via the wrong code path.
        store.Put("paper_session", SamplePayload("paper_first"));
        var backtest = store.Put(QuantEndpoints.Kind, SamplePayload("backtest_second"));

        var claimed = store.Claim(QuantEndpoints.Kind, "test-worker");

        Assert.NotNull(claimed);
        Assert.Equal(backtest.RequestId, claimed!.RequestId);
        Assert.Equal(SessionRequestState.Claimed, claimed.State);
        Assert.Equal("test-worker", claimed.ClaimedBy);
        Assert.NotNull(claimed.ClaimedAtUtc);
    }

    [Fact]
    public void Claim_returns_null_when_no_pending_backtests()
    {
        var store = NewStore();
        // Put a paper row to confirm we don't accidentally pick it up.
        store.Put("paper_session", SamplePayload());

        var claimed = store.Claim(QuantEndpoints.Kind, "test-worker");

        Assert.Null(claimed);
    }

    [Fact]
    public void MarkCompleted_stores_result_summary_and_transitions_state()
    {
        var store = NewStore();
        var row = store.Put(QuantEndpoints.Kind, SamplePayload());
        store.Claim(QuantEndpoints.Kind, "test-worker");

        var summary = JsonDocument.Parse("""
            {
              "kind": "backtest",
              "summary": {"final_equity": 123456.78, "label": "test"},
              "charts": {"backtest_4panel": {"data": []}, "monte_carlo_fan": {"data": []}}
            }
            """).RootElement.Clone();

        var done = store.MarkCompleted(row.RequestId, summary);

        Assert.NotNull(done);
        Assert.Equal(SessionRequestState.Completed, done!.State);
        Assert.NotNull(done.CompletedAtUtc);
        Assert.NotNull(done.ResultSummary);
        Assert.Equal("backtest",
            done.ResultSummary!.Value.GetProperty("kind").GetString());
        // Charts dict is preserved through the round-trip — the
        // Session Detail page reads these from result_summary.charts.
        var charts = done.ResultSummary.Value.GetProperty("charts");
        Assert.True(charts.TryGetProperty("backtest_4panel", out _));
        Assert.True(charts.TryGetProperty("monte_carlo_fan", out _));
    }

    [Fact]
    public void MarkFailed_records_error_and_clears_result_summary_path()
    {
        var store = NewStore();
        var row = store.Put(QuantEndpoints.Kind, SamplePayload());

        var failed = store.MarkFailed(row.RequestId, "yfinance returned no bars");

        Assert.NotNull(failed);
        Assert.Equal(SessionRequestState.Failed, failed!.State);
        Assert.Equal("yfinance returned no bars", failed.Error);
        Assert.NotNull(failed.CompletedAtUtc);
    }

    [Fact]
    public void Get_round_trips_the_payload_and_envelope_fields()
    {
        var store = NewStore();
        var row = store.Put(QuantEndpoints.Kind, SamplePayload());

        var fetched = store.Get(row.RequestId);

        Assert.NotNull(fetched);
        Assert.Equal(row.RequestId, fetched!.RequestId);
        Assert.Equal("backtest", fetched.Kind);
        Assert.Equal(SessionRequestState.Pending, fetched.State);
    }

    [Fact]
    public void Cancel_only_cancels_pending_or_claimed_rows()
    {
        var store = NewStore();
        var row = store.Put(QuantEndpoints.Kind, SamplePayload());

        var cancelled = store.Cancel(row.RequestId);

        Assert.NotNull(cancelled);
        Assert.Equal(SessionRequestState.Cancelled, cancelled!.State);

        // Calling cancel again on a terminal row is a no-op — state
        // stays Cancelled. (The store returns the latest row read.)
        var second = store.Cancel(row.RequestId);
        Assert.NotNull(second);
        Assert.Equal(SessionRequestState.Cancelled, second!.State);
    }
}

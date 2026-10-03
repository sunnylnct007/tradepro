using System.Globalization;
using System.Xml.Linq;
using Microsoft.Extensions.Options;

namespace TradePro.Api.Providers.IBKR;

/// <summary>One position row from an Open Positions Flex report.</summary>
public sealed record FlexPosition(
    string Account, string Symbol, string AssetCategory, string Currency,
    decimal Quantity, decimal? CostBasisPrice, decimal? MarkPrice,
    decimal? UnrealizedPnl, string? Conid, DateOnly? ReportDate);

/// <summary>One execution row from a Trades Flex report.</summary>
public sealed record FlexTrade(
    string Account, string Symbol, string AssetCategory, string Currency,
    string BuySell, decimal Quantity, decimal Price, decimal? Proceeds,
    decimal? Commission, string? TradeId, string? OrderId, string? Conid,
    DateTimeOffset? TradeTimeUtc);

public sealed record FlexResult<T>(
    bool Enabled, IReadOnlyList<T> Rows, string? Error,
    DateTimeOffset? FetchedAtUtc, bool FromCache, string? QueryId);

/// <summary>
/// IBKR Flex Web Service client.
///
/// Two-step by design on IBKR's side: SendRequest returns a reference code,
/// then GetStatement is polled until the report has been generated. We treat
/// the three outcomes as genuinely different things rather than one "failed":
///
///   * a transport failure      -> retryable, say so
///   * an IBKR error code       -> NOT retryable, surface the code verbatim
///   * "generation in progress" -> keep polling within the timeout
///
/// THIS CLIENT NEVER TOUCHES THE MARKET-DATA SESSION. That is the entire
/// point of the integration. It shares no state with IBKRClient, uses its own
/// token, and cannot affect or be affected by `compete`, line budgets, or a
/// portal login stealing the session.
/// </summary>
public sealed class IBKRFlexClient
{
    // IBKR's documented Flex error codes we handle by name rather than by
    // guessing from the message text.
    private const string ErrGenerationInProgress = "1019";
    private const string ErrTooManyRequests = "1018";
    private const string ErrInvalidToken = "1012";

    private readonly IHttpClientFactory _http;
    private readonly IOptions<IBKRFlexOptions> _opts;
    private readonly ILogger<IBKRFlexClient> _log;

    private readonly SemaphoreSlim _gate = new(1, 1);
    private readonly Dictionary<string, (DateTimeOffset at, string xml)> _cache = new();

    public IBKRFlexClient(IHttpClientFactory http, IOptions<IBKRFlexOptions> opts,
                          ILogger<IBKRFlexClient> log)
    {
        _http = http; _opts = opts; _log = log;
    }

    public bool IsEnabled => _opts.Value.IsEnabled;
    public string? PositionsQueryId => Blank(_opts.Value.PositionsQueryId);
    public string? TradesQueryId => Blank(_opts.Value.TradesQueryId);

    private static string? Blank(string s) => string.IsNullOrWhiteSpace(s) ? null : s;

    public async Task<FlexResult<FlexPosition>> GetPositionsAsync(
        CancellationToken ct, bool forceFresh = false)
    {
        var q = PositionsQueryId;
        if (!IsEnabled || q is null)
            return new(false, Array.Empty<FlexPosition>(),
                "IBKR Flex not configured — set IBKRFlex:Token and "
                + "IBKRFlex:PositionsQueryId (see IBKRFlexOptions for the "
                + "Account Management steps).", null, false, null);

        var (xml, err, cached, at) = await FetchAsync(q, ct, forceFresh);
        if (xml is null)
            return new(true, Array.Empty<FlexPosition>(), err, at, cached, q);

        try
        {
            return new(true, ParsePositions(xml), null, at, cached, q);
        }
        catch (Exception ex)
        {
            // A parse failure must NOT read as "no positions". An unreadable
            // report is not an empty account — that confusion is exactly how
            // a flatten against an assumed-flat book goes wrong.
            _log.LogError(ex, "IBKR Flex positions parse failed");
            return new(true, Array.Empty<FlexPosition>(),
                $"report fetched but could not be parsed: {ex.Message}", at, cached, q);
        }
    }

    public async Task<FlexResult<FlexTrade>> GetTradesAsync(
        CancellationToken ct, bool forceFresh = false)
    {
        var q = TradesQueryId;
        if (!IsEnabled || q is null)
            return new(false, Array.Empty<FlexTrade>(),
                "IBKR Flex not configured — set IBKRFlex:Token and "
                + "IBKRFlex:TradesQueryId.", null, false, null);

        var (xml, err, cached, at) = await FetchAsync(q, ct, forceFresh);
        if (xml is null)
            return new(true, Array.Empty<FlexTrade>(), err, at, cached, q);

        try
        {
            return new(true, ParseTrades(xml), null, at, cached, q);
        }
        catch (Exception ex)
        {
            _log.LogError(ex, "IBKR Flex trades parse failed");
            return new(true, Array.Empty<FlexTrade>(),
                $"report fetched but could not be parsed: {ex.Message}", at, cached, q);
        }
    }

    // ── fetch: SendRequest -> poll GetStatement ─────────────────────────

    private async Task<(string? xml, string? error, bool cached, DateTimeOffset? at)>
        FetchAsync(string queryId, CancellationToken ct, bool forceFresh)
    {
        var o = _opts.Value;
        var ttl = TimeSpan.FromSeconds(Math.Max(0, o.CacheSeconds));

        await _gate.WaitAsync(ct);
        try
        {
            if (!forceFresh && _cache.TryGetValue(queryId, out var hit)
                && DateTimeOffset.UtcNow - hit.at < ttl)
            {
                return (hit.xml, null, true, hit.at);
            }

            var client = _http.CreateClient("ibkr-flex");
            client.Timeout = TimeSpan.FromSeconds(60);

            var sendUrl = $"{o.BaseUrl.TrimEnd('/')}/SendRequest"
                        + $"?t={Uri.EscapeDataString(o.Token)}"
                        + $"&q={Uri.EscapeDataString(queryId)}&v=3";
            string sendBody;
            try
            {
                using var r = await client.GetAsync(sendUrl, ct);
                sendBody = await r.Content.ReadAsStringAsync(ct);
            }
            catch (Exception ex)
            {
                return (null, $"SendRequest unreachable: {ex.Message}", false, null);
            }

            var (code, msg, reference) = ReadEnvelope(sendBody);
            if (code is not null)
                return (null, DescribeError(code, msg), false, null);
            if (string.IsNullOrWhiteSpace(reference))
                return (null, "SendRequest returned no ReferenceCode", false, null);

            var deadline = DateTimeOffset.UtcNow.AddSeconds(
                Math.Max(5, o.GenerationTimeoutSeconds));
            var delay = TimeSpan.FromSeconds(3);

            while (true)
            {
                var getUrl = $"{o.BaseUrl.TrimEnd('/')}/GetStatement"
                           + $"?t={Uri.EscapeDataString(o.Token)}"
                           + $"&q={Uri.EscapeDataString(reference!)}&v=3";
                string body;
                try
                {
                    using var r = await client.GetAsync(getUrl, ct);
                    body = await r.Content.ReadAsStringAsync(ct);
                }
                catch (Exception ex)
                {
                    return (null, $"GetStatement unreachable: {ex.Message}", false, null);
                }

                var (gc, gm, _) = ReadEnvelope(body);
                if (gc is null)
                {
                    var now = DateTimeOffset.UtcNow;
                    _cache[queryId] = (now, body);
                    return (body, null, false, now);
                }
                if (gc != ErrGenerationInProgress)
                    return (null, DescribeError(gc, gm), false, null);

                if (DateTimeOffset.UtcNow + delay > deadline)
                    return (null,
                        $"IBKR was still generating the report after "
                        + $"{o.GenerationTimeoutSeconds}s. This is not a failure — "
                        + "large Trades reports are slow. Try again shortly.",
                        false, null);

                await Task.Delay(delay, ct);
                delay = TimeSpan.FromSeconds(Math.Min(15, delay.TotalSeconds * 1.5));
            }
        }
        finally
        {
            _gate.Release();
        }
    }

    /// <summary>Pull (ErrorCode, ErrorMessage, ReferenceCode) out of a Flex
    /// envelope. Flex answers 200 with an error body, so the status code
    /// tells you nothing — the body is the only truth.</summary>
    /// <remarks>Public purely so the tests can call it. It is a pure
    /// function over a response body with no I/O and no state — the error
    /// envelopes it decodes (1012 expired token, 1019 still generating)
    /// are the difference between "ask again" and "your token is dead",
    /// and that distinction deserves direct tests rather than inference
    /// through an HTTP mock.</remarks>
    public static (string? code, string? message, string? reference) ReadEnvelope(string body)
    {
        if (string.IsNullOrWhiteSpace(body)) return (null, null, null);
        try
        {
            var doc = XDocument.Parse(body);
            string? Val(string name) => doc.Descendants()
                .FirstOrDefault(e => string.Equals(e.Name.LocalName, name,
                    StringComparison.OrdinalIgnoreCase))?.Value?.Trim();
            return (Blank(Val("ErrorCode") ?? ""), Val("ErrorMessage"), Val("ReferenceCode"));
        }
        catch
        {
            return (null, null, null);   // not XML we understand; let the caller try to parse
        }
    }

    private static string DescribeError(string code, string? msg) => code switch
    {
        ErrInvalidToken =>
            $"IBKR Flex token is invalid or expired ({code}). Regenerate it in "
            + "Account Management → Flex Web Service and update the "
            + "tradepro/ibkr-flex secret. Tokens expire — this will recur.",
        ErrTooManyRequests =>
            $"IBKR is rate-limiting Flex ({code}). Reports are generated on "
            + "their schedule; raise IBKRFlex:CacheSeconds rather than retrying.",
        _ => $"IBKR Flex error {code}: {msg ?? "(no message)"}",
    };

    // ── parsing ─────────────────────────────────────────────────────────

    private static decimal? Dec(XElement e, string attr)
        => decimal.TryParse(e.Attribute(attr)?.Value, NumberStyles.Any,
               CultureInfo.InvariantCulture, out var v) ? v : null;

    private static string Str(XElement e, string attr) => e.Attribute(attr)?.Value ?? "";

    /// <remarks>Public for tests — pure, no I/O.</remarks>
    public static IReadOnlyList<FlexPosition> ParsePositions(string xml)
    {
        var doc = XDocument.Parse(xml);
        var rows = new List<FlexPosition>();
        foreach (var e in doc.Descendants().Where(x =>
                     string.Equals(x.Name.LocalName, "OpenPosition", StringComparison.OrdinalIgnoreCase)))
        {
            DateOnly? rd = DateOnly.TryParseExact(Str(e, "reportDate"), "yyyyMMdd",
                CultureInfo.InvariantCulture, DateTimeStyles.None, out var d) ? d : null;
            rows.Add(new FlexPosition(
                Str(e, "accountId"), Str(e, "symbol"), Str(e, "assetCategory"),
                Str(e, "currency"), Dec(e, "position") ?? 0m, Dec(e, "costBasisPrice"),
                Dec(e, "markPrice"), Dec(e, "fifoPnlUnrealized"),
                Blank(Str(e, "conid")), rd));
        }
        return rows;
    }

    /// <remarks>Public for tests — pure, no I/O.</remarks>
    public static IReadOnlyList<FlexTrade> ParseTrades(string xml)
    {
        var doc = XDocument.Parse(xml);
        var rows = new List<FlexTrade>();
        foreach (var e in doc.Descendants().Where(x =>
                     string.Equals(x.Name.LocalName, "Trade", StringComparison.OrdinalIgnoreCase)))
        {
            DateTimeOffset? ts = null;
            // Flex writes "yyyyMMdd;HHmmss" by default, and plain "yyyyMMdd"
            // when the query omits the time column.
            var raw = Str(e, "dateTime").Replace(" ", "");
            foreach (var fmt in new[] { "yyyyMMdd;HHmmss", "yyyyMMddHHmmss", "yyyyMMdd" })
            {
                if (DateTime.TryParseExact(raw, fmt, CultureInfo.InvariantCulture,
                        DateTimeStyles.AssumeUniversal | DateTimeStyles.AdjustToUniversal,
                        out var dt))
                {
                    ts = new DateTimeOffset(dt, TimeSpan.Zero);
                    break;
                }
            }
            rows.Add(new FlexTrade(
                Str(e, "accountId"), Str(e, "symbol"), Str(e, "assetCategory"),
                Str(e, "currency"), Str(e, "buySell"), Dec(e, "quantity") ?? 0m,
                Dec(e, "tradePrice") ?? 0m, Dec(e, "proceeds"), Dec(e, "ibCommission"),
                Blank(Str(e, "tradeID")), Blank(Str(e, "ibOrderID")),
                Blank(Str(e, "conid")), ts));
        }
        return rows;
    }
}

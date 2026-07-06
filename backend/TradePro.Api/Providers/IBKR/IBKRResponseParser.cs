using System.Text.Json;

namespace TradePro.Api.Providers.IBKR;

/// <summary>
/// Pure parsers for the IBKR OAuth2 / cpapi JSON response shapes. Kept
/// separate from the HTTP client so they can be unit-tested against
/// canned documented payloads with no network. All parsers are tolerant:
/// missing fields yield null rather than throwing, because IBKR's cpapi
/// occasionally omits optional blocks.
/// </summary>
public static class IBKRResponseParser
{
    /// <summary>Parse the step-1 OAuth2 token response
    /// (<c>{ "access_token": "...", "token_type": "Bearer",
    /// "expires_in": 86400, "scope": "sso-sessions.write" }</c>).</summary>
    public static IBKRTokenResponse ParseToken(string json)
    {
        using var doc = JsonDocument.Parse(json);
        var root = doc.RootElement;
        return new IBKRTokenResponse(
            AccessToken: Str(root, "access_token"),
            TokenType: Str(root, "token_type"),
            ExpiresInSeconds: Int(root, "expires_in"),
            Scope: Str(root, "scope"));
    }

    /// <summary>Parse the step-2 sso-sessions response. Per the IBKR Postman
    /// collection the response carries <c>access_token</c> (this is SSO_ACCESS,
    /// the DIFFERENT token that becomes the bearer for all downstream /v1/api
    /// calls). Some gateway versions name it <c>session_token</c> — accept
    /// either. The chosen value is stored as the working bearer (SSO_ACCESS).</summary>
    public static IBKRSsoSessionResponse ParseSsoSession(string json)
    {
        using var doc = JsonDocument.Parse(json);
        var root = doc.RootElement;
        return new IBKRSsoSessionResponse(
            SessionToken: Str(root, "session_token") ?? Str(root, "access_token"),
            ExpiresInSeconds: Int(root, "expires_in"));
    }

    /// <summary>Parse the /tickle response. We care about
    /// <c>session</c> (the session id) and <c>iserver.authStatus.authenticated</c>
    /// / <c>connected</c> so keepalive can detect a dropped session.</summary>
    public static IBKRTickleResponse ParseTickle(string json)
    {
        using var doc = JsonDocument.Parse(json);
        var root = doc.RootElement;
        bool authenticated = false, connected = false;
        if (root.TryGetProperty("iserver", out var iserver)
            && iserver.ValueKind == JsonValueKind.Object
            && iserver.TryGetProperty("authStatus", out var auth)
            && auth.ValueKind == JsonValueKind.Object)
        {
            authenticated = Bool(auth, "authenticated");
            connected = Bool(auth, "connected");
        }
        return new IBKRTickleResponse(
            Session: Str(root, "session"),
            Authenticated: authenticated,
            Connected: connected);
    }

    /// <summary>Parse GET /iserver/accounts — an array of account id
    /// strings under <c>accounts</c> plus a <c>selectedAccount</c>.</summary>
    public static IReadOnlyList<string> ParseAccounts(string json)
    {
        using var doc = JsonDocument.Parse(json);
        var root = doc.RootElement;
        var ids = new List<string>();
        if (root.ValueKind == JsonValueKind.Object
            && root.TryGetProperty("accounts", out var arr)
            && arr.ValueKind == JsonValueKind.Array)
        {
            foreach (var a in arr.EnumerateArray())
                if (a.ValueKind == JsonValueKind.String && a.GetString() is { } s)
                    ids.Add(s);
        }
        return ids;
    }

    /// <summary>Parse GET /portfolio/{accountId}/positions — IBKR returns
    /// a flat array of position objects (conid, ticker/contractDesc,
    /// position qty, mktPrice, avgCost, unrealizedPnl, currency).</summary>
    public static IReadOnlyList<IBKRPosition> ParsePositions(string json)
    {
        using var doc = JsonDocument.Parse(json);
        var root = doc.RootElement;
        var result = new List<IBKRPosition>();
        if (root.ValueKind != JsonValueKind.Array) return result;
        foreach (var p in root.EnumerateArray())
        {
            if (p.ValueKind != JsonValueKind.Object) continue;
            result.Add(new IBKRPosition(
                ConId: Long(p, "conid"),
                Symbol: Str(p, "ticker") ?? Str(p, "contractDesc"),
                Quantity: Dec(p, "position") ?? 0m,
                AvgCost: Dec(p, "avgCost"),
                MarketPrice: Dec(p, "mktPrice"),
                MarketValue: Dec(p, "mktValue"),
                UnrealizedPnl: Dec(p, "unrealizedPnl"),
                Currency: Str(p, "currency")));
        }
        return result;
    }

    /// <summary>Parse GET /portfolio/{accountId}/ledger — a map keyed by
    /// currency (plus a "BASE" synthetic). We surface the BASE row's
    /// cashbalance + netliquidationvalue for sizing.</summary>
    public static IBKRCashSummary ParseLedger(string json)
    {
        using var doc = JsonDocument.Parse(json);
        var root = doc.RootElement;
        if (root.ValueKind != JsonValueKind.Object)
            return new IBKRCashSummary(null, null, null, null);

        // Prefer the BASE aggregate; fall back to the first currency row.
        JsonElement? chosen = null;
        string? chosenCcy = null;
        if (root.TryGetProperty("BASE", out var baseEl) && baseEl.ValueKind == JsonValueKind.Object)
        {
            chosen = baseEl;
            chosenCcy = Str(baseEl, "currency") ?? "BASE";
        }
        else
        {
            foreach (var prop in root.EnumerateObject())
            {
                if (prop.Value.ValueKind == JsonValueKind.Object)
                {
                    chosen = prop.Value;
                    chosenCcy = Str(prop.Value, "currency") ?? prop.Name;
                    break;
                }
            }
        }
        if (chosen is null) return new IBKRCashSummary(null, null, null, null);
        var el = chosen.Value;
        return new IBKRCashSummary(
            Cash: Dec(el, "cashbalance"),
            NetLiquidation: Dec(el, "netliquidationvalue"),
            UnrealizedPnl: Dec(el, "unrealizedpnl"),
            Currency: chosenCcy);
    }

    /// <summary>Parse POST /iserver/account/{accountId}/orders response —
    /// an array; IBKR returns the placed order id (or a reply-id needing
    /// confirmation). We surface the first order id / error.</summary>
    public static IBKROrderAck ParseOrderAck(string json)
    {
        using var doc = JsonDocument.Parse(json);
        var root = doc.RootElement;
        // Error shape: { "error": "...." }
        if (root.ValueKind == JsonValueKind.Object && root.TryGetProperty("error", out var err))
            return new IBKROrderAck(OrderId: null, OrderStatus: null, Error: err.GetString());

        // Reply-confirmation shape: [ { "id": "<replyId>", "message": [...] } ]
        // Placed shape:             [ { "order_id": "123", "order_status": "Submitted" } ]
        if (root.ValueKind == JsonValueKind.Array)
        {
            foreach (var item in root.EnumerateArray())
            {
                if (item.ValueKind != JsonValueKind.Object) continue;
                var orderId = Str(item, "order_id") ?? Str(item, "orderId");
                var status = Str(item, "order_status") ?? Str(item, "orderStatus");
                var replyId = Str(item, "id");
                // A reply with a "message" but no order id means IBKR wants
                // a confirmation POST to /iserver/reply/{replyId}.
                if (orderId is null && replyId is not null
                    && item.TryGetProperty("message", out _))
                {
                    return new IBKROrderAck(OrderId: null, OrderStatus: "NEEDS_CONFIRM",
                        Error: null, ReplyId: replyId);
                }
                return new IBKROrderAck(OrderId: orderId, OrderStatus: status, Error: null);
            }
        }
        return new IBKROrderAck(OrderId: null, OrderStatus: null, Error: "unparseable order ack");
    }

    /// <summary>Parse GET /iserver/secdef/search?symbol=X — an array of matches,
    /// best-first. Returns the FIRST match's conid (may be a string or number in
    /// the payload). Null when there's no match — the caller FLAGS the symbol as
    /// unresolvable rather than swallowing it (fail-loud).</summary>
    public static long? ParseConidSearch(string json)
    {
        using var doc = JsonDocument.Parse(json);
        var root = doc.RootElement;
        if (root.ValueKind != JsonValueKind.Array) return null;
        foreach (var m in root.EnumerateArray())
        {
            if (m.ValueKind != JsonValueKind.Object) continue;
            if (m.TryGetProperty("conid", out var c))
            {
                if (c.ValueKind == JsonValueKind.Number && c.TryGetInt64(out var n)) return n;
                if (c.ValueKind == JsonValueKind.String && long.TryParse(c.GetString(), out var s)) return s;
            }
        }
        return null;
    }

    /// <summary>Parse GET /iserver/marketdata/history — OHLCV bars in the "data"
    /// array ({t: epoch-MILLISECONDS UTC, o,h,l,c, v}). Empty list when there are
    /// no bars; the caller FLAGS that (never a silent empty).</summary>
    public static IReadOnlyList<IBKRBar> ParseHistory(string json)
    {
        using var doc = JsonDocument.Parse(json);
        var root = doc.RootElement;
        var bars = new List<IBKRBar>();
        if (root.ValueKind != JsonValueKind.Object
            || !root.TryGetProperty("data", out var data)
            || data.ValueKind != JsonValueKind.Array) return bars;
        foreach (var b in data.EnumerateArray())
        {
            if (b.ValueKind != JsonValueKind.Object) continue;
            long? t = Long(b, "t");
            decimal? o = Dec(b, "o"), h = Dec(b, "h"), l = Dec(b, "l"), c = Dec(b, "c");
            if (t is null || o is null || h is null || l is null || c is null) continue;
            bars.Add(new IBKRBar(t.Value, o.Value, h.Value, l.Value, c.Value, (long)(Dec(b, "v") ?? 0m)));
        }
        return bars;
    }

    /// <summary>Parse GET /iserver/account/orders — the broker's live order
    /// blotter (GOLDEN SOURCE for order status/fills). Field names vary by
    /// gateway version; we read the common ones tolerantly.</summary>
    public static IReadOnlyList<IBKRLiveOrder> ParseLiveOrders(string json)
    {
        using var doc = JsonDocument.Parse(json);
        var root = doc.RootElement;
        JsonElement arr;
        if (root.ValueKind == JsonValueKind.Object
            && root.TryGetProperty("orders", out var o) && o.ValueKind == JsonValueKind.Array)
            arr = o;
        else if (root.ValueKind == JsonValueKind.Array)
            arr = root;
        else
            return Array.Empty<IBKRLiveOrder>();
        var list = new List<IBKRLiveOrder>();
        foreach (var it in arr.EnumerateArray())
        {
            if (it.ValueKind != JsonValueKind.Object) continue;
            list.Add(new IBKRLiveOrder(
                OrderId: StrLoose(it, "orderId") ?? StrLoose(it, "order_id"),
                Status: Str(it, "status") ?? Str(it, "order_status"),
                Symbol: Str(it, "ticker") ?? Str(it, "symbol"),
                Side: Str(it, "side"),
                TotalSize: Dec(it, "totalSize") ?? Dec(it, "quantity"),
                FilledQty: Dec(it, "filledQuantity") ?? Dec(it, "cumFill") ?? Dec(it, "filled"),
                RemainingQty: Dec(it, "remainingQuantity") ?? Dec(it, "remaining"),
                AvgPrice: Dec(it, "avgPrice") ?? Dec(it, "price")));
        }
        return list;
    }

    // ─── helpers ────────────────────────────────────────────────────
    /// <summary>String from a String OR Number field (IBKR order ids arrive as
    /// numbers in some payloads, strings in others).</summary>
    private static string? StrLoose(JsonElement el, string prop)
    {
        if (el.ValueKind != JsonValueKind.Object || !el.TryGetProperty(prop, out var v)) return null;
        return v.ValueKind switch
        {
            JsonValueKind.String => v.GetString(),
            JsonValueKind.Number => v.ToString(),
            _ => null,
        };
    }

    private static string? Str(JsonElement el, string prop) =>
        el.ValueKind == JsonValueKind.Object
        && el.TryGetProperty(prop, out var v)
        && v.ValueKind == JsonValueKind.String ? v.GetString() : null;

    private static int? Int(JsonElement el, string prop) =>
        el.ValueKind == JsonValueKind.Object
        && el.TryGetProperty(prop, out var v)
        && v.ValueKind == JsonValueKind.Number && v.TryGetInt32(out var i) ? i : null;

    private static long? Long(JsonElement el, string prop) =>
        el.ValueKind == JsonValueKind.Object
        && el.TryGetProperty(prop, out var v)
        && v.ValueKind == JsonValueKind.Number && v.TryGetInt64(out var i) ? i : null;

    private static decimal? Dec(JsonElement el, string prop) =>
        el.ValueKind == JsonValueKind.Object
        && el.TryGetProperty(prop, out var v)
        && v.ValueKind == JsonValueKind.Number && v.TryGetDecimal(out var d) ? d : null;

    private static bool Bool(JsonElement el, string prop) =>
        el.TryGetProperty(prop, out var v) && v.ValueKind == JsonValueKind.True;
}

// ─── DTOs (documented IBKR shapes) ─────────────────────────────────

public sealed record IBKRTokenResponse(
    string? AccessToken,
    string? TokenType,
    int? ExpiresInSeconds,
    string? Scope);

public sealed record IBKRSsoSessionResponse(
    string? SessionToken,
    int? ExpiresInSeconds);

public sealed record IBKRTickleResponse(
    string? Session,
    bool Authenticated,
    bool Connected);

public sealed record IBKRPosition(
    long? ConId,
    string? Symbol,
    decimal Quantity,
    decimal? AvgCost,
    decimal? MarketPrice,
    decimal? MarketValue,
    decimal? UnrealizedPnl,
    string? Currency);

public sealed record IBKRPositionsResult(
    IReadOnlyList<IBKRPosition> Positions,
    string? Error,
    int HttpStatus);

public sealed record IBKRCashSummary(
    decimal? Cash,
    decimal? NetLiquidation,
    decimal? UnrealizedPnl,
    string? Currency);

public sealed record IBKRCashResult(
    decimal? Cash,
    decimal? NetLiquidation,
    decimal? UnrealizedPnl,
    string? Currency,
    string? Error,
    int HttpStatus);

public sealed record IBKROrderAck(
    string? OrderId,
    string? OrderStatus,     // Submitted / Filled / NEEDS_CONFIRM / ...
    string? Error,
    string? ReplyId = null);

public sealed record IBKROrderResult(
    string? OrderId,
    string Status,           // ACCEPTED / REJECTED / NEEDS_CONFIRM / PARSE_ERROR
    string? StatusReason,
    int HttpStatus);

/// <summary>One row of the broker's live order blotter (GET /iserver/account/orders)
/// — the golden source the OMS reconciles TO.</summary>
public sealed record IBKRLiveOrder(
    string? OrderId,
    string? Status,          // PreSubmitted / Submitted / Filled / Cancelled / ...
    string? Symbol,
    string? Side,
    decimal? TotalSize,
    decimal? FilledQty,
    decimal? RemainingQty,
    decimal? AvgPrice);

public sealed record IBKROrdersResult(
    IReadOnlyList<IBKRLiveOrder> Orders,
    string? Error,
    int HttpStatus);

/// <summary>Read-only connectivity probe result (see
/// <c>IBKRClient.GetStatusAsync</c>). No order is placed to produce this.</summary>
public sealed record IBKRStatusResult(
    bool Enabled,                       // IBKROptions.IsEnabled (secret present + mode set)
    bool Authenticated,                 // full OAuth bring-up + iserver/accounts succeeded
    IReadOnlyList<string> Accounts,     // account ids the session can see
    string? AccountIdInUse,             // the configured account this app routes to
    string Mode,                        // paper / live / disabled
    string BrokerLabel,                 // IBKR_PAPER / IBKR_LIVE
    string? IpInUse,                    // ip sent in the sso-sessions claim (null until resolved)
    string? IpSource,                   // "override" | "auto-detected" | null
    string? Error);                     // verbatim IBKR error body on failure, else null

/// <summary>One OHLCV bar from IBKR marketdata/history. Time is epoch MILLISECONDS UTC.</summary>
public sealed record IBKRBar(
    long TimeMs, decimal Open, decimal High, decimal Low, decimal Close, long Volume);

/// <summary>Result of <c>IBKRClient.GetPriceHistoryAsync</c>. On failure Bars is
/// empty and <see cref="Error"/> carries the reason — callers MUST surface it
/// (fail-loud); an empty result is NEVER to be treated as "no data" silently.</summary>
public sealed record IBKRHistoryResult(
    IReadOnlyList<IBKRBar> Bars,
    long? ConId,
    string? Error,
    int HttpStatus);

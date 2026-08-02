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

    /// <summary>Parse GET /iserver/account/trades — an array of last-day
    /// executions. Defensive on field names (IBKR varies: side B/S, size/qty).</summary>
    public static IReadOnlyList<IBKRTrade> ParseTrades(string json)
    {
        using var doc = JsonDocument.Parse(json);
        var root = doc.RootElement;
        JsonElement arr;
        if (root.ValueKind == JsonValueKind.Array)
            arr = root;
        else if (root.ValueKind == JsonValueKind.Object
                 && root.TryGetProperty("trades", out var t) && t.ValueKind == JsonValueKind.Array)
            arr = t;
        else
            return Array.Empty<IBKRTrade>();
        var list = new List<IBKRTrade>();
        foreach (var it in arr.EnumerateArray())
        {
            if (it.ValueKind != JsonValueKind.Object) continue;
            list.Add(new IBKRTrade(
                Symbol: Str(it, "symbol") ?? Str(it, "ticker"),
                Side: Str(it, "side"),
                Size: Dec(it, "size") ?? Dec(it, "quantity") ?? 0m,
                Price: Dec(it, "price") ?? 0m,
                TradeTime: Str(it, "trade_time") ?? StrLoose(it, "trade_time_r"),
                ExecId: Str(it, "execution_id") ?? Str(it, "exec_id"),
                Account: Str(it, "account") ?? Str(it, "acctNumber")));
        }
        return list;
    }

    /// <summary>Parse GET /iserver/secdef/search?symbol=X for the option chain flow:
    /// the underlying's conid plus its listed expiration months. IBKR has been seen
    /// to carry the months two different ways — a `sections` array with a
    /// secType="OPT" entry's `months` field, or a top-level semicolon string (`opt`
    /// or `optType`) — so both are tried; first match wins. Best-first array entry
    /// (matches <see cref="ParseConidSearch"/>'s convention).</summary>
    public static (long? ConId, IReadOnlyList<string> Months) ParseOptionMonths(string json)
    {
        using var doc = JsonDocument.Parse(json);
        var root = doc.RootElement;
        if (root.ValueKind != JsonValueKind.Array) return (null, Array.Empty<string>());
        foreach (var m in root.EnumerateArray())
        {
            if (m.ValueKind != JsonValueKind.Object) continue;
            long? conId = null;
            if (m.TryGetProperty("conid", out var c))
            {
                if (c.ValueKind == JsonValueKind.Number && c.TryGetInt64(out var n)) conId = n;
                else if (c.ValueKind == JsonValueKind.String && long.TryParse(c.GetString(), out var s)) conId = s;
            }
            if (conId is null) continue;

            var months = new List<string>();
            if (m.TryGetProperty("sections", out var sections) && sections.ValueKind == JsonValueKind.Array)
            {
                foreach (var sec in sections.EnumerateArray())
                {
                    if (sec.ValueKind != JsonValueKind.Object) continue;
                    if (Str(sec, "secType") != "OPT") continue;
                    var raw = Str(sec, "months");
                    if (!string.IsNullOrWhiteSpace(raw))
                        months.AddRange(raw.Split(';', StringSplitOptions.RemoveEmptyEntries));
                }
            }
            if (months.Count == 0)
            {
                var flat = Str(m, "opt") ?? Str(m, "optType");
                if (!string.IsNullOrWhiteSpace(flat))
                    months.AddRange(flat.Split(';', StringSplitOptions.RemoveEmptyEntries));
            }
            return (conId, months);
        }
        return (null, Array.Empty<string>());
    }

    /// <summary>Parse GET /iserver/secdef/strikes — <c>{"call": [...], "put": [...]}</c>
    /// of numeric strikes (may arrive as JSON numbers or strings; tolerant either way).</summary>
    public static (IReadOnlyList<decimal> Calls, IReadOnlyList<decimal> Puts) ParseStrikes(string json)
    {
        using var doc = JsonDocument.Parse(json);
        var root = doc.RootElement;
        if (root.ValueKind != JsonValueKind.Object) return (Array.Empty<decimal>(), Array.Empty<decimal>());
        return (StrikeArray(root, "call"), StrikeArray(root, "put"));

        static IReadOnlyList<decimal> StrikeArray(JsonElement el, string prop)
        {
            var list = new List<decimal>();
            if (!el.TryGetProperty(prop, out var arr) || arr.ValueKind != JsonValueKind.Array) return list;
            foreach (var v in arr.EnumerateArray())
            {
                var d = v.ValueKind switch
                {
                    JsonValueKind.Number when v.TryGetDecimal(out var dn) => (decimal?)dn,
                    JsonValueKind.String when decimal.TryParse(v.GetString(), out var ds) => ds,
                    _ => null,
                };
                if (d is not null) list.Add(d.Value);
            }
            return list;
        }
    }

    /// <summary>Parse GET /iserver/secdef/info (strike omitted) — an array of
    /// contract objects, each carrying its own <c>conid</c> and <c>strike</c>.</summary>
    public static IReadOnlyList<IBKROptionContract> ParseOptionContracts(string json)
    {
        using var doc = JsonDocument.Parse(json);
        var root = doc.RootElement;
        var list = new List<IBKROptionContract>();
        if (root.ValueKind != JsonValueKind.Array) return list;
        foreach (var it in root.EnumerateArray())
        {
            if (it.ValueKind != JsonValueKind.Object) continue;
            long? conId = null;
            if (it.TryGetProperty("conid", out var c))
            {
                if (c.ValueKind == JsonValueKind.Number && c.TryGetInt64(out var n)) conId = n;
                else if (c.ValueKind == JsonValueKind.String && long.TryParse(c.GetString(), out var s)) conId = s;
            }
            var strike = DecLoose(it, "strike");
            if (conId is not null && strike is not null)
                list.Add(new IBKROptionContract(conId.Value, strike.Value));
        }
        return list;
    }

    /// <summary>Parse GET /iserver/marketdata/snapshot for option conids — field values
    /// arrive as either JSON numbers or strings depending on gateway version, so every
    /// value is read via <see cref="DecLoose"/>. See <c>IBKRClient.GetOptionSnapshotBatchAsync</c>
    /// for the field-code table and its provenance caveat.</summary>
    public static IReadOnlyList<IBKROptionQuote> ParseOptionSnapshot(string json)
    {
        using var doc = JsonDocument.Parse(json);
        var root = doc.RootElement;
        var list = new List<IBKROptionQuote>();
        if (root.ValueKind != JsonValueKind.Array) return list;
        foreach (var it in root.EnumerateArray())
        {
            if (it.ValueKind != JsonValueKind.Object) continue;
            long? conId = null;
            if (it.TryGetProperty("conid", out var c))
            {
                if (c.ValueKind == JsonValueKind.Number && c.TryGetInt64(out var n)) conId = n;
                else if (c.ValueKind == JsonValueKind.String && long.TryParse(c.GetString(), out var s)) conId = s;
            }
            if (conId is null) continue;
            list.Add(new IBKROptionQuote(
                ConId: conId.Value,
                Last: DecLoose(it, "31"),
                Bid: DecLoose(it, "84"),
                Ask: DecLoose(it, "86"),
                BidSize: DecLoose(it, "88"),
                AskSize: DecLoose(it, "85"),
                Volume: DecLoose(it, "87"),
                Delta: DecLoose(it, "7308"),
                Gamma: DecLoose(it, "7309"),
                Theta: DecLoose(it, "7310"),
                Vega: DecLoose(it, "7311"),
                ImpliedVolPct: DecLoose(it, "7633"),
                OpenInterest: DecLoose(it, "7638")));
        }
        return list;
    }

    /// <summary>Parse the single-conid raw snapshot body returned by
    /// <c>IBKRClient.GetSnapshotRawAsync</c> for field 31 (last price) — used to
    /// find an underlying's spot price for near-the-money strike filtering.</summary>
    public static decimal? ParseSnapshotLast(string json)
    {
        using var doc = JsonDocument.Parse(json);
        var root = doc.RootElement;
        var el = root.ValueKind == JsonValueKind.Array && root.GetArrayLength() > 0
            ? root[0]
            : root;
        return el.ValueKind == JsonValueKind.Object ? DecLoose(el, "31") : null;
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

    /// <summary>Decimal from a String OR Number field — IBKR's marketdata/snapshot
    /// encodes quote/greek values as strings on some gateway versions, raw numbers
    /// on others. Strips a trailing 'C'/'H' halted-market marker IBKR sometimes
    /// prefixes onto string values (e.g. "C168.42").</summary>
    private static decimal? DecLoose(JsonElement el, string prop)
    {
        if (el.ValueKind != JsonValueKind.Object || !el.TryGetProperty(prop, out var v)) return null;
        if (v.ValueKind == JsonValueKind.Number && v.TryGetDecimal(out var dn)) return dn;
        if (v.ValueKind == JsonValueKind.String)
        {
            var s = v.GetString();
            if (string.IsNullOrWhiteSpace(s)) return null;
            s = s.TrimStart('C', 'H');
            if (decimal.TryParse(s, out var ds)) return ds;
        }
        return null;
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

/// <summary>One executed trade from GET /iserver/account/trades (last-day
/// executions). Side is IBKR's raw "B"/"S" (or "BOT"/"SLD"); callers normalise.</summary>
public sealed record IBKRTrade(
    string? Symbol,
    string? Side,
    decimal Size,
    decimal Price,
    string? TradeTime,
    string? ExecId,
    string? Account);

public sealed record IBKRTradesResult(
    IReadOnlyList<IBKRTrade> Trades,
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

// ─── Option chain (G3) DTOs ─────────────────────────────────────────

/// <summary>Result of step 1 (secdef/search): underlying conid + listed
/// expiration months (e.g. "AUG26", "SEP26", ...).</summary>
public sealed record IBKROptionMonthsResult(
    long? ConId,
    IReadOnlyList<string> Months,
    string? Error,
    int HttpStatus);

/// <summary>Result of step 2 (secdef/strikes): available call/put strikes
/// for one underlying + expiration month.</summary>
public sealed record IBKROptionStrikesResult(
    IReadOnlyList<decimal> Calls,
    IReadOnlyList<decimal> Puts,
    string? Error,
    int HttpStatus);

/// <summary>One resolved option contract — its own tradeable conid + strike
/// (right and month are the caller's request context, not repeated here).</summary>
public sealed record IBKROptionContract(long ConId, decimal Strike);

/// <summary>Result of step 3 (secdef/info, strike omitted): every contract
/// for one underlying + month + right.</summary>
public sealed record IBKROptionContractsResult(
    IReadOnlyList<IBKROptionContract> Contracts,
    string? Error,
    int HttpStatus);

/// <summary>One option conid's live quote — bid/ask/last/greeks/OI. Null
/// fields mean IBKR hadn't warmed that field yet (never treat null as zero).</summary>
public sealed record IBKROptionQuote(
    long ConId,
    decimal? Last,
    decimal? Bid,
    decimal? Ask,
    decimal? BidSize,
    decimal? AskSize,
    decimal? Volume,
    decimal? Delta,
    decimal? Gamma,
    decimal? Theta,
    decimal? Vega,
    decimal? ImpliedVolPct,
    decimal? OpenInterest);

/// <summary>Result of step 4 (marketdata/snapshot, batched/chunked).</summary>
public sealed record IBKROptionQuotesResult(
    IReadOnlyList<IBKROptionQuote> Quotes,
    string? Error,
    int HttpStatus);

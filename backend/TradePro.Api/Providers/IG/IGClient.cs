using System.Net;
using System.Net.Http.Headers;
using System.Text.Json;
using Microsoft.Extensions.Options;

namespace TradePro.Api.Providers.IG;

/// <summary>
/// IG Markets REST API client. Single class because the IG surface we
/// touch is small (session, place order, get order/position, cash) and
/// adding more files would just spread the IG-specific oddities (CST
/// + X-SECURITY-TOKEN headers, EPIC vs ticker translation, expiry
/// codes) across the codebase.
///
/// Session management: re-login on first call OR when CST is unset.
/// The tokens are good for ~6 hours; we don't proactively refresh
/// (would need a background refresher). On HTTP 401 from any call,
/// we re-login once + retry — covers the expiry case.
///
/// Order placement: POST /positions/otc with currencyCode + epic +
/// expiry ("-" for cash equities, "DFB" for daily FX) + direction +
/// size + orderType ("MARKET"). Response includes dealReference; we
/// poll /confirms/{dealReference} for the final status (FILLED /
/// REJECTED / OPEN / DELETED).
/// </summary>
public sealed class IGClient
{
    private readonly HttpClient _http;
    private readonly IGOptions _options;
    private readonly ILogger<IGClient> _log;

    // Session tokens live in a SINGLETON cache shared across every (transient)
    // IGClient instance — see IGSessionCache. Holding them per-instance made
    // every request re-login to /session, tripping IG's anti-abuse block.
    private readonly IGSessionCache _session;

    // Per-epic dealing rules cache: (default dealing currency, min deal size).
    // IG rejects orders that send a currencyCode the instrument doesn't
    // support (e.g. hardcoded "USD" on EUR/JPY) or a size below the epic's
    // minDealSize — both with an opaque reason:"UNKNOWN". We fetch
    // /markets/{epic} once per epic to use the correct currency + floor the
    // size. Cached for process lifetime (dealing rules don't change intraday).
    private readonly System.Collections.Concurrent.ConcurrentDictionary<string, (string Currency, decimal MinSize)> _dealingCache = new();

    public IGClient(
        HttpClient http,
        IOptions<IGOptions> options,
        IGSessionCache session,
        ILogger<IGClient> log)
    {
        _http = http;
        _options = options.Value;
        _session = session;
        _log = log;
        if (_options.IsEnabled)
        {
            _http.BaseAddress = new Uri(_options.BaseUrl);
            // IG requires Accept-Version on every call. v1 covers
            // what we need; some calls require v2 — set per-request
            // when needed.
            _http.DefaultRequestHeaders.Accept.Add(
                new MediaTypeWithQualityHeaderValue("application/json"));
        }
    }

    public bool IsEnabled => _options.IsEnabled;
    public string BrokerLabel => _options.BrokerLabel;

    // ─── Session ────────────────────────────────────────────────

    /// <summary>POST /session to obtain CST + X-SECURITY-TOKEN. Called
    /// on first use and on 401-retry. Thread-safe via _loginLock so
    /// concurrent callers don't all hit /session at once.</summary>
    private async Task LoginAsync(CancellationToken ct)
    {
        await _session.Lock.WaitAsync(ct);
        try
        {
            // Another caller may have logged in while we waited on the lock,
            // or a still-valid session may already be cached — reuse it.
            if (_session.IsValid) return;
            // Back off during the post-failure cooldown so we don't hammer
            // /session while IG is blocking us (which keeps the block alive).
            if (!_session.MayAttemptLogin)
            {
                throw new InvalidOperationException(
                    "IG login backing off after a recent failure (cooldown) — "
                    + "not re-hitting /session yet");
            }
            var body = new
            {
                identifier = _options.Username,
                password = _options.Password,
            };
            using var req = new HttpRequestMessage(HttpMethod.Post, "session")
            {
                Content = JsonContent.Create(body),
            };
            req.Headers.Add("X-IG-API-KEY", _options.ApiKey);
            req.Headers.Add("Version", "2");
            using var resp = await _http.SendAsync(req, ct);
            if (!resp.IsSuccessStatusCode)
            {
                var bodyText = await resp.Content.ReadAsStringAsync(ct);
                _session.RecordFailure();
                throw new InvalidOperationException(
                    $"IG /session failed {(int)resp.StatusCode}: {bodyText}");
            }
            // IG returns the session tokens in RESPONSE headers, not body.
            var cstVal = resp.Headers.TryGetValues("CST", out var cst)
                ? cst.FirstOrDefault() : null;
            var xstVal = resp.Headers.TryGetValues("X-SECURITY-TOKEN", out var xst)
                ? xst.FirstOrDefault() : null;
            if (cstVal is null || xstVal is null)
            {
                _session.RecordFailure();
                throw new InvalidOperationException(
                    "IG /session returned 200 but no CST / X-SECURITY-TOKEN headers");
            }
            _session.Set(cstVal, xstVal);
            _log.LogInformation("IG session established ({Mode})", _options.Mode);
        }
        finally
        {
            _session.Lock.Release();
        }
    }

    private async Task<HttpResponseMessage> SendWithAuthAsync(
        HttpMethod method, string path, object? jsonBody, string version, CancellationToken ct,
        IDictionary<string, string>? extraHeaders = null)
    {
        if (!_options.IsEnabled)
        {
            throw new InvalidOperationException("IG client is disabled — set Mode + creds");
        }
        if (!_session.IsValid) await LoginAsync(ct);

        async Task<HttpResponseMessage> SendOnce()
        {
            using var req = new HttpRequestMessage(method, path);
            req.Headers.Add("X-IG-API-KEY", _options.ApiKey);
            req.Headers.Add("Version", version);
            if (_session.Cst is not null) req.Headers.Add("CST", _session.Cst);
            if (_session.XSecurityToken is not null) req.Headers.Add("X-SECURITY-TOKEN", _session.XSecurityToken);
            if (extraHeaders is not null)
                foreach (var (k, val) in extraHeaders) req.Headers.Add(k, val);
            if (jsonBody is not null) req.Content = JsonContent.Create(jsonBody);
            return await _http.SendAsync(req, ct);
        }

        var resp = await SendOnce();
        if (resp.StatusCode == HttpStatusCode.Unauthorized)
        {
            // Token expired — clear the shared cache + re-login once.
            _session.Clear();
            resp.Dispose();
            await LoginAsync(ct);
            resp = await SendOnce();
        }
        return resp;
    }

    // ─── Orders ─────────────────────────────────────────────────

    /// <summary>Place a market order. <paramref name="epic"/> is the
    /// IG instrument code (e.g. "IX.D.SPTRD.IFE.IP" for SPX); for
    /// equities it's typically "<symbol>.US.EQ" but the mapping is
    /// per-listing. <paramref name="direction"/> = "BUY" / "SELL".
    /// Returns the deal reference; poll <see cref="ConfirmDealAsync"/>
    /// for the final status.</summary>
    /// <summary>Resolve the epic's default dealing currency + min deal size
    /// from GET /markets/{epic}. Cached. DEFENSIVE: on any failure (network,
    /// parse, missing fields) returns ("USD", 0m) — i.e. exactly the old
    /// hardcoded behaviour — so this can never regress a pair that already
    /// works. minSize 0 means "don't clamp".</summary>
    private async Task<(string Currency, decimal MinSize)> GetDealingRulesAsync(
        string epic, CancellationToken ct)
    {
        if (_dealingCache.TryGetValue(epic, out var cached)) return cached;
        var result = ("USD", 0m);
        try
        {
            using var resp = await SendWithAuthAsync(
                HttpMethod.Get, $"markets/{Uri.EscapeDataString(epic)}", null, version: "3", ct);
            if (resp.IsSuccessStatusCode)
            {
                var text = await resp.Content.ReadAsStringAsync(ct);
                using var doc = JsonDocument.Parse(text);
                var root = doc.RootElement;
                string currency = "USD";
                decimal minSize = 0m;
                if (root.TryGetProperty("instrument", out var instr)
                    && instr.TryGetProperty("currencies", out var ccys)
                    && ccys.ValueKind == JsonValueKind.Array && ccys.GetArrayLength() > 0)
                {
                    // Prefer the IG-flagged default; else the first listed.
                    JsonElement chosen = ccys[0];
                    foreach (var c in ccys.EnumerateArray())
                        if (c.TryGetProperty("isDefault", out var d) && d.ValueKind == JsonValueKind.True)
                        { chosen = c; break; }
                    if (chosen.TryGetProperty("code", out var code) && code.GetString() is { Length: > 0 } cc)
                        currency = cc;
                }
                if (root.TryGetProperty("dealingRules", out var rules)
                    && rules.TryGetProperty("minDealSize", out var mds)
                    && mds.TryGetProperty("value", out var mv)
                    && mv.TryGetDecimal(out var mval))
                    minSize = mval;
                result = (currency, minSize);
            }
            else
            {
                _log.LogWarning("IG markets/{Epic} returned {Status}; using USD/no-clamp fallback",
                    epic, (int)resp.StatusCode);
            }
        }
        catch (Exception ex)
        {
            _log.LogWarning(ex, "IG dealing-rules fetch failed for {Epic}; using USD/no-clamp fallback", epic);
        }
        _dealingCache[epic] = result;
        return result;
    }

    public async Task<IGOrderResult> PlaceMarketOrderAsync(
        string epic, string direction, decimal size,
        string? expiry = "-",
        CancellationToken ct = default)
    {
        // Use the epic's real dealing currency + min size. The old code
        // hardcoded currencyCode="USD" (which IG rejects for non-USD-quote
        // pairs like EUR/JPY, USD/CHF — reason:"UNKNOWN") and never floored
        // the size to minDealSize (so vol-target sizes like 0.1 bounced).
        var (currency, minSize) = await GetDealingRulesAsync(epic, ct);
        var effectiveSize = (minSize > 0m && size < minSize) ? minSize : size;
        if (effectiveSize != size || currency != "USD")
            _log.LogInformation(
                "IG order {Epic}: currency {Ccy}, size {Size}->{Eff} (minDealSize {Min})",
                epic, currency, size, effectiveSize, minSize);
        var body = new
        {
            epic,
            expiry = expiry ?? "-",
            direction = direction.ToUpperInvariant(),  // BUY / SELL
            size = effectiveSize,
            orderType = "MARKET",
            currencyCode = currency,
            // forceOpen=false so an order NETS against any existing opposing
            // position instead of opening a separate deal. ichimoku_fx_mr is a
            // delta-based net-position strategy (it emits target-minus-current
            // sized orders), so the correct IG behaviour is to net: a SELL
            // against a long reduces it, a same-direction order aggregates.
            // forceOpen=true was opening a NEW deal per order, which is why
            // EUR/USD had 21 coexisting deals netting to one position.
            forceOpen = false,
            guaranteedStop = false,
        };
        using var resp = await SendWithAuthAsync(
            HttpMethod.Post, "positions/otc", body, version: "2", ct);
        var text = await resp.Content.ReadAsStringAsync(ct);
        if (!resp.IsSuccessStatusCode)
        {
            return new IGOrderResult(
                DealReference: null,
                Status: "REJECTED",
                StatusReason: text,
                HttpStatus: (int)resp.StatusCode);
        }
        try
        {
            using var doc = JsonDocument.Parse(text);
            var dealRef = doc.RootElement.TryGetProperty("dealReference", out var dr)
                ? dr.GetString() : null;
            return new IGOrderResult(
                DealReference: dealRef,
                Status: "ACCEPTED",
                StatusReason: null,
                HttpStatus: (int)resp.StatusCode);
        }
        catch (Exception ex)
        {
            return new IGOrderResult(
                DealReference: null,
                Status: "PARSE_ERROR",
                StatusReason: ex.Message,
                HttpStatus: (int)resp.StatusCode);
        }
    }

    /// <summary>Close an open deal at market. IG closes positions via
    /// POST /positions/otc with a `_method: DELETE` override header,
    /// sending the OPPOSITE direction for the same size against the
    /// specific dealId. Used by the "Flatten FX" action to net a symbol
    /// down to flat by closing each stacked deal individually.
    /// `openDirection` is the deal's current direction (BUY/SELL); we
    /// flip it to close.</summary>
    public async Task<IGOrderResult> CloseDealAsync(
        string dealId, string openDirection, decimal size, CancellationToken ct = default)
    {
        var closeDir = openDirection.ToUpperInvariant() == "BUY" ? "SELL" : "BUY";
        var body = new
        {
            dealId,
            direction = closeDir,
            size,
            orderType = "MARKET",
        };
        using var resp = await SendWithAuthAsync(
            HttpMethod.Post, "positions/otc", body, version: "1", ct,
            extraHeaders: new Dictionary<string, string> { ["_method"] = "DELETE" });
        var text = await resp.Content.ReadAsStringAsync(ct);
        if (!resp.IsSuccessStatusCode)
        {
            return new IGOrderResult(
                DealReference: null,
                Status: "REJECTED",
                StatusReason: text,
                HttpStatus: (int)resp.StatusCode);
        }
        try
        {
            using var doc = JsonDocument.Parse(text);
            var dealRef = doc.RootElement.TryGetProperty("dealReference", out var dr)
                ? dr.GetString() : null;
            return new IGOrderResult(
                DealReference: dealRef,
                Status: "ACCEPTED",
                StatusReason: null,
                HttpStatus: (int)resp.StatusCode);
        }
        catch (Exception ex)
        {
            return new IGOrderResult(
                DealReference: null,
                Status: "PARSE_ERROR",
                StatusReason: ex.Message,
                HttpStatus: (int)resp.StatusCode);
        }
    }

    /// <summary>Poll /confirms/{dealReference} for the order's final
    /// status. IG returns dealStatus = ACCEPTED / REJECTED and reason
    /// = the rejection cause.</summary>
    public async Task<IGOrderResult> ConfirmDealAsync(string dealReference, CancellationToken ct)
    {
        using var resp = await SendWithAuthAsync(
            HttpMethod.Get, $"confirms/{dealReference}", null, version: "1", ct);
        var text = await resp.Content.ReadAsStringAsync(ct);
        if (!resp.IsSuccessStatusCode)
        {
            return new IGOrderResult(
                DealReference: dealReference,
                Status: "UNKNOWN",
                StatusReason: text,
                HttpStatus: (int)resp.StatusCode);
        }
        try
        {
            using var doc = JsonDocument.Parse(text);
            var root = doc.RootElement;
            var dealStatus = root.TryGetProperty("dealStatus", out var ds) ? ds.GetString() : null;
            var reason = root.TryGetProperty("reason", out var rs) ? rs.GetString() : null;
            // IG's `reason` is frequently a useless "UNKNOWN" on rejection,
            // which left us unable to tell WHY (bad epic vs size vs market).
            // Enrich it with the epic + a compact raw-body snippet so the
            // OMS cancellation reason + logs are actually diagnosable. (The
            // observed pattern: USD-quote pairs fill, the rest reject — a
            // strong hint the non-USD-quote epics are wrong/untradeable.)
            if (string.Equals(dealStatus, "REJECTED", StringComparison.OrdinalIgnoreCase)
                && (string.IsNullOrWhiteSpace(reason) || reason == "UNKNOWN"))
            {
                var epic = root.TryGetProperty("epic", out var ep) ? ep.GetString() : null;
                var snippet = text.Length > 240 ? text[..240] : text;
                reason = $"{reason ?? "UNKNOWN"} (epic={epic ?? "?"}; raw={snippet})";
            }
            // IG /confirms returns `level` = the executed fill price on an
            // ACCEPTED deal. Capture it so the OMS records the REAL fill price
            // (was hardcoded 0 → zero cost basis → no unrealised P&L for FX +
            // intraday). null when absent (IG omits it on some instruments).
            decimal? level = root.TryGetProperty("level", out var lvl)
                && lvl.ValueKind == JsonValueKind.Number
                ? lvl.GetDecimal()
                : null;
            return new IGOrderResult(
                DealReference: dealReference,
                Status: dealStatus ?? "UNKNOWN",
                StatusReason: reason,
                HttpStatus: (int)resp.StatusCode,
                Level: level);
        }
        catch (Exception ex)
        {
            return new IGOrderResult(
                DealReference: dealReference,
                Status: "PARSE_ERROR",
                StatusReason: ex.Message,
                HttpStatus: (int)resp.StatusCode);
        }
    }

    // ─── Positions ─────────────────────────────────────────────

    /// <summary>GET /positions — all open positions on the account.
    /// Cached at a higher layer (mirrors the T212 positions cache
    /// pattern) so we don't hit IG every UI render.</summary>
    public async Task<IGPositionsResult> GetPositionsAsync(CancellationToken ct)
    {
        using var resp = await SendWithAuthAsync(
            HttpMethod.Get, "positions", null, version: "2", ct);
        var text = await resp.Content.ReadAsStringAsync(ct);
        if (!resp.IsSuccessStatusCode)
        {
            return new IGPositionsResult(
                Positions: Array.Empty<IGPosition>(),
                Error: text, HttpStatus: (int)resp.StatusCode);
        }
        try
        {
            var positions = new List<IGPosition>();
            using var doc = JsonDocument.Parse(text);
            if (doc.RootElement.TryGetProperty("positions", out var arr)
                && arr.ValueKind == JsonValueKind.Array)
            {
                foreach (var item in arr.EnumerateArray())
                {
                    var pos = item.TryGetProperty("position", out var p) ? p : default;
                    var market = item.TryGetProperty("market", out var m) ? m : default;
                    var epic = market.ValueKind == JsonValueKind.Object
                               && market.TryGetProperty("epic", out var e)
                        ? e.GetString() : null;
                    var instrumentName = market.ValueKind == JsonValueKind.Object
                                         && market.TryGetProperty("instrumentName", out var inm)
                        ? inm.GetString() : null;
                    var direction = pos.TryGetProperty("direction", out var d) ? d.GetString() : null;
                    var size = pos.TryGetProperty("size", out var sz) ? sz.GetDecimal() : 0m;
                    var level = pos.TryGetProperty("level", out var lv) ? lv.GetDecimal() : 0m;
                    var dealId = pos.TryGetProperty("dealId", out var di) ? di.GetString() : null;
                    // Current mark + lot size from the market block so the
                    // position can carry a live price + unrealised P&L — same
                    // uniform shape T212 emits, so the desk stays broker-agnostic.
                    decimal? Num(JsonElement el, string prop) =>
                        el.ValueKind == JsonValueKind.Object
                        && el.TryGetProperty(prop, out var v)
                        && v.ValueKind == JsonValueKind.Number
                            ? v.GetDecimal() : (decimal?)null;
                    positions.Add(new IGPosition(
                        Epic: epic ?? "?",
                        InstrumentName: instrumentName,
                        Direction: direction ?? "?",
                        Size: size,
                        EntryLevel: level,
                        DealId: dealId,
                        Bid: Num(market, "bid"),
                        Offer: Num(market, "offer"),
                        LotSize: Num(market, "lotSize")));
                }
            }
            return new IGPositionsResult(positions, Error: null, HttpStatus: (int)resp.StatusCode);
        }
        catch (Exception ex)
        {
            return new IGPositionsResult(
                Positions: Array.Empty<IGPosition>(),
                Error: ex.Message, HttpStatus: (int)resp.StatusCode);
        }
    }

    // ─── Cash / account ─────────────────────────────────────────

    /// <summary>GET /accounts — returns the configured account's
    /// available balance for sizing decisions.</summary>
    public async Task<IGCashResult> GetCashAsync(CancellationToken ct)
    {
        using var resp = await SendWithAuthAsync(
            HttpMethod.Get, "accounts", null, version: "1", ct);
        var text = await resp.Content.ReadAsStringAsync(ct);
        if (!resp.IsSuccessStatusCode)
        {
            return new IGCashResult(
                Available: null, Balance: null, Currency: null,
                Error: text, HttpStatus: (int)resp.StatusCode);
        }
        try
        {
            using var doc = JsonDocument.Parse(text);
            if (!doc.RootElement.TryGetProperty("accounts", out var accs)
                || accs.ValueKind != JsonValueKind.Array)
            {
                return new IGCashResult(null, null, null,
                    "no accounts array", (int)resp.StatusCode);
            }
            // Pick the configured account or first preferred one.
            JsonElement? target = null;
            foreach (var a in accs.EnumerateArray())
            {
                var id = a.TryGetProperty("accountId", out var ai) ? ai.GetString() : null;
                if (!string.IsNullOrEmpty(_options.AccountId)
                    && string.Equals(id, _options.AccountId, StringComparison.OrdinalIgnoreCase))
                {
                    target = a;
                    break;
                }
                if (a.TryGetProperty("preferred", out var pref)
                    && pref.ValueKind == JsonValueKind.True)
                {
                    target = a;
                }
            }
            target ??= accs[0];
            var bal = target.Value.TryGetProperty("balance", out var b) ? b : default;
            var available = bal.ValueKind == JsonValueKind.Object
                            && bal.TryGetProperty("available", out var av)
                ? av.GetDecimal() : (decimal?)null;
            var balance = bal.ValueKind == JsonValueKind.Object
                          && bal.TryGetProperty("balance", out var bv)
                ? bv.GetDecimal() : (decimal?)null;
            // IG reports its OWN account P&L + net deposit in the same block —
            // golden source for "are we making money", no derivation needed.
            var profitLoss = bal.ValueKind == JsonValueKind.Object
                             && bal.TryGetProperty("profitLoss", out var pl)
                             && pl.ValueKind == JsonValueKind.Number
                ? pl.GetDecimal() : (decimal?)null;
            var deposit = bal.ValueKind == JsonValueKind.Object
                          && bal.TryGetProperty("deposit", out var dp)
                          && dp.ValueKind == JsonValueKind.Number
                ? dp.GetDecimal() : (decimal?)null;
            var currency = target.Value.TryGetProperty("currency", out var c) ? c.GetString() : null;
            return new IGCashResult(
                Available: available, Balance: balance,
                Currency: currency, Error: null,
                HttpStatus: (int)resp.StatusCode,
                ProfitLoss: profitLoss, Deposit: deposit);
        }
        catch (Exception ex)
        {
            return new IGCashResult(null, null, null, ex.Message, (int)resp.StatusCode);
        }
    }

    // ─── Market snapshot (Phase F-2 — fill-quality capture) ───
    //
    // GET /markets/{epic} (v3) — returns a `snapshot` block with
    // top-of-book bid/offer + update timestamp. We use this right
    // before recording a fill so we can store realised-vs-mid bps
    // on oms_fills (Phase F-3 builds the empirical slippage model
    // from these rows).
    //
    // Defensive: on any failure (network, parse, market closed) we
    // return an IGSnapshotResult with bid/ask = null + the error
    // message + http status, so the caller can fall through to
    // RecordFill without the snapshot. Recording a fill without a
    // snapshot is strictly better than refusing to record it.
    public async Task<IGSnapshotResult> GetMarketSnapshotAsync(
        string epic, CancellationToken ct = default)
    {
        try
        {
            using var resp = await SendWithAuthAsync(
                HttpMethod.Get,
                $"markets/{Uri.EscapeDataString(epic)}",
                null, version: "3", ct);
            var text = await resp.Content.ReadAsStringAsync(ct);
            if (!resp.IsSuccessStatusCode)
            {
                return new IGSnapshotResult(
                    Bid: null, Ask: null, MarketStatus: null,
                    UpdateTime: null, Error: text, HttpStatus: (int)resp.StatusCode);
            }
            using var doc = JsonDocument.Parse(text);
            if (!doc.RootElement.TryGetProperty("snapshot", out var snap)
                || snap.ValueKind != JsonValueKind.Object)
            {
                return new IGSnapshotResult(
                    Bid: null, Ask: null, MarketStatus: null,
                    UpdateTime: null,
                    Error: "no snapshot block in response",
                    HttpStatus: (int)resp.StatusCode);
            }
            decimal? bid = snap.TryGetProperty("bid", out var b)
                && b.ValueKind == JsonValueKind.Number
                && b.TryGetDecimal(out var bv) ? bv : (decimal?)null;
            decimal? offer = snap.TryGetProperty("offer", out var o)
                && o.ValueKind == JsonValueKind.Number
                && o.TryGetDecimal(out var ov) ? ov : (decimal?)null;
            string? marketStatus = snap.TryGetProperty("marketStatus", out var ms)
                ? ms.GetString() : null;
            string? updateTime = snap.TryGetProperty("updateTime", out var ut)
                ? ut.GetString() : null;
            return new IGSnapshotResult(
                Bid: bid, Ask: offer, MarketStatus: marketStatus,
                UpdateTime: updateTime, Error: null,
                HttpStatus: (int)resp.StatusCode);
        }
        catch (Exception ex)
        {
            return new IGSnapshotResult(
                Bid: null, Ask: null, MarketStatus: null,
                UpdateTime: null, Error: ex.Message, HttpStatus: 0);
        }
    }

    // Contract size is STATIC per instrument, so cache process-wide. IGClient
    // is transient (DI), so this MUST be static — an instance cache would
    // re-fetch /markets every request (same class of bug as the /session flood
    // the singleton IGSessionCache fixed).
    private static readonly System.Collections.Concurrent.ConcurrentDictionary<string, decimal> _contractSizeCache = new();

    /// <summary>
    /// GET /markets/{epic} → instrument.contractSize (the units-per-contract
    /// used to scale P&amp;L to money). Broker-provided golden source — NOT a
    /// hardcoded constant. Cached process-wide (static value). Returns null if
    /// IG omits it; callers fall back so a missing size never zeroes a fill.
    /// </summary>
    public async Task<decimal?> GetContractSizeAsync(string epic, CancellationToken ct = default)
    {
        if (_contractSizeCache.TryGetValue(epic, out var cached)) return cached;
        try
        {
            using var resp = await SendWithAuthAsync(
                HttpMethod.Get, $"markets/{Uri.EscapeDataString(epic)}", null, version: "3", ct);
            if (!resp.IsSuccessStatusCode) return null;   // don't cache failures
            var text = await resp.Content.ReadAsStringAsync(ct);
            using var doc = JsonDocument.Parse(text);
            if (doc.RootElement.TryGetProperty("instrument", out var inst)
                && inst.ValueKind == JsonValueKind.Object
                && inst.TryGetProperty("contractSize", out var cs))
            {
                // IG reports contractSize as a STRING ("10000") or number.
                var raw = cs.ValueKind == JsonValueKind.String ? cs.GetString()
                          : cs.ValueKind == JsonValueKind.Number ? cs.GetRawText() : null;
                if (decimal.TryParse(raw, System.Globalization.NumberStyles.Any,
                        System.Globalization.CultureInfo.InvariantCulture, out var v) && v > 0)
                {
                    _contractSizeCache[epic] = v;
                    return v;
                }
            }
            return null;
        }
        catch { return null; }
    }

    // FX rates move, so cache with a short TTL (display P&L tolerates minutes).
    private static readonly System.Collections.Concurrent.ConcurrentDictionary<string, (decimal rate, DateTime at)> _usdRateCache = new();

    /// <summary>
    /// Multiplier to convert an amount in <paramref name="ccy"/> into USD,
    /// sourced from IG (golden source). FX P&amp;L is computed in the QUOTE
    /// currency; this converts it so a GBP/JPY position's JPY P&amp;L isn't shown
    /// as if it were dollars (the −$211 vs −$1 bug). Tries {CCY}USD then
    /// USD{CCY} so the market quoting convention is discovered, not hardcoded.
    /// Returns null when neither pair is found (caller leaves quote-ccy value).
    /// </summary>
    public async Task<decimal?> GetUsdRateAsync(string ccy, CancellationToken ct = default)
    {
        ccy = (ccy ?? "").ToUpperInvariant();
        if (ccy.Length != 3) return null;
        if (ccy == "USD") return 1m;
        if (_usdRateCache.TryGetValue(ccy, out var c) && (DateTime.UtcNow - c.at).TotalMinutes < 10)
            return c.rate;
        // {CCY}USD: 1 unit of CCY = mid USD (e.g. GBPUSD ⇒ GBP→USD = 1.27).
        var direct = await GetMarketSnapshotAsync($"CS.D.{ccy}USD.MINI.IP", ct);
        if (direct.Bid is decimal db && direct.Ask is decimal da && db + da > 0)
        {
            var r = (db + da) / 2m;
            _usdRateCache[ccy] = (r, DateTime.UtcNow);
            return r;
        }
        // USD{CCY}: 1 USD = mid CCY ⇒ CCY→USD = 1/mid (e.g. USDJPY 159.9 ⇒ JPY→USD).
        var inv = await GetMarketSnapshotAsync($"CS.D.USD{ccy}.MINI.IP", ct);
        if (inv.Bid is decimal ib && inv.Ask is decimal ia && ib + ia > 0)
        {
            var r = 2m / (ib + ia);
            _usdRateCache[ccy] = (r, DateTime.UtcNow);
            return r;
        }
        return null;
    }

    // ─── Market search ─────────────────────────────────────────

    /// <summary>GET /markets?searchTerm=&lt;term&gt; — discover EPICs for a
    /// symbol like "EURUSD". Returns the top matches with epic +
    /// instrument name + type so the operator can pick the right one
    /// to wire into broker_ticker_map / ig_epic_map.json.</summary>
    public async Task<IGMarketSearchResult> SearchMarketsAsync(
        string searchTerm, CancellationToken ct)
    {
        using var resp = await SendWithAuthAsync(
            HttpMethod.Get,
            $"markets?searchTerm={Uri.EscapeDataString(searchTerm)}",
            null, version: "1", ct);
        var text = await resp.Content.ReadAsStringAsync(ct);
        if (!resp.IsSuccessStatusCode)
        {
            return new IGMarketSearchResult(
                Array.Empty<IGMarketMatch>(), text, (int)resp.StatusCode);
        }
        try
        {
            var matches = new List<IGMarketMatch>();
            using var doc = JsonDocument.Parse(text);
            if (doc.RootElement.TryGetProperty("markets", out var arr)
                && arr.ValueKind == JsonValueKind.Array)
            {
                foreach (var m in arr.EnumerateArray())
                {
                    matches.Add(new IGMarketMatch(
                        Epic: m.TryGetProperty("epic", out var e) ? e.GetString() ?? "" : "",
                        InstrumentName: m.TryGetProperty("instrumentName", out var n) ? n.GetString() : null,
                        InstrumentType: m.TryGetProperty("instrumentType", out var t) ? t.GetString() : null,
                        ExpiryString: m.TryGetProperty("expiry", out var x) ? x.GetString() : null,
                        MarketStatus: m.TryGetProperty("marketStatus", out var s) ? s.GetString() : null));
                }
            }
            return new IGMarketSearchResult(matches, null, (int)resp.StatusCode);
        }
        catch (Exception ex)
        {
            return new IGMarketSearchResult(
                Array.Empty<IGMarketMatch>(), ex.Message, (int)resp.StatusCode);
        }
    }

    // ─── Historical prices ──────────────────────────────────────
    //
    // GET /prices/{epic}?resolution=&from=&to=&max= — multi-resolution
    // historical bars. The endpoint counts each bar against the
    // demo account's weekly allowance (typically 10k datapoints/week);
    // exceeding it returns 403 with errorCode = exceeded.allowance,
    // which we surface as HttpStatus 403 + Error so callers can
    // back off + try yfinance for the same range.
    //
    // Resolutions accepted: MINUTE / MINUTE_2 / MINUTE_3 / MINUTE_5 /
    // MINUTE_10 / MINUTE_15 / MINUTE_30 / HOUR / HOUR_2 / HOUR_3 /
    // HOUR_4 / DAY / WEEK / MONTH. The Python BarStore maps the
    // canonical resolutions (1m / 5m / 1h / 1d) to IG's strings.
    //
    // The response shape varies between API versions; we use v3 which
    // returns ``prices: [{ snapshotTime, openPrice {bid, ask, lastTraded}, ... }]``.
    // For US equity bars (Phase B-4 first use) we take lastTraded as
    // OHLC and lastTradedVolume as the volume.

    /// <summary>GET /prices/{epic} — historical bars at a resolution
    /// over a date range. ``from`` and ``to`` are ISO 8601 strings;
    /// ``max`` caps the response count (IG default 10, we send a
    /// large enough cap to cover a typical month of 1m bars). Returns
    /// the bars + the consumed allowance count from the response
    /// metadata.</summary>
    public async Task<IGPricesResult> GetPricesAsync(
        string epic, string resolution, string from, string to,
        int max = 5000,
        CancellationToken ct = default)
    {
        if (!IsEnabled)
        {
            return new IGPricesResult(
                Bars: Array.Empty<IGPriceBar>(),
                AllowanceRemaining: null, AllowanceTotal: null,
                Error: "IG disabled", HttpStatus: 503);
        }

        // Use v3 explicitly — earlier versions return a different
        // shape and lose the allowance metadata we want for telemetry.
        var path = $"prices/{Uri.EscapeDataString(epic)}" +
                   $"?resolution={Uri.EscapeDataString(resolution)}" +
                   $"&from={Uri.EscapeDataString(from)}" +
                   $"&to={Uri.EscapeDataString(to)}" +
                   $"&max={max}";
        using var resp = await SendWithAuthAsync(
            HttpMethod.Get, path, null, version: "3", ct);
        var text = await resp.Content.ReadAsStringAsync(ct);
        if (!resp.IsSuccessStatusCode)
        {
            return new IGPricesResult(
                Bars: Array.Empty<IGPriceBar>(),
                AllowanceRemaining: null, AllowanceTotal: null,
                Error: text, HttpStatus: (int)resp.StatusCode);
        }
        try
        {
            using var doc = JsonDocument.Parse(text);
            var root = doc.RootElement;
            // Allowance — best-effort; missing fields stay null.
            int? remaining = null;
            int? total = null;
            if (root.TryGetProperty("allowance", out var allowance))
            {
                if (allowance.TryGetProperty("remainingAllowance", out var ra))
                    remaining = ra.GetInt32();
                if (allowance.TryGetProperty("totalAllowance", out var ta))
                    total = ta.GetInt32();
            }
            var bars = new List<IGPriceBar>();
            if (root.TryGetProperty("prices", out var prices)
                && prices.ValueKind == JsonValueKind.Array)
            {
                foreach (var p in prices.EnumerateArray())
                {
                    bars.Add(ParseBar(p));
                }
            }
            return new IGPricesResult(
                Bars: bars,
                AllowanceRemaining: remaining,
                AllowanceTotal: total,
                Error: null,
                HttpStatus: (int)resp.StatusCode);
        }
        catch (Exception ex)
        {
            return new IGPricesResult(
                Bars: Array.Empty<IGPriceBar>(),
                AllowanceRemaining: null, AllowanceTotal: null,
                Error: ex.Message, HttpStatus: (int)resp.StatusCode);
        }
    }

    private static IGPriceBar ParseBar(JsonElement p)
    {
        // Each price has openPrice / closePrice / highPrice / lowPrice
        // as objects { bid, ask, lastTraded } — we take lastTraded for
        // equity bars (the right call for FX would be mid; that comes
        // later when fx_spot is a real asset class).
        decimal? Pick(string prop, string field)
        {
            if (p.TryGetProperty(prop, out var obj)
                && obj.ValueKind == JsonValueKind.Object
                && obj.TryGetProperty(field, out var v)
                && v.ValueKind == JsonValueKind.Number)
                return v.GetDecimal();
            return null;
        }
        var snapshotTime = p.TryGetProperty("snapshotTime", out var t)
            ? t.GetString() : null;
        var openLast = Pick("openPrice", "lastTraded");
        var highLast = Pick("highPrice", "lastTraded");
        var lowLast = Pick("lowPrice", "lastTraded");
        var closeLast = Pick("closePrice", "lastTraded");
        long? volume = null;
        if (p.TryGetProperty("lastTradedVolume", out var vol)
            && vol.ValueKind == JsonValueKind.Number)
        {
            try { volume = vol.GetInt64(); }
            catch { volume = (long)vol.GetDouble(); }
        }
        return new IGPriceBar(
            SnapshotTime: snapshotTime ?? "",
            Open: openLast ?? 0m,
            High: highLast ?? 0m,
            Low: lowLast ?? 0m,
            Close: closeLast ?? 0m,
            Volume: volume ?? 0L);
    }
}

// ─── DTOs ──────────────────────────────────────────────────────

public sealed record IGOrderResult(
    string? DealReference,
    string Status,            // ACCEPTED / REJECTED / UNKNOWN / PARSE_ERROR
    string? StatusReason,
    int HttpStatus,
    decimal? Level = null);   // executed fill price from /confirms (null if absent)

public sealed record IGPosition(
    string Epic,
    string? InstrumentName,
    string Direction,         // BUY / SELL
    decimal Size,
    decimal EntryLevel,
    string? DealId,
    decimal? Bid = null,      // current market bid (from /positions market block)
    decimal? Offer = null,    // current market offer
    decimal? LotSize = null); // contract/lot size — P&L scale (broker-provided)

public sealed record IGPositionsResult(
    IReadOnlyList<IGPosition> Positions,
    string? Error,
    int HttpStatus);

public sealed record IGCashResult(
    decimal? Available,
    decimal? Balance,
    string? Currency,
    string? Error,
    int HttpStatus,
    decimal? ProfitLoss = null,   // IG's OWN running account P&L (open positions) — golden source
    decimal? Deposit = null);     // net deposited; net-since-start = (balance + profitLoss) − deposit

public sealed record IGMarketMatch(
    string Epic,
    string? InstrumentName,
    string? InstrumentType,    // CURRENCIES / SHARES / INDICES / COMMODITIES …
    string? ExpiryString,
    string? MarketStatus);     // TRADEABLE / EDITS_ONLY / OFFLINE …

public sealed record IGMarketSearchResult(
    IReadOnlyList<IGMarketMatch> Matches,
    string? Error,
    int HttpStatus);

// Phase F-2 — top-of-book snapshot, captured around fill time so we
// can stamp realised-vs-mid bps on oms_fills. Nullable bid/ask let
// the caller proceed without a snapshot when the market is closed
// or the fetch threw — recording a fill without bps data is still
// better than not recording the fill.
public sealed record IGSnapshotResult(
    decimal? Bid,
    decimal? Ask,
    string? MarketStatus,          // TRADEABLE / EDITS_ONLY / OFFLINE ...
    string? UpdateTime,            // IG's local timestamp string
    string? Error,
    int HttpStatus);

public sealed record IGPriceBar(
    string SnapshotTime,         // "2024-12-23T14:30:00" — IG's local TZ; UTC when account is set so
    decimal Open,
    decimal High,
    decimal Low,
    decimal Close,
    long Volume);

public sealed record IGPricesResult(
    IReadOnlyList<IGPriceBar> Bars,
    int? AllowanceRemaining,     // datapoints left this week (demo cap ~10k)
    int? AllowanceTotal,
    string? Error,
    int HttpStatus);

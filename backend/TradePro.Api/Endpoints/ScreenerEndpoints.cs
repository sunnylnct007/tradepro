using System.Diagnostics;
using System.Text;
using System.Text.Json;
using TradePro.Api.Providers.IBKR;

namespace TradePro.Api.Endpoints;

/// <summary>
/// POST /api/screener/run  — trigger the daily wheel + swing screener from the UI.
///
/// Uses the backend IBKR client (already authenticated, persistent session) to
/// fetch price history for each ticker, builds the JSON input file the Python
/// daily_run.py expects, executes it as a subprocess, and returns the result.
/// Snapshot fields (IVP, div yield, etc.) are fetched from IBKR's REST snapshot
/// endpoint; if IBKR is disabled the endpoint falls back to empty snapshots so
/// the screener still runs with whatever history is available.
/// </summary>
public static class ScreenerEndpoints
{
    // IBKR snapshot field codes needed by the Python screener
    private const string SnapshotFields = "31,7293,7294,7282,7283,7631,7286,87,7718";

    // Tickers with conids — the canonical screener universe
    private static readonly (string Ticker, long ConId)[] Universe =
    [
        ("SPY",  756733),
        ("MO",   9769),
        ("BAC",  10098),
        ("T",    37018770),
        ("ACN",  67889930),
        ("KMI",  83975037),
        ("NVDA", 4815747),
        ("PFE",  11031),
        ("WFC",  10375),
        ("APLD", 556067819),
        ("MSFT", 272093),
        ("TSLA", 76792991),
        ("AAPL", 265598),
        ("JPM",  1520593),
        ("GS",   4627828),
        ("MS",   2841574),
        ("XOM",  895178251),
        ("CVX",  5684),
        ("C",    87335484),
        ("OXY",  10880),
        ("VZ",   4901),
        ("CSCO", 268084),
        ("GOOG", 208813720),
        ("META", 107113386),
        ("UBER", 365207014),
        ("INTC", 270639),
        ("F",    9599491),
        ("GM",   80986742),
        ("DIS",  6459),
        ("HOOD", 504546674),
    ];

    public static IEndpointRouteBuilder MapScreenerEndpoints(this IEndpointRouteBuilder app)
    {
        var g = app.MapGroup("/screener").WithTags("Screener");

        g.MapPost("/run", async (
            IBKRClient ibkr,
            ILogger<ScreenerRunRequest> log,
            CancellationToken ct) =>
        {
            log.LogInformation("Screener run triggered via API");
            try
            {
            var runDate = DateTime.UtcNow.ToString("yyyy-MM-dd");
            var stocks = new Dictionary<string, object>();

            // SPY history for regime detection (unguarded before — a single IBKR
            // hiccup here threw straight to a bare 500 with no diagnosable detail).
            var spyHistory = await FetchHistoryDict(ibkr, "SPY", 756733, log, ct);

            foreach (var (ticker, conid) in Universe)
            {
                try
                {
                    var snapshot = await FetchSnapshotDict(ibkr, conid, log, ct);
                    var history = ticker == "SPY"
                        ? spyHistory
                        : await FetchHistoryDict(ibkr, ticker, conid, log, ct);

                    stocks[ticker] = new
                    {
                        conid,
                        snapshot,
                        history,
                        earnings_date = (string?)null,
                    };
                    log.LogInformation("Screener: fetched {Ticker}", ticker);
                }
                catch (Exception ex)
                {
                    log.LogWarning("Screener: failed {Ticker}: {Err}", ticker, ex.Message);
                    stocks[ticker] = new { conid, snapshot = new { }, history = new { }, earnings_date = (string?)null };
                }
            }

            var payload = new { run_date = runDate, spy_history = spyHistory, stocks };
            var json = JsonSerializer.Serialize(payload, new JsonSerializerOptions { WriteIndented = false });

            // Write temp input file. UTF-8 WITHOUT a BOM: Encoding.UTF8 emits a
            // byte-order mark, and daily_run.py's json.load chokes on it
            // ("Unexpected UTF-8 BOM"). JSON must not carry a BOM.
            var inputFile = Path.Combine(Path.GetTempPath(), $"screener_{runDate}.json");
            await File.WriteAllTextAsync(inputFile, json, new UTF8Encoding(false), ct);

            // Locate daily_run.py relative to this assembly
            var screenerScript = FindScreenerScript();
            if (screenerScript is null)
                return Results.Problem("screener/daily_run.py not found on the server");

            // Use venv python if available (Docker image sets SCREENER_PYTHON env var)
            var python = Environment.GetEnvironmentVariable("SCREENER_PYTHON") ?? "python3";
            var psi = new ProcessStartInfo(python, $"\"{screenerScript}\" --input-file \"{inputFile}\"")
            {
                RedirectStandardOutput = true,
                RedirectStandardError  = true,
                UseShellExecute        = false,
                WorkingDirectory       = Path.GetDirectoryName(screenerScript)!,
            };
            using var proc = Process.Start(psi)!;
            var stdout = await proc.StandardOutput.ReadToEndAsync(ct);
            var stderr = await proc.StandardError.ReadToEndAsync(ct);
            await proc.WaitForExitAsync(ct);

            log.LogInformation("Screener exit={Exit} stderr={Err}", proc.ExitCode, stderr[..Math.Min(500, stderr.Length)]);

            // Parse the last JSON line from stdout
            var resultLine = stdout.Trim().Split('\n').LastOrDefault(l => l.TrimStart().StartsWith('{')) ?? "{}";
            try
            {
                var result = JsonSerializer.Deserialize<JsonElement>(resultLine);
                return Results.Ok(new { ok = true, result, stderr = stderr[..Math.Min(5000, stderr.Length)] });
            }
            catch
            {
                return Results.Ok(new { ok = false, stdout, stderr = stderr[..Math.Min(5000, stderr.Length)] });
            }
            }
            catch (Exception ex)
            {
                // Fail-loud AND diagnosable: return the real error as a 200 with
                // ok:false (same shape as the other return paths). A Problem(500)
                // is stripped to an empty body by the front nginx
                // (proxy_intercept_errors), so the portal only ever saw "500 :" —
                // undiagnosable. 200 lets the actual exception reach the caller.
                log.LogError(ex, "Screener run failed");
                return Results.Ok(new
                {
                    ok = false,
                    error = ex.Message,
                    type = ex.GetType().Name,
                    stack = (ex.StackTrace ?? "").Split('\n').Take(8).ToArray(),
                    inner = ex.InnerException?.Message,
                });
            }
        });

        // GET /api/screener/live — real-time snapshot for all universe tickers
        g.MapGet("/live", async (IBKRClient ibkr, ILogger<ScreenerRunRequest> log, CancellationToken ct) =>
        {
            // Fields: 31=last, 7293=52wHigh, 7294=52wLow, 7282=IVP(52w), 7283=IV-annual,
            //         7631=HV30, 7286=divYield, 87=avgVol90d, 82=change%, 83=change$
            const string LiveFields = "31,7293,7294,7282,7283,7631,7286,87,82,83";

            var rows = new List<object>();
            foreach (var (ticker, conid) in Universe)
            {
                try
                {
                    var raw = await ibkr.GetSnapshotRawAsync(conid, LiveFields, ct);
                    if (string.IsNullOrWhiteSpace(raw)) continue;

                    using var doc = JsonDocument.Parse(raw);
                    var root = doc.RootElement;
                    var elem = root.ValueKind == JsonValueKind.Array
                        ? root.EnumerateArray().FirstOrDefault()
                        : root;
                    if (elem.ValueKind != JsonValueKind.Object) continue;

                    static double? N(JsonElement e, string key)
                    {
                        if (!e.TryGetProperty(key, out var v)) return null;
                        if (v.ValueKind == JsonValueKind.Number) return v.GetDouble();
                        if (v.ValueKind == JsonValueKind.String && double.TryParse(v.GetString(), out var d)) return d;
                        return null;
                    }

                    var price    = N(elem, "31");
                    var high52w  = N(elem, "7293");
                    var low52w   = N(elem, "7294");
                    var ivp52w   = N(elem, "7282");   // 0-100
                    var ivAnn    = N(elem, "7283");   // 0-100
                    var hv30     = N(elem, "7631");   // 0-100
                    var divYld   = N(elem, "7286");
                    var avgVol   = N(elem, "87");
                    var changePct = N(elem, "82");
                    var changeAbs = N(elem, "83");

                    // BS ATM put premium estimate: ~0.4 × σ × √(30/252) × S
                    double? putYieldPct = null;
                    if (ivAnn.HasValue && price.HasValue && ivAnn > 0 && price > 0)
                    {
                        var sigma = ivAnn.Value / 100.0;
                        putYieldPct = Math.Round(0.4 * sigma * Math.Sqrt(30.0 / 252.0) * price.Value / price.Value * 100, 2);
                    }

                    // Distance from 52w low (upside buffer)
                    double? distLowPct = null;
                    if (price.HasValue && low52w.HasValue && low52w > 0)
                        distLowPct = Math.Round((price.Value - low52w.Value) / low52w.Value * 100, 1);

                    rows.Add(new
                    {
                        ticker,
                        price,
                        change_pct = changePct,
                        change_abs = changeAbs,
                        ivp_52w = ivp52w,
                        iv_annual = ivAnn,
                        hv30,
                        div_yield = divYld,
                        put_yield_pct = putYieldPct,
                        high_52w = high52w,
                        low_52w = low52w,
                        dist_low_pct = distLowPct,
                        avg_vol_90d = avgVol,
                    });
                }
                catch (Exception ex)
                {
                    log.LogWarning("Live scan: {T} failed: {E}", ticker, ex.Message);
                }
            }

            return Results.Ok(new { fetched_at_utc = DateTime.UtcNow.ToString("o"), rows });
        });

        return app;
    }

    private static async Task<Dictionary<string, object>> FetchSnapshotDict(
        IBKRClient ibkr, long conid, ILogger log, CancellationToken ct)
    {
        var raw = await ibkr.GetSnapshotRawAsync(conid, SnapshotFields, ct);
        if (string.IsNullOrWhiteSpace(raw)) return [];

        try
        {
            using var doc = JsonDocument.Parse(raw);
            var root = doc.RootElement;
            // IBKR returns an array; take the first element matching our conid
            var elem = root.ValueKind == JsonValueKind.Array
                ? root.EnumerateArray().FirstOrDefault()
                : root;

            if (elem.ValueKind != JsonValueKind.Object) return [];

            static double Num(JsonElement e, string key, double def = 0.0)
            {
                if (!e.TryGetProperty(key, out var v)) return def;
                if (v.ValueKind == JsonValueKind.Number) return v.GetDouble();
                if (v.ValueKind == JsonValueKind.String && double.TryParse(v.GetString(), out var d)) return d;
                return def;
            }

            var last   = Num(elem, "31");
            var h52w   = Num(elem, "7293");
            var l52w   = Num(elem, "7294");
            var ivPct  = Num(elem, "7282") / 100.0; // IBKR returns 0-100, we need fraction
            var ivAnn  = Num(elem, "7283") / 100.0;
            var hvAnn  = Num(elem, "7631") / 100.0;
            var divYld = Num(elem, "7286");
            var avgVol = Num(elem, "87");   // avg daily USD volume

            return new Dictionary<string, object>
            {
                ["last"]                          = new { price = last },
                ["misc-statistics"]               = new { high_52w = h52w, low_52w = l52w },
                ["implied-volatility-percentile"] = new { high_52w = ivPct },
                ["implied-vol-underlying"]        = new { annual_iv = ivAnn, is_valid = ivAnn > 0 },
                ["historical-vol"]                = new { annual_pct = hvAnn },
                ["dividend-yield"]                = new { yield_pct = divYld },
                // field 87 = avg daily SHARE volume; multiply by price → USD volume
                // so Python's snapshot_to_fields (which divides back by price) recovers shares
                ["avg-90d-usd-volume"]            = new { volume = avgVol * last },
                ["underlying-avg-option-volume"]  = new { avgCallVolume = 0, avgPutVolume = 0 },
            };
        }
        catch (Exception ex)
        {
            log.LogWarning("Snapshot parse failed for conid {C}: {E}", conid, ex.Message);
            return [];
        }
    }

    private static async Task<Dictionary<string, object>> FetchHistoryDict(
        IBKRClient ibkr, string symbol, long conid, ILogger log, CancellationToken ct)
    {
        // 1Y daily bars — enough for MA200 + RSI + HV30
        var result = await ibkr.GetPriceHistoryAsync(symbol, "1y", "1d", ct);
        if (result.Bars.Count == 0)
        {
            if (result.Error is not null)
                log.LogWarning("History empty for {Sym}: {Err}", symbol, result.Error);
            return [];
        }

        var times   = result.Bars.Select(b => b.TimeMs).ToArray();
        var opens   = result.Bars.Select(b => (double)b.Open).ToArray();
        var highs   = result.Bars.Select(b => (double)b.High).ToArray();
        var lows    = result.Bars.Select(b => (double)b.Low).ToArray();
        var closes  = result.Bars.Select(b => (double)b.Close).ToArray();
        var volumes = result.Bars.Select(b => (double)b.Volume).ToArray();

        return new Dictionary<string, object>
        {
            ["time"]   = times,
            ["open"]   = opens,
            ["high"]   = highs,
            ["low"]    = lows,
            ["close"]  = closes,
            ["volume"] = volumes,
        };
    }

    private static string? FindScreenerScript()
    {
        // Try paths relative to the assembly location
        var asm = AppContext.BaseDirectory;
        var candidates = new[]
        {
            "/screener/daily_run.py",                                          // Docker image path
            Path.Combine(asm, "../../../../screener/daily_run.py"),
            Path.Combine(asm, "../../../../../screener/daily_run.py"),
            "/home/user/tradepro/screener/daily_run.py",
        };
        return candidates.FirstOrDefault(File.Exists);
    }

    private sealed record ScreenerRunRequest;
}

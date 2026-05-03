using System.Collections.Concurrent;
using System.Text.Json;
using TradePro.Api.Models;

namespace TradePro.Api.Simulation;

/// File-backed implementation of <see cref="ICompareStore"/>. Each universe
/// is stored as `<root>/<universe>.json` containing the raw payload plus a
/// small metadata header — universe, runId, generatedAt, receivedAt,
/// rankMetric, rowCount. On startup the store hydrates itself from disk so
/// API restarts and deploys don't lose data.
///
/// Design notes:
/// - One file per universe, not one big index. Trivial to inspect, diff,
///   delete, or back up. Easy to rsync to another machine.
/// - Atomic writes: write to `<file>.tmp`, fsync, rename. Avoids torn JSON
///   if the process is killed mid-write.
/// - Thread safety via a single ConcurrentDictionary. Disk writes happen
///   under a per-universe lock so concurrent ingests for the same universe
///   serialise without blocking other universes.
/// - Concurrency model assumes a single API replica. If we ever scale out,
///   move to Firestore (Phase 4 alternative noted in ROADMAP).
public sealed class FileCompareStore : ICompareStore
{
    private static readonly JsonSerializerOptions JsonOpts = new()
    {
        WriteIndented = false,
        DefaultIgnoreCondition = System.Text.Json.Serialization.JsonIgnoreCondition.WhenWritingNull,
    };

    private readonly string _root;
    private readonly ILogger<FileCompareStore> _logger;
    private readonly ConcurrentDictionary<string, CompareEnvelope> _byUniverse = new();
    private readonly ConcurrentDictionary<string, object> _writeLocks = new();

    public FileCompareStore(IConfiguration config, ILogger<FileCompareStore> logger)
    {
        _logger = logger;
        // Default to a path that's always writable so the API never fails to
        // start over a missing volume mount. Operators set Compare:StorePath
        // (env Compare__StorePath) for a durable location:
        //   - compose dev: /data/compare (named volume)
        //   - Azure App Service: /home/data/compare (persistent storage)
        //   - AWS Fargate: an EFS mount
        var configured = config["Compare:StorePath"];
        _root = !string.IsNullOrWhiteSpace(configured)
            ? configured
            : Path.Combine(Path.GetTempPath(), "tradepro-compare");

        try
        {
            Directory.CreateDirectory(_root);
        }
        catch (Exception ex)
        {
            _logger.LogError(ex,
                "Failed to create compare cache directory {Root}; falling back to temp",
                _root);
            _root = Path.Combine(Path.GetTempPath(), "tradepro-compare");
            Directory.CreateDirectory(_root);
        }

        Hydrate();
    }

    public CompareEnvelope Put(JsonElement payload)
    {
        var universe = ReadString(payload, "universe") ?? "custom";
        var runId = ReadString(payload, "run_id");
        var rankMetric = ReadString(payload, "rank_metric");
        var generatedAt = ReadDate(payload, "generated_at") ?? DateTime.UtcNow;
        var rowCount = ReadArrayLength(payload, "rows");

        var envelope = new CompareEnvelope(
            Universe: universe,
            RunId: runId,
            GeneratedAtUtc: generatedAt,
            ReceivedAtUtc: DateTime.UtcNow,
            RankMetric: rankMetric,
            RowCount: rowCount,
            // Clone — request-body JsonDocument is owned by the framework.
            Payload: payload.Clone());

        var lockObj = _writeLocks.GetOrAdd(universe, _ => new object());
        lock (lockObj)
        {
            _byUniverse[universe] = envelope;
            WriteToDisk(envelope);
        }
        return envelope;
    }

    public CompareEnvelope? GetLatest(string universe)
        => _byUniverse.TryGetValue(universe, out var env) ? env : null;

    public IReadOnlyList<CompareSummary> ListUniverses()
        => _byUniverse.Values
            .Select(e => new CompareSummary(
                e.Universe, e.RunId, e.GeneratedAtUtc, e.ReceivedAtUtc,
                e.RankMetric, e.RowCount))
            .OrderByDescending(s => s.GeneratedAtUtc)
            .ToArray();

    private void Hydrate()
    {
        try
        {
            foreach (var file in Directory.EnumerateFiles(_root, "*.json"))
            {
                try
                {
                    var raw = File.ReadAllText(file);
                    using var doc = JsonDocument.Parse(raw);
                    var root = doc.RootElement;
                    if (root.ValueKind != JsonValueKind.Object) continue;

                    // A genuine compare payload always has either an
                    // explicit `universe` string OR an inner `payload`
                    // object with one + a top-level `kind` of 'compare'.
                    // Skip anything else — settings.json, ad-hoc files
                    // an operator dropped in, etc — to avoid surfacing
                    // ghost universes on /health/details.
                    var hasUniverse = root.TryGetProperty("universe", out var uvRaw)
                                      && uvRaw.ValueKind == JsonValueKind.String;
                    var hasPayloadUniverse = root.TryGetProperty("payload", out var pl)
                                             && pl.ValueKind == JsonValueKind.Object
                                             && pl.TryGetProperty("universe", out var pluv)
                                             && pluv.ValueKind == JsonValueKind.String;
                    if (!hasUniverse && !hasPayloadUniverse)
                    {
                        _logger.LogInformation(
                            "Skipping {File} — not a compare payload (no universe field).",
                            Path.GetFileName(file));
                        continue;
                    }

                    var universe = ReadString(root, "universe") ?? Path.GetFileNameWithoutExtension(file);
                    var runId = ReadString(root, "runId") ?? ReadString(root, "run_id");
                    var generatedAt = ReadDate(root, "generatedAtUtc")
                        ?? ReadDate(root, "generated_at")
                        ?? File.GetLastWriteTimeUtc(file);
                    var receivedAt = ReadDate(root, "receivedAtUtc")
                        ?? File.GetLastWriteTimeUtc(file);
                    var rankMetric = ReadString(root, "rankMetric") ?? ReadString(root, "rank_metric");

                    if (!root.TryGetProperty("payload", out var payload))
                    {
                        // Backwards-compat: treat the whole file as the payload.
                        payload = root;
                    }
                    var rowCount = ReadArrayLength(payload, "rows");

                    var env = new CompareEnvelope(
                        universe, runId, generatedAt, receivedAt,
                        rankMetric, rowCount, payload.Clone());
                    _byUniverse[universe] = env;
                }
                catch (Exception ex)
                {
                    _logger.LogWarning(ex, "Failed to hydrate compare cache file {File}", file);
                }
            }
            _logger.LogInformation("Hydrated FileCompareStore with {Count} universe(s) from {Root}",
                _byUniverse.Count, _root);
        }
        catch (Exception ex)
        {
            _logger.LogError(ex, "Failed to hydrate FileCompareStore from {Root}", _root);
        }
    }

    private void WriteToDisk(CompareEnvelope env)
    {
        var safe = SafeUniverse(env.Universe);
        var path = Path.Combine(_root, $"{safe}.json");
        var tmp = path + ".tmp";

        // Persisted shape mirrors what GET /api/compare/latest returns —
        // makes the disk format directly inspectable without a converter.
        var record = new
        {
            universe = env.Universe,
            runId = env.RunId,
            generatedAtUtc = env.GeneratedAtUtc,
            receivedAtUtc = env.ReceivedAtUtc,
            rankMetric = env.RankMetric,
            rowCount = env.RowCount,
            payload = env.Payload,
        };

        try
        {
            using (var fs = File.Create(tmp))
            {
                JsonSerializer.Serialize(fs, record, JsonOpts);
                fs.Flush(true); // fsync
            }
            File.Move(tmp, path, overwrite: true);
        }
        catch (Exception ex)
        {
            _logger.LogError(ex, "Failed to persist compare envelope for {Universe} to {Path}",
                env.Universe, path);
            // Best-effort cleanup of the half-written tmp file.
            try { if (File.Exists(tmp)) File.Delete(tmp); } catch { /* ignore */ }
        }
    }

    private static string SafeUniverse(string u)
    {
        // Restrict to characters that are safe on every filesystem we deploy
        // on (Linux App Service + macOS local). Defensive: the universe name
        // comes from a Mac-side push, but a typo shouldn't break the store.
        var chars = u.Select(c => char.IsLetterOrDigit(c) || c == '_' || c == '-' ? c : '_');
        return new string(chars.ToArray());
    }

    private static string? ReadString(JsonElement el, string key)
        => el.ValueKind == JsonValueKind.Object
            && el.TryGetProperty(key, out var v)
            && v.ValueKind == JsonValueKind.String
                ? v.GetString()
                : null;

    private static DateTime? ReadDate(JsonElement el, string key)
    {
        if (el.ValueKind != JsonValueKind.Object) return null;
        if (!el.TryGetProperty(key, out var v) || v.ValueKind != JsonValueKind.String) return null;
        var s = v.GetString();
        if (string.IsNullOrEmpty(s)) return null;
        return DateTime.TryParse(s, null,
            System.Globalization.DateTimeStyles.AdjustToUniversal | System.Globalization.DateTimeStyles.AssumeUniversal,
            out var dt) ? dt : null;
    }

    private static int ReadArrayLength(JsonElement el, string key)
        => el.ValueKind == JsonValueKind.Object
            && el.TryGetProperty(key, out var v)
            && v.ValueKind == JsonValueKind.Array
                ? v.GetArrayLength()
                : 0;
}

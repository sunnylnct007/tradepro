using System.Text.Json;
using TradePro.Api.Models;

namespace TradePro.Api.Simulation;

public interface ISettingsStore
{
    AppSettings Get();
    AppSettings Update(AppSettings incoming);
}

/// File-backed settings, atomic-rename writes. Settings rarely change
/// (a user tweaks thresholds maybe once a week) so contention isn't an
/// issue; a single in-memory cache + lock is plenty.
public sealed class FileSettingsStore : ISettingsStore
{
    private readonly string _path;
    private readonly ILogger<FileSettingsStore> _logger;
    private readonly object _lock = new();
    private AppSettings _cached;

    public FileSettingsStore(IConfiguration config, ILogger<FileSettingsStore> logger)
    {
        _logger = logger;
        var root = config["Compare:StorePath"];
        if (string.IsNullOrWhiteSpace(root))
        {
            root = Path.Combine(Path.GetTempPath(), "tradepro-compare");
        }
        Directory.CreateDirectory(root);
        _path = Path.Combine(root, "settings.json");
        _cached = LoadOrDefault();
    }

    public AppSettings Get()
    {
        lock (_lock) return _cached;
    }

    public AppSettings Update(AppSettings incoming)
    {
        // Server stamps UpdatedAtUtc — clients can't lie about when
        // the change happened.
        var stamped = incoming with { UpdatedAtUtc = DateTime.UtcNow };
        lock (_lock)
        {
            WriteToDisk(stamped);
            _cached = stamped;
        }
        return stamped;
    }

    private AppSettings LoadOrDefault()
    {
        if (!File.Exists(_path))
        {
            var d = AppSettingsDefaults.Build();
            try { WriteToDisk(d); } catch { /* tolerate */ }
            return d;
        }
        try
        {
            var raw = File.ReadAllText(_path);
            var parsed = JsonSerializer.Deserialize<AppSettings>(raw, JsonOpts);
            return parsed ?? AppSettingsDefaults.Build();
        }
        catch (Exception ex)
        {
            _logger.LogWarning(ex, "Failed to read settings file {Path}; using defaults", _path);
            return AppSettingsDefaults.Build();
        }
    }

    private void WriteToDisk(AppSettings s)
    {
        var tmp = _path + ".tmp";
        using (var fs = File.Create(tmp))
        {
            JsonSerializer.Serialize(fs, s, JsonOpts);
            fs.Flush(true);
        }
        File.Move(tmp, _path, overwrite: true);
    }

    private static readonly JsonSerializerOptions JsonOpts = new()
    {
        WriteIndented = true,
        PropertyNamingPolicy = JsonNamingPolicy.CamelCase,
    };
}

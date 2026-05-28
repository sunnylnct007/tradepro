using System.Collections.Concurrent;
using System.Text.Json;
using TradePro.Api.Models;

namespace TradePro.Api.Simulation;

public interface IDocumentStore
{
    DocumentEnvelope Put(JsonElement payload);
    DocumentEnvelope? Get(string docId);
    IReadOnlyList<DocumentSummary> List(string? symbol = null);
    string? GetExtractedText(string docId);
}

/// File-backed document store, mirroring FileCompareStore's pattern.
/// One JSON file per doc at <Compare:StorePath>/documents/<doc_id>.json.
/// Atomic-rename writes; hydrates on startup so docs survive restarts.
///
/// Single-user assumption holds — uploads are infrequent, contention
/// isn't an issue. Move to a real KV store when we go multi-tenant.
public sealed class FileDocumentStore : IDocumentStore
{
    private static readonly JsonSerializerOptions JsonOpts = new()
    {
        WriteIndented = false,
        DefaultIgnoreCondition =
            System.Text.Json.Serialization.JsonIgnoreCondition.WhenWritingNull,
    };

    private readonly string _root;
    private readonly ILogger<FileDocumentStore> _logger;
    private readonly ConcurrentDictionary<string, DocumentEnvelope> _byId = new();

    public FileDocumentStore(IConfiguration config, ILogger<FileDocumentStore> logger)
    {
        _logger = logger;
        var compareRoot = config["Compare:StorePath"];
        if (string.IsNullOrWhiteSpace(compareRoot))
        {
            compareRoot = Path.Combine(Path.GetTempPath(), "tradepro-compare");
        }
        _root = Path.Combine(compareRoot, "documents");
        Directory.CreateDirectory(_root);
        Hydrate();
    }

    public DocumentEnvelope Put(JsonElement payload)
    {
        // Mac side wraps the manifest as { "document": {...} } so `document`
        // is the inner object we care about. Tolerate both shapes.
        var doc = payload.ValueKind == JsonValueKind.Object
                  && payload.TryGetProperty("document", out var inner)
                ? inner
                : payload;

        var docId = ReadString(doc, "doc_id") ?? Guid.NewGuid().ToString();
        var title = ReadString(doc, "title") ?? "(untitled)";
        var sourceUrl = ReadString(doc, "source_url");
        var symbols = ReadStringArray(doc, "linked_symbols");
        var fileKind = ReadString(doc, "file_kind") ?? "text";
        var sha256 = ReadString(doc, "sha256") ?? "";
        var charCount = ReadInt(doc, "char_count") ?? 0;
        int? pageCount = ReadInt(doc, "page_count");
        var extractedAt = ReadDate(doc, "extracted_at") ?? DateTime.UtcNow;
        var extractor = ReadString(doc, "extractor") ?? "";
        var uploadedAt = ReadDate(doc, "uploaded_at") ?? DateTime.UtcNow;
        var uploader = ReadString(doc, "uploader");

        JsonElement sections = doc.TryGetProperty("sections", out var s)
            ? s.Clone()
            : JsonDocument.Parse("[]").RootElement.Clone();

        var env = new DocumentEnvelope(
            DocId: docId,
            Title: title,
            SourceUrl: sourceUrl,
            LinkedSymbols: symbols,
            FileKind: fileKind,
            Sha256: sha256,
            CharCount: charCount,
            PageCount: pageCount,
            ExtractedAtUtc: extractedAt,
            Extractor: extractor,
            UploadedAtUtc: uploadedAt,
            ReceivedAtUtc: DateTime.UtcNow,
            Uploader: uploader,
            Sections: sections);

        _byId[docId] = env;
        WriteToDisk(env);
        return env;
    }

    public DocumentEnvelope? Get(string docId)
        => _byId.TryGetValue(docId, out var env) ? env : null;

    public IReadOnlyList<DocumentSummary> List(string? symbol = null)
    {
        var all = _byId.Values
            .Select(e => new DocumentSummary(
                e.DocId, e.Title, e.SourceUrl, e.LinkedSymbols,
                e.FileKind, e.CharCount, e.PageCount,
                e.UploadedAtUtc, e.ReceivedAtUtc));
        if (!string.IsNullOrWhiteSpace(symbol))
        {
            var sym = symbol.Trim().ToUpperInvariant();
            all = all.Where(s =>
                s.LinkedSymbols.Any(l =>
                    string.Equals(l, sym, StringComparison.OrdinalIgnoreCase)));
        }
        return all
            .OrderByDescending(s => s.ReceivedAtUtc)
            .ToArray();
    }

    public string? GetExtractedText(string docId)
    {
        if (!_byId.TryGetValue(docId, out var env)) return null;
        if (env.Sections.ValueKind != JsonValueKind.Array) return null;
        var sb = new System.Text.StringBuilder();
        foreach (var sec in env.Sections.EnumerateArray())
        {
            var heading = ReadString(sec, "heading");
            var text = ReadString(sec, "text");
            if (!string.IsNullOrWhiteSpace(heading))
            {
                sb.AppendLine($"# {heading}");
                sb.AppendLine();
            }
            if (!string.IsNullOrEmpty(text))
            {
                sb.AppendLine(text);
                sb.AppendLine();
            }
        }
        return sb.ToString().Trim();
    }

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
                    var env = Put(doc.RootElement);
                    _byId[env.DocId] = env;
                }
                catch (Exception ex)
                {
                    _logger.LogWarning(ex,
                        "Failed to hydrate document {File}", file);
                }
            }
            _logger.LogInformation("Hydrated DocumentStore with {Count} doc(s)", _byId.Count);
        }
        catch (Exception ex)
        {
            _logger.LogError(ex, "Failed to hydrate DocumentStore from {Root}", _root);
        }
    }

    private void WriteToDisk(DocumentEnvelope env)
    {
        var safe = Sanitise(env.DocId);
        var path = Path.Combine(_root, $"{safe}.json");
        var tmp = path + ".tmp";
        var record = new
        {
            doc_id = env.DocId,
            title = env.Title,
            source_url = env.SourceUrl,
            linked_symbols = env.LinkedSymbols,
            file_kind = env.FileKind,
            sha256 = env.Sha256,
            char_count = env.CharCount,
            page_count = env.PageCount,
            extracted_at = env.ExtractedAtUtc,
            extractor = env.Extractor,
            uploaded_at = env.UploadedAtUtc,
            received_at = env.ReceivedAtUtc,
            uploader = env.Uploader,
            sections = env.Sections,
        };
        try
        {
            using (var fs = File.Create(tmp))
            {
                JsonSerializer.Serialize(fs, record, JsonOpts);
                fs.Flush(true);
            }
            File.Move(tmp, path, overwrite: true);
        }
        catch (Exception ex)
        {
            _logger.LogError(ex,
                "Failed to persist document {DocId} to {Path}", env.DocId, path);
            try { if (File.Exists(tmp)) File.Delete(tmp); } catch { /* ignore */ }
        }
    }

    private static string Sanitise(string id)
    {
        var chars = id.Select(c => char.IsLetterOrDigit(c) || c == '-' ? c : '_');
        return new string(chars.ToArray());
    }

    private static string? ReadString(JsonElement el, string key)
        => el.ValueKind == JsonValueKind.Object
           && el.TryGetProperty(key, out var v)
           && v.ValueKind == JsonValueKind.String
            ? v.GetString()
            : null;

    private static int? ReadInt(JsonElement el, string key)
        => el.ValueKind == JsonValueKind.Object
           && el.TryGetProperty(key, out var v)
           && v.ValueKind == JsonValueKind.Number
           && v.TryGetInt32(out var n)
            ? n
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

    private static string[] ReadStringArray(JsonElement el, string key)
    {
        if (el.ValueKind != JsonValueKind.Object) return Array.Empty<string>();
        if (!el.TryGetProperty(key, out var v)) return Array.Empty<string>();
        if (v.ValueKind != JsonValueKind.Array) return Array.Empty<string>();
        return v.EnumerateArray()
                .Where(x => x.ValueKind == JsonValueKind.String)
                .Select(x => x.GetString() ?? "")
                .Where(s => !string.IsNullOrWhiteSpace(s))
                .ToArray();
    }
}

using System.Text;
using System.Text.Json;
using Dapper;
using Npgsql;
using TradePro.Api.Models;
using TradePro.Api.Simulation;

namespace TradePro.Api.Data.Stores;

/// <summary>
/// Documents (uploaded research artefacts) live in two tables —
/// <c>documents</c> for metadata + the JSONB envelope, and
/// <c>document_text</c> for the extracted plaintext. The split keeps
/// List() queries fast (we never project the full text just to render
/// the documents page) and lets us GIN-index linked_symbols on the
/// metadata table without including the text blobs.
/// </summary>
public sealed class PostgresDocumentStore : IDocumentStore
{
    private static readonly JsonSerializerOptions JsonOpts = new()
    {
        WriteIndented = false,
        DefaultIgnoreCondition =
            System.Text.Json.Serialization.JsonIgnoreCondition.WhenWritingNull,
    };

    private readonly NpgsqlDataSource _db;

    public PostgresDocumentStore(NpgsqlDataSource db) { _db = db; }

    public DocumentEnvelope Put(JsonElement payload)
    {
        // The Mac side sometimes wraps the manifest as { "document": {...} };
        // tolerate both shapes.
        var doc = payload.ValueKind == JsonValueKind.Object
                  && payload.TryGetProperty("document", out var inner)
                ? inner
                : payload;

        var docId = JsonbHelpers.ReadString(doc, "doc_id") ?? Guid.NewGuid().ToString();
        var title = JsonbHelpers.ReadString(doc, "title") ?? "(untitled)";
        var sourceUrl = JsonbHelpers.ReadString(doc, "source_url");
        var symbols = JsonbHelpers.ReadStringArray(doc, "linked_symbols");
        var fileKind = JsonbHelpers.ReadString(doc, "file_kind") ?? "text";
        var sha256 = JsonbHelpers.ReadString(doc, "sha256") ?? "";
        var charCount = JsonbHelpers.ReadInt(doc, "char_count");
        int? pageCount = JsonbHelpers.ReadIntOrNull(doc, "page_count");
        var extractedAt = JsonbHelpers.ReadDateOrNull(doc, "extracted_at") ?? DateTime.UtcNow;
        var extractor = JsonbHelpers.ReadString(doc, "extractor") ?? "";
        var uploadedAt = JsonbHelpers.ReadDateOrNull(doc, "uploaded_at") ?? DateTime.UtcNow;
        var uploader = JsonbHelpers.ReadString(doc, "uploader");

        JsonElement sections = doc.TryGetProperty("sections", out var s)
            ? s.Clone()
            : JsonDocument.Parse("[]").RootElement.Clone();

        var receivedAt = DateTime.UtcNow;
        var envelopeJson = JsonbHelpers.ToJsonb(doc);
        var extractedText = ExtractText(sections);

        using var conn = _db.OpenConnection();
        using var tx = conn.BeginTransaction();
        conn.Execute(@"
            INSERT INTO documents
                (doc_id, title, file_kind, extractor, char_count, page_count,
                 linked_symbols, payload, source_url, created_at)
            VALUES
                (@docId, @title, @fileKind, @extractor, @charCount, @pageCount,
                 @symbols, @envelopeJson::jsonb, @sourceUrl, @receivedAt)
            ON CONFLICT (doc_id) DO UPDATE
                SET title = EXCLUDED.title,
                    file_kind = EXCLUDED.file_kind,
                    extractor = EXCLUDED.extractor,
                    char_count = EXCLUDED.char_count,
                    page_count = EXCLUDED.page_count,
                    linked_symbols = EXCLUDED.linked_symbols,
                    payload = EXCLUDED.payload,
                    source_url = EXCLUDED.source_url;",
            new { docId, title, fileKind, extractor, charCount, pageCount,
                  symbols, envelopeJson, sourceUrl, receivedAt },
            transaction: tx);
        conn.Execute(@"
            INSERT INTO document_text (doc_id, text)
            VALUES (@docId, @text)
            ON CONFLICT (doc_id) DO UPDATE SET text = EXCLUDED.text;",
            new { docId, text = extractedText },
            transaction: tx);
        tx.Commit();

        return new DocumentEnvelope(
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
            ReceivedAtUtc: receivedAt,
            Uploader: uploader,
            Sections: sections);
    }

    public DocumentEnvelope? Get(string docId)
    {
        using var conn = _db.OpenConnection();
        var row = conn.QueryFirstOrDefault<DocRow>(@"
            SELECT doc_id, title, file_kind, extractor, char_count, page_count,
                   linked_symbols, source_url, created_at, payload::text AS payload
            FROM documents WHERE doc_id = @docId;",
            new { docId });
        if (row is null) return null;
        var payloadEl = JsonbHelpers.FromJsonb(row.payload);
        var sections = payloadEl.TryGetProperty("sections", out var s)
            ? s.Clone()
            : JsonDocument.Parse("[]").RootElement.Clone();
        return new DocumentEnvelope(
            DocId: row.doc_id,
            Title: row.title,
            SourceUrl: row.source_url,
            LinkedSymbols: row.linked_symbols ?? Array.Empty<string>(),
            FileKind: row.file_kind,
            Sha256: JsonbHelpers.ReadString(payloadEl, "sha256") ?? "",
            CharCount: row.char_count,
            PageCount: row.page_count,
            ExtractedAtUtc: JsonbHelpers.ReadDateOrNull(payloadEl, "extracted_at") ?? row.created_at,
            Extractor: row.extractor,
            UploadedAtUtc: JsonbHelpers.ReadDateOrNull(payloadEl, "uploaded_at") ?? row.created_at,
            ReceivedAtUtc: row.created_at,
            Uploader: JsonbHelpers.ReadString(payloadEl, "uploader"),
            Sections: sections);
    }

    public IReadOnlyList<DocumentSummary> List(string? symbol = null)
    {
        using var conn = _db.OpenConnection();
        IEnumerable<DocSummaryRow> rows;
        if (!string.IsNullOrWhiteSpace(symbol))
        {
            // GIN index makes the @> (contains) check O(log n).
            // We uppercase on the way in for consistency with the
            // existing case-insensitive behaviour.
            var sym = symbol.Trim().ToUpperInvariant();
            rows = conn.Query<DocSummaryRow>(@"
                SELECT doc_id, title, file_kind, char_count, page_count,
                       linked_symbols, source_url, created_at
                FROM documents
                WHERE EXISTS (
                    SELECT 1 FROM unnest(linked_symbols) sym
                    WHERE UPPER(sym) = @sym
                )
                ORDER BY created_at DESC;",
                new { sym });
        }
        else
        {
            rows = conn.Query<DocSummaryRow>(@"
                SELECT doc_id, title, file_kind, char_count, page_count,
                       linked_symbols, source_url, created_at
                FROM documents
                ORDER BY created_at DESC;");
        }
        return rows.Select(r => new DocumentSummary(
            DocId: r.doc_id,
            Title: r.title,
            SourceUrl: r.source_url,
            LinkedSymbols: r.linked_symbols ?? Array.Empty<string>(),
            FileKind: r.file_kind,
            CharCount: r.char_count,
            PageCount: r.page_count,
            UploadedAtUtc: r.created_at,
            ReceivedAtUtc: r.created_at)).ToArray();
    }

    public string? GetExtractedText(string docId)
    {
        using var conn = _db.OpenConnection();
        return conn.QueryFirstOrDefault<string>(
            "SELECT text FROM document_text WHERE doc_id = @docId;",
            new { docId });
    }

    private static string ExtractText(JsonElement sections)
    {
        if (sections.ValueKind != JsonValueKind.Array) return string.Empty;
        var sb = new StringBuilder();
        foreach (var sec in sections.EnumerateArray())
        {
            var heading = JsonbHelpers.ReadString(sec, "heading");
            var text = JsonbHelpers.ReadString(sec, "text");
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

    private sealed record DocRow(
        string doc_id, string title, string file_kind, string extractor,
        int char_count, int? page_count, string[]? linked_symbols,
        string? source_url, DateTime created_at, string payload);

    private sealed record DocSummaryRow(
        string doc_id, string title, string file_kind,
        int char_count, int? page_count, string[]? linked_symbols,
        string? source_url, DateTime created_at);
}

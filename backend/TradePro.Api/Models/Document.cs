using System.Text.Json;

namespace TradePro.Api.Models;

/// One uploaded research document (PDF / HTML / TXT extracted on the Mac
/// before push). The `Sections` array preserves the structural extraction
/// — page boundaries for PDFs, paragraph blocks for HTML — so retrieval
/// at decision time can cite back to a specific section + page.
public record DocumentEnvelope(
    string DocId,
    string Title,
    string? SourceUrl,
    string[] LinkedSymbols,
    string FileKind,
    string Sha256,
    int CharCount,
    int? PageCount,
    DateTime ExtractedAtUtc,
    string Extractor,
    DateTime UploadedAtUtc,
    DateTime ReceivedAtUtc,
    string? Uploader,
    JsonElement Sections);          // raw, preserves the producer-side shape

public record DocumentSummary(
    string DocId,
    string Title,
    string? SourceUrl,
    string[] LinkedSymbols,
    string FileKind,
    int CharCount,
    int? PageCount,
    DateTime UploadedAtUtc,
    DateTime ReceivedAtUtc);

using TradePro.Api.Simulation;

namespace TradePro.Api.Endpoints;

/// Read side of the document store. Authenticated frontend users list
/// and view uploaded research; the Mac comparator (Phase 5c-iii.) hits
/// /api/documents/{id}/text at decision time to retrieve extracted
/// chunks for the rationale's allowed-facts bundle.
public static class DocumentEndpoints
{
    public static IEndpointRouteBuilder MapDocumentEndpoints(this IEndpointRouteBuilder app)
    {
        var group = app.MapGroup("/documents").WithTags("Documents");

        group.MapGet("", (string? symbol, IDocumentStore store) =>
            Results.Ok(new { documents = store.List(symbol) }));

        group.MapGet("/{docId}", (string docId, IDocumentStore store) =>
        {
            var env = store.Get(docId);
            if (env is null)
                return Results.NotFound(new { error = $"document {docId} not found" });
            return Results.Ok(new
            {
                docId = env.DocId,
                title = env.Title,
                sourceUrl = env.SourceUrl,
                linkedSymbols = env.LinkedSymbols,
                fileKind = env.FileKind,
                sha256 = env.Sha256,
                charCount = env.CharCount,
                pageCount = env.PageCount,
                extractedAtUtc = env.ExtractedAtUtc,
                extractor = env.Extractor,
                uploadedAtUtc = env.UploadedAtUtc,
                receivedAtUtc = env.ReceivedAtUtc,
                uploader = env.Uploader,
                sections = env.Sections,
            });
        });

        group.MapGet("/{docId}/text", (string docId, IDocumentStore store) =>
        {
            var text = store.GetExtractedText(docId);
            if (text is null)
                return Results.NotFound(new { error = $"document {docId} not found" });
            return Results.Text(text, "text/plain");
        });

        return app;
    }
}

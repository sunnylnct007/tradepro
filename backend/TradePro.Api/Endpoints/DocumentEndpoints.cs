using System.Net.Http.Headers;
using System.Text.Json;
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

        // Browser-driven upload. The .NET API doesn't do PDF / HTML
        // extraction itself — it forwards the multipart payload to the
        // extractor sidecar (PyMuPDF + trafilatura), receives a
        // structured manifest, stores via DocumentStore.
        group.MapPost("/upload",
            async (HttpRequest req,
                   IDocumentStore store,
                   IConfiguration config,
                   IHttpClientFactory clientFactory,
                   ILoggerFactory logFactory) =>
            {
                if (!req.HasFormContentType)
                {
                    return Results.BadRequest(new
                    { error = "expected multipart/form-data with 'file' part" });
                }
                var form = await req.ReadFormAsync();
                var file = form.Files.GetFile("file");
                if (file is null || file.Length == 0)
                {
                    return Results.BadRequest(new { error = "missing 'file' part" });
                }

                var title = form["title"].ToString();
                if (string.IsNullOrWhiteSpace(title))
                    title = Path.GetFileNameWithoutExtension(file.FileName);
                var symbols = form["symbols"].ToString();
                var sourceUrl = form["sourceUrl"].ToString();
                var uploader = form["uploader"].ToString();
                if (string.IsNullOrWhiteSpace(uploader)) uploader = "browser-upload";

                var extractorUrl = config["Extractor:Url"] ?? "http://extractor:8000";
                var log = logFactory.CreateLogger("DocumentEndpoints");

                using var client = clientFactory.CreateClient();
                client.Timeout = TimeSpan.FromSeconds(60);
                using var content = new MultipartFormDataContent();
                using var stream = file.OpenReadStream();
                var fileContent = new StreamContent(stream);
                fileContent.Headers.ContentType =
                    new MediaTypeHeaderValue(
                        string.IsNullOrEmpty(file.ContentType)
                            ? "application/octet-stream"
                            : file.ContentType);
                content.Add(fileContent, "file", file.FileName);
                content.Add(new StringContent(title), "title");
                content.Add(new StringContent(symbols), "symbols");
                content.Add(new StringContent(sourceUrl), "source_url");
                content.Add(new StringContent(uploader), "uploader");

                HttpResponseMessage extractorResp;
                try
                {
                    extractorResp = await client.PostAsync(
                        $"{extractorUrl.TrimEnd('/')}/extract", content);
                }
                catch (Exception ex)
                {
                    log.LogError(ex, "extractor unreachable at {Url}", extractorUrl);
                    return Results.Problem(
                        $"extractor unreachable: {ex.Message}", statusCode: 502);
                }

                var body = await extractorResp.Content.ReadAsStringAsync();
                if (!extractorResp.IsSuccessStatusCode)
                {
                    return Results.Problem(
                        $"extractor returned {(int)extractorResp.StatusCode}: " +
                        body[..Math.Min(body.Length, 500)],
                        statusCode: 502);
                }

                JsonElement root;
                try
                {
                    root = JsonDocument.Parse(body).RootElement;
                }
                catch (Exception ex)
                {
                    return Results.Problem(
                        $"extractor response wasn't JSON: {ex.Message}",
                        statusCode: 502);
                }

                var env = store.Put(root);
                return Results.Ok(new
                {
                    docId = env.DocId,
                    title = env.Title,
                    fileKind = env.FileKind,
                    extractor = env.Extractor,
                    charCount = env.CharCount,
                    pageCount = env.PageCount,
                    linkedSymbols = env.LinkedSymbols,
                    receivedAtUtc = env.ReceivedAtUtc,
                });
            })
            .DisableAntiforgery();

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

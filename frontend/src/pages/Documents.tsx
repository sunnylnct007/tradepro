import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api/client";
import type { DocumentSummary } from "../api/types";

/** Document library — uploaded research, prospectuses, analyst notes.
 *
 * Today's flow is push-from-Mac via the `tradepro-doc-upload` CLI
 * (extraction lives in Python, with pdfplumber + trafilatura — too
 * heavy to put in the .NET API directly). This page surfaces the
 * existing library + the CLI command. Native browser upload comes
 * with a Mac-side extraction HTTP endpoint in a follow-up slice. */
export function Documents() {
  const [docs, setDocs] = useState<DocumentSummary[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [filter, setFilter] = useState<string>("");

  useEffect(() => {
    api.documents()
      .then((r) => setDocs(r.documents))
      .catch((e) => setError(String(e)));
  }, []);

  const filtered = useMemo(() => {
    if (!docs) return [];
    if (!filter.trim()) return docs;
    const q = filter.trim().toUpperCase();
    return docs.filter((d) =>
      d.linkedSymbols.some((s) => s.toUpperCase().includes(q))
      || d.title.toUpperCase().includes(q)
    );
  }, [docs, filter]);

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 18 }}>
      <div>
        <h1 style={{ margin: 0, fontSize: 24 }}>Documents</h1>
        <p style={{ color: "var(--text-dim)", margin: "6px 0 0 0", maxWidth: 760 }}>
          Research uploaded from the Mac for use at decision time. PDFs
          extracted with pdfplumber, HTML with trafilatura, TXT/MD
          pass-through. Each document is linked to one or more symbols
          and the extracted text is queryable by the comparator.
        </p>
      </div>

      <UploadInstructions />

      <section className="card" style={{ padding: "12px 16px" }}>
        <label style={{ display: "flex", alignItems: "center", gap: 10 }}>
          <span style={{ fontSize: 12, color: "var(--text-dim)" }}>Filter</span>
          <input
            type="search"
            placeholder="symbol or title (e.g. QQQ)"
            value={filter}
            onChange={(e) => setFilter(e.target.value)}
            style={{ flex: 1, maxWidth: 360 }}
          />
          <span style={{ fontSize: 11, color: "var(--text-muted)" }}>
            {docs ? `${filtered.length} of ${docs.length}` : "—"}
          </span>
        </label>
      </section>

      {error && (
        <div className="card" style={{ borderColor: "var(--down)", color: "var(--down)" }}>
          {error}
        </div>
      )}

      {docs && filtered.length === 0 && !error && (
        <div className="card" style={{ color: "var(--text-dim)", padding: "16px 18px" }}>
          {docs.length === 0
            ? "No documents uploaded yet. Use the CLI command above to add one."
            : `No documents matched "${filter}".`}
        </div>
      )}

      {filtered.length > 0 && (
        <ul style={{
          margin: 0, padding: 0, listStyle: "none",
          display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(320px, 1fr))",
          gap: 12,
        }}>
          {filtered.map((d) => <DocCard key={d.docId} doc={d} />)}
        </ul>
      )}
    </div>
  );
}

function DocCard({ doc }: { doc: DocumentSummary }) {
  return (
    <li className="card" style={{ padding: "14px 16px" }}>
      <div style={{ display: "flex", alignItems: "baseline", gap: 8, marginBottom: 6 }}>
        <span style={{
          fontSize: 10,
          padding: "2px 6px",
          borderRadius: 3,
          background: "rgba(255,255,255,0.06)",
          color: "var(--text-muted)",
          letterSpacing: "0.05em",
          textTransform: "uppercase",
        }}>
          {doc.fileKind}
        </span>
        <span style={{ fontSize: 11, color: "var(--text-muted)" }}>
          {doc.charCount.toLocaleString()} chars
          {doc.pageCount ? ` · ${doc.pageCount}p` : ""}
        </span>
      </div>
      <Link
        to={`/documents/${encodeURIComponent(doc.docId)}`}
        style={{ color: "var(--text)", fontWeight: 600, textDecoration: "none" }}
      >
        {doc.title}
      </Link>
      <div style={{ marginTop: 6, display: "flex", flexWrap: "wrap", gap: 4 }}>
        {doc.linkedSymbols.map((s) => (
          <span key={s} style={{
            fontSize: 10, padding: "2px 6px", borderRadius: 3,
            background: "rgba(31,193,107,0.10)",
            color: "var(--up)",
            fontFamily: "ui-monospace, SFMono-Regular, Menlo, monospace",
          }}>
            {s}
          </span>
        ))}
      </div>
      <div style={{ marginTop: 8, fontSize: 11, color: "var(--text-muted)" }}>
        Uploaded {new Date(doc.uploadedAtUtc).toLocaleString()}
      </div>
    </li>
  );
}

function UploadInstructions() {
  return (
    <details
      className="card"
      style={{ borderLeft: "3px solid var(--neutral)", padding: "10px 14px" }}
    >
      <summary style={{ cursor: "pointer", fontSize: 13, fontWeight: 600 }}>
        Upload a new document (CLI)
      </summary>
      <p style={{ margin: "8px 0 4px 0", fontSize: 12, color: "var(--text-dim)" }}>
        Run this on the Mac. PDFs are extracted with pdfplumber, HTML
        with trafilatura, TXT/MD pass through. Browser drag-and-drop
        coming in a follow-up.
      </p>
      <pre style={{
        margin: "6px 0 0 0",
        padding: "8px 10px",
        background: "rgba(0,0,0,0.3)",
        borderRadius: 4,
        fontSize: 12,
        overflowX: "auto",
        color: "var(--text)",
      }}>
{`uv run tradepro-doc-upload prospectus.pdf \\
    --symbols QQQ,VOO \\
    --title "Vanguard S&P 500 prospectus 2026" \\
    --source-url https://...`}
      </pre>
      <div style={{ marginTop: 6, fontSize: 11, color: "var(--text-muted)" }}>
        Supported: <code>.pdf</code> · <code>.html</code> · <code>.htm</code>{" "}
        · <code>.txt</code> · <code>.md</code>
      </div>
    </details>
  );
}

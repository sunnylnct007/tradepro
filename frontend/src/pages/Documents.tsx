import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { api } from "../api/client";
import type { DocumentSummary } from "../api/types";

/** Document library — uploaded research, prospectuses, analyst notes.
 *
 * Browser upload posts multipart to /api/documents/upload, which
 * forwards to the Python extractor sidecar (PyMuPDF for PDFs,
 * trafilatura for HTML, pass-through for TXT/MD). The CLI route
 * (`tradepro-doc-upload`) still works for batch ingestion from the
 * Mac. */
export function Documents() {
  const [docs, setDocs] = useState<DocumentSummary[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [filter, setFilter] = useState<string>("");

  const reload = useCallback(() => {
    api.documents()
      .then((r) => { setDocs(r.documents); setError(null); })
      .catch((e) => setError(String(e)));
  }, []);

  useEffect(() => { reload(); }, [reload]);

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
          Research uploaded for use at decision time. PDFs extracted with{" "}
          <strong>PyMuPDF</strong>, HTML with <strong>trafilatura</strong>,
          TXT/MD pass-through. Each document is linked to one or more
          symbols and the extracted text is queryable by the comparator.
        </p>
      </div>

      <UploadDropZone onUploaded={reload} />

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
            ? "No documents uploaded yet. Drop one above to get started."
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

/** Drag-and-drop / picker upload. Posts to /api/documents/upload
 * (multipart) which forwards to the extractor sidecar. Shows progress
 * + the resulting doc summary. */
function UploadDropZone({ onUploaded }: { onUploaded: () => void }) {
  const [drag, setDrag] = useState(false);
  const [pending, setPending] = useState<File | null>(null);
  const [title, setTitle] = useState("");
  const [symbols, setSymbols] = useState("");
  const [sourceUrl, setSourceUrl] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [last, setLast] = useState<{ docId: string; title: string; charCount: number; extractor: string } | null>(null);
  const fileRef = useRef<HTMLInputElement>(null);
  const navigate = useNavigate();

  function pickFile(f: File) {
    setPending(f);
    setLast(null);
    setError(null);
    if (!title) {
      const base = f.name.replace(/\.[^.]+$/, "");
      setTitle(base);
    }
  }

  async function upload() {
    if (!pending) return;
    setBusy(true);
    setError(null);
    try {
      const result = await api.uploadDocument(
        pending,
        title || pending.name,
        symbols,
        sourceUrl || undefined,
      );
      setLast(result);
      setPending(null);
      setTitle("");
      setSymbols("");
      setSourceUrl("");
      if (fileRef.current) fileRef.current.value = "";
      onUploaded();
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <section
      className="card"
      style={{
        padding: 0,
        overflow: "hidden",
        borderColor: drag ? "var(--up)" : undefined,
        borderStyle: drag ? "dashed" : "solid",
        transition: "border-color 0.1s ease",
      }}
    >
      <div
        onDragOver={(e) => { e.preventDefault(); setDrag(true); }}
        onDragLeave={() => setDrag(false)}
        onDrop={(e) => {
          e.preventDefault(); setDrag(false);
          const f = e.dataTransfer.files?.[0];
          if (f) pickFile(f);
        }}
        style={{
          padding: "20px 22px",
          background: drag ? "rgba(31,193,107,0.06)" : "transparent",
          textAlign: "center",
          cursor: "pointer",
        }}
        onClick={() => fileRef.current?.click()}
      >
        <input
          ref={fileRef}
          type="file"
          accept=".pdf,.html,.htm,.txt,.md"
          style={{ display: "none" }}
          onChange={(e) => {
            const f = e.target.files?.[0];
            if (f) pickFile(f);
          }}
        />
        {pending ? (
          <div style={{ color: "var(--text)" }}>
            <strong>{pending.name}</strong>{" "}
            <span style={{ color: "var(--text-muted)", fontSize: 12 }}>
              ({Math.round(pending.size / 1024)} KB) — fill in details + click Upload, or drop another to replace
            </span>
          </div>
        ) : (
          <div>
            <div style={{ fontSize: 14, color: "var(--text)" }}>
              Drop a file here, or click to choose
            </div>
            <div style={{ marginTop: 6, fontSize: 11, color: "var(--text-muted)" }}>
              Supported: <code>.pdf</code> · <code>.html</code> · <code>.htm</code>{" "}
              · <code>.txt</code> · <code>.md</code> &nbsp;·&nbsp;
              PDF via PyMuPDF, HTML via trafilatura
            </div>
          </div>
        )}
      </div>

      {pending && (
        <div style={{
          padding: "12px 16px",
          borderTop: "1px solid var(--border)",
          display: "grid",
          gridTemplateColumns: "1fr 1fr",
          gap: 10,
        }}>
          <label style={{ display: "flex", flexDirection: "column", gap: 3 }}>
            <span className="stat-label">Title</span>
            <input
              type="text"
              value={title}
              onChange={(e) => setTitle(e.target.value)}
              placeholder={pending.name}
            />
          </label>
          <label style={{ display: "flex", flexDirection: "column", gap: 3 }}>
            <span className="stat-label">Linked symbols (comma-sep)</span>
            <input
              type="text"
              value={symbols}
              onChange={(e) => setSymbols(e.target.value)}
              placeholder="QQQ,VOO"
            />
          </label>
          <label style={{ gridColumn: "1 / -1", display: "flex", flexDirection: "column", gap: 3 }}>
            <span className="stat-label">Source URL (optional)</span>
            <input
              type="url"
              value={sourceUrl}
              onChange={(e) => setSourceUrl(e.target.value)}
              placeholder="https://example.com/research-paper.pdf"
            />
          </label>
          <div style={{ gridColumn: "1 / -1", display: "flex", gap: 8 }}>
            <button className="primary" onClick={upload} disabled={busy}>
              {busy ? "Uploading + extracting…" : "Upload"}
            </button>
            <button onClick={() => { setPending(null); setError(null); if (fileRef.current) fileRef.current.value = ""; }} disabled={busy}>
              Cancel
            </button>
          </div>
        </div>
      )}

      {error && (
        <div style={{
          padding: "10px 16px",
          borderTop: "1px solid var(--border)",
          color: "var(--down)",
          fontSize: 12,
        }}>
          Upload failed: {error}
        </div>
      )}

      {last && (
        <div style={{
          padding: "10px 16px",
          borderTop: "1px solid var(--border)",
          color: "var(--text-dim)",
          fontSize: 12,
          display: "flex",
          alignItems: "center",
          gap: 10,
          flexWrap: "wrap",
        }}>
          <span style={{ color: "var(--up)", fontWeight: 600 }}>
            ✓ Uploaded
          </span>
          <span>
            <strong style={{ color: "var(--text)" }}>{last.title}</strong> —{" "}
            {last.charCount.toLocaleString()} chars via {last.extractor}
          </span>
          <button
            style={{ marginLeft: "auto" }}
            onClick={() => navigate(`/documents/${encodeURIComponent(last.docId)}`)}
          >
            Open →
          </button>
        </div>
      )}
    </section>
  );
}

import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { api } from "../api/client";
import type { DocumentEnvelope } from "../api/types";

/** Single-document view: manifest header + extracted text. The text is
 * what the comparator actually consumes at decision time, so showing
 * exactly that closes the trust loop — the user can scroll the same
 * paragraphs the LLM rationale was allowed to cite. */
export function DocumentDetail() {
  const { docId } = useParams<{ docId: string }>();
  const [envelope, setEnvelope] = useState<DocumentEnvelope | null>(null);
  const [text, setText] = useState<string>("");
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!docId) return;
    api.document(docId)
      .then(setEnvelope)
      .catch((e) => setError(String(e)));
    api.documentText(docId)
      .then(setText)
      .catch(() => {/* text load failure is non-fatal — manifest is the lead */});
  }, [docId]);

  if (error) {
    return (
      <div className="card" style={{ borderColor: "var(--down)", color: "var(--down)" }}>
        Couldn't load document: {error}
        <div style={{ marginTop: 8 }}>
          <Link to="/documents" style={{ color: "var(--text)" }}>← Back to library</Link>
        </div>
      </div>
    );
  }

  if (!envelope) {
    return <div style={{ color: "var(--text-dim)" }}>Loading…</div>;
  }

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
      <div>
        <Link to="/documents" style={{ fontSize: 12, color: "var(--text-dim)", textDecoration: "none" }}>
          ← All documents
        </Link>
        <h1 style={{ margin: "6px 0 0 0", fontSize: 22 }}>{envelope.title}</h1>
        {envelope.sourceUrl && (
          <a
            href={envelope.sourceUrl}
            target="_blank" rel="noreferrer"
            style={{ fontSize: 12, color: "var(--text-dim)" }}
          >
            {envelope.sourceUrl}
          </a>
        )}
      </div>

      <section className="card" style={{
        padding: "12px 14px",
        display: "grid",
        gridTemplateColumns: "repeat(auto-fit, minmax(150px, 1fr))",
        gap: 10,
      }}>
        <Stat label="Linked symbols" value={
          envelope.linkedSymbols.length
            ? envelope.linkedSymbols.join(" · ")
            : "—"
        } />
        <Stat label="Kind" value={envelope.fileKind} />
        <Stat
          label="Size"
          value={`${envelope.charCount.toLocaleString()} chars${envelope.pageCount ? ` · ${envelope.pageCount} pages` : ""}`}
        />
        <Stat label="Extractor" value={envelope.extractor} />
        <Stat label="Uploaded" value={new Date(envelope.uploadedAtUtc).toLocaleString()} />
        <Stat label="Uploader" value={envelope.uploader ?? "—"} mono />
        <Stat label="SHA-256" value={envelope.sha256.slice(0, 12) + "…"} mono />
      </section>

      <section className="card" style={{ padding: "16px 18px" }}>
        <div className="stat-label" style={{ marginBottom: 8 }}>
          Extracted text{" "}
          <span style={{ color: "var(--text-muted)", fontWeight: 400 }}>
            (the exact body the comparator can cite at decision time)
          </span>
        </div>
        {text ? (
          <pre
            style={{
              whiteSpace: "pre-wrap",
              wordBreak: "break-word",
              margin: 0,
              fontFamily: "inherit",
              fontSize: 13,
              lineHeight: 1.55,
              color: "var(--text)",
              maxHeight: 640,
              overflowY: "auto",
            }}
          >
            {text}
          </pre>
        ) : (
          <div style={{ color: "var(--text-muted)", fontSize: 12 }}>
            (loading text…)
          </div>
        )}
      </section>

      {envelope.sections && envelope.sections.length > 1 && (
        <section className="card" style={{ padding: "12px 14px" }}>
          <div className="stat-label" style={{ marginBottom: 6 }}>
            Sections ({envelope.sections.length})
          </div>
          <ul style={{ margin: 0, padding: "0 0 0 16px", fontSize: 12, color: "var(--text-dim)" }}>
            {envelope.sections.slice(0, 50).map((s, i) => (
              <li key={i}>
                {s.heading ?? `(no heading)`}
                {s.page ? ` · page ${s.page}` : ""}
                {" "}<span style={{ color: "var(--text-muted)" }}>
                  · {s.text.length.toLocaleString()} chars
                </span>
              </li>
            ))}
          </ul>
        </section>
      )}
    </div>
  );
}

function Stat({
  label,
  value,
  mono,
}: {
  label: string;
  value: string;
  mono?: boolean;
}) {
  return (
    <div>
      <div className="stat-label">{label}</div>
      <div
        className={mono ? "num" : ""}
        style={{ marginTop: 2, fontSize: 13, color: "var(--text)" }}
      >
        {value}
      </div>
    </div>
  );
}

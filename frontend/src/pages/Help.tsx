import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
// Embedded at build time from the single source of truth.
import architectureMd from "../../../docs/ARCHITECTURE.md?raw";

export function Help() {
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
      <div>
        <h1 style={{ margin: 0, fontSize: 24 }}>Help</h1>
        <p style={{ color: "var(--text-dim)", margin: "6px 0 0 0" }}>
          What TradePro does, what every control means, and the maths behind the signals.
          Hover the <span className="info" style={{ cursor: "default" }}>i</span> icon next to
          any form field for a short explanation of that specific input.
        </p>
      </div>
      <article className="card prose" style={{ padding: "24px 28px" }}>
        <ReactMarkdown remarkPlugins={[remarkGfm]}>
          {architectureMd}
        </ReactMarkdown>
      </article>
    </div>
  );
}

import { Link, useParams } from "react-router-dom";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { HELP_TOPICS, topicBySlug } from "../docs/help-content";

/** Renders a single Help topic — title, summary, all sections.
 *
 * Each section's body is markdown so we can keep the content in
 * help-content.ts and benefit from react-markdown's table / code-block
 * support without a separate file per section. */
export function HelpTopic() {
  const { topic: slug } = useParams<{ topic: string }>();
  const topic = slug ? topicBySlug(slug) : undefined;

  if (!topic) {
    return (
      <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
        <h1 style={{ margin: 0, fontSize: 22 }}>Topic not found</h1>
        <p style={{ color: "var(--text-dim)" }}>
          The topic <code>{slug}</code> doesn't exist.{" "}
          <Link to="/help">Back to Help</Link>.
        </p>
      </div>
    );
  }

  const idx = HELP_TOPICS.findIndex((t) => t.slug === topic.slug);
  const prev = idx > 0 ? HELP_TOPICS[idx - 1] : null;
  const next = idx < HELP_TOPICS.length - 1 ? HELP_TOPICS[idx + 1] : null;

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 18 }}>
      <div>
        <Link
          to="/help"
          style={{
            fontSize: 12,
            color: "var(--text-dim)",
            textDecoration: "none",
          }}
        >
          ← All topics
        </Link>
        <h1 style={{ margin: "6px 0 0 0", fontSize: 24, display: "flex", gap: 10, alignItems: "center" }}>
          <span style={{ fontSize: 22 }}>{topic.emoji}</span>
          {topic.title}
        </h1>
        <p style={{ color: "var(--text-dim)", margin: "6px 0 0 0", maxWidth: 760 }}>
          {topic.summary}
        </p>
      </div>

      <article
        className="card prose"
        style={{ padding: "20px 24px", display: "flex", flexDirection: "column", gap: 18 }}
      >
        {topic.sections.map((s, i) => (
          <section key={i}>
            <h2
              style={{
                margin: "0 0 8px 0",
                fontSize: 16,
                color: "var(--text)",
                fontWeight: 600,
              }}
            >
              {s.heading}
            </h2>
            <div style={{ color: "var(--text-dim)", fontSize: 14, lineHeight: 1.65 }}>
              <ReactMarkdown remarkPlugins={[remarkGfm]}>{s.body}</ReactMarkdown>
            </div>
          </section>
        ))}
      </article>

      <div
        style={{
          display: "flex",
          justifyContent: "space-between",
          gap: 12,
          fontSize: 13,
        }}
      >
        {prev ? (
          <Link
            to={`/help/${prev.slug}`}
            style={{
              padding: "10px 14px",
              borderRadius: 8,
              border: "1px solid var(--border)",
              color: "var(--text)",
              textDecoration: "none",
              flex: 1,
              maxWidth: 320,
            }}
          >
            <div style={{ fontSize: 11, color: "var(--text-muted)" }}>← Previous</div>
            <div style={{ fontWeight: 600, marginTop: 2 }}>{prev.emoji} {prev.title}</div>
          </Link>
        ) : <div />}
        {next ? (
          <Link
            to={`/help/${next.slug}`}
            style={{
              padding: "10px 14px",
              borderRadius: 8,
              border: "1px solid var(--border)",
              color: "var(--text)",
              textDecoration: "none",
              flex: 1,
              maxWidth: 320,
              textAlign: "right",
            }}
          >
            <div style={{ fontSize: 11, color: "var(--text-muted)" }}>Next →</div>
            <div style={{ fontWeight: 600, marginTop: 2 }}>{next.emoji} {next.title}</div>
          </Link>
        ) : <div />}
      </div>
    </div>
  );
}

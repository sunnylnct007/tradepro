import { Link } from "react-router-dom";
import { HELP_TOPICS } from "../docs/help-content";

/** Help index — modular topic cards. Each card links to /help/<slug>
 * which renders that single topic in HelpTopic.tsx. The old single-
 * page render of ARCHITECTURE.md is now linked from the 'How it works'
 * topic for users who want the full deep-dive. */
export function Help() {
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 20 }}>
      <div>
        <h1 style={{ margin: 0, fontSize: 24 }}>Help & learn</h1>
        <p style={{ color: "var(--text-dim)", margin: "6px 0 0 0", maxWidth: 880 }}>
          Short, plain-English explanations of every concept the app uses —
          from <em>what is an ETF</em> through <em>how a Sharpe ratio works</em>.
          Topics are intentionally bite-sized; pick the one you need.
        </p>
        <p style={{ color: "var(--text-muted)", margin: "8px 0 0 0", fontSize: 12, maxWidth: 880 }}>
          Tip: hover the <span className="info" style={{ cursor: "default" }}>i</span> icon
          next to any control on the rest of the app for a one-line explanation
          of <em>that specific</em> input.
        </p>
      </div>

      <div
        style={{
          display: "grid",
          gridTemplateColumns: "repeat(auto-fit, minmax(280px, 1fr))",
          gap: 14,
        }}
      >
        {HELP_TOPICS.map((t) => (
          <Link
            key={t.slug}
            to={`/help/${t.slug}`}
            className="card"
            style={{
              padding: "16px 18px",
              textDecoration: "none",
              color: "inherit",
              display: "flex",
              flexDirection: "column",
              gap: 8,
              transition: "border-color 0.15s ease, transform 0.1s ease",
            }}
          >
            <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
              <span style={{ fontSize: 22 }}>{t.emoji}</span>
              <span style={{ fontSize: 16, fontWeight: 600, color: "var(--text)" }}>
                {t.title}
              </span>
            </div>
            <p style={{ margin: 0, fontSize: 13, color: "var(--text-dim)", lineHeight: 1.5 }}>
              {t.summary}
            </p>
            <div style={{ fontSize: 11, color: "var(--text-muted)", marginTop: "auto" }}>
              {t.sections.length} section{t.sections.length === 1 ? "" : "s"} →
            </div>
          </Link>
        ))}
      </div>
    </div>
  );
}

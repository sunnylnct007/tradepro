import { Link } from "react-router-dom";
import { HELP_TOPICS, HelpTopic } from "../docs/help-content";

/**
 * Help index — topics organised into three lanes:
 *
 *  🏦 Trade Support   — COMPASS, macro gate, sector RS, EPS revision,
 *                       signal ledger.  What the alpha engine means for
 *                       your daily trading decisions.
 *
 *  🖥️ IT / Ops Guide  — Scheduling, automation, health checks, runbook.
 *                       What the IT operator / developer needs to keep
 *                       the system running.
 *
 *  📖 Concepts        — Everything else: trading basics, indicators,
 *                       risk metrics, strategies, market context, LLM,
 *                       data sources.
 */

const TRADE_SUPPORT_SLUGS = new Set([
  "compass-score",
  "macro-regime",
  "sector-rs",
  "eps-revision",
  "signal-ledger",
]);

const IT_OPS_SLUGS = new Set([
  "scheduling",
  "ops-runbook",
]);

function lane(topics: HelpTopic[], slugSet: Set<string>): HelpTopic[] {
  return topics.filter((t) => slugSet.has(t.slug));
}

function rest(topics: HelpTopic[], ...slugSets: Set<string>[]): HelpTopic[] {
  const all = new Set(slugSets.flatMap((s) => [...s]));
  return topics.filter((t) => !all.has(t.slug));
}

function TopicCard({ t }: { t: HelpTopic }) {
  return (
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
        <span style={{ fontSize: 15, fontWeight: 600, color: "var(--text)" }}>
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
  );
}

function Section({
  label,
  badge,
  description,
  topics,
}: {
  label: string;
  badge: string;
  description: string;
  topics: HelpTopic[];
}) {
  if (topics.length === 0) return null;
  return (
    <div>
      <div style={{ display: "flex", alignItems: "baseline", gap: 10, marginBottom: 4 }}>
        <span style={{ fontSize: 18 }}>{badge}</span>
        <h2 style={{ margin: 0, fontSize: 16, fontWeight: 700 }}>{label}</h2>
      </div>
      <p style={{ margin: "0 0 12px 0", fontSize: 13, color: "var(--text-muted)", maxWidth: 680 }}>
        {description}
      </p>
      <div
        style={{
          display: "grid",
          gridTemplateColumns: "repeat(auto-fit, minmax(270px, 1fr))",
          gap: 12,
        }}
      >
        {topics.map((t) => (
          <TopicCard key={t.slug} t={t} />
        ))}
      </div>
    </div>
  );
}

export function Help() {
  const tradeTopics  = lane(HELP_TOPICS, TRADE_SUPPORT_SLUGS);
  const opsTopics    = lane(HELP_TOPICS, IT_OPS_SLUGS);
  const otherTopics  = rest(HELP_TOPICS, TRADE_SUPPORT_SLUGS, IT_OPS_SLUGS);

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 32 }}>

      {/* ── Header ─────────────────────────────────────────────────── */}
      <div>
        <h1 style={{ margin: 0, fontSize: 24 }}>Help & learn</h1>
        <p style={{ color: "var(--text-dim)", margin: "6px 0 0 0", maxWidth: 880 }}>
          Short, plain-English explanations of every concept the app uses.
          Topics are split by audience: <strong>traders</strong> who need
          to understand COMPASS and the alpha engine, <strong>IT operators</strong> who
          manage scheduling and the Mac engine, and everyone else who wants
          to understand the underlying concepts.
        </p>
        <p style={{ color: "var(--text-muted)", margin: "8px 0 0 0", fontSize: 12, maxWidth: 880 }}>
          Tip: hover the <span className="info" style={{ cursor: "default" }}>i</span> icon
          next to any control for a one-line explanation of that specific input.
        </p>
      </div>

      {/* ── Trade Support ───────────────────────────────────────────── */}
      <Section
        label="Trade Support"
        badge="🏦"
        description="The COMPASS alpha engine, macro regime gate, and supporting signal infrastructure. Read these to understand what the scores mean and how to act on them."
        topics={tradeTopics}
      />

      {/* ── IT / Ops Guide ──────────────────────────────────────────── */}
      <Section
        label="IT / Ops Guide"
        badge="🖥️"
        description="Scheduling, automation, health checks, and the operations runbook. Read these to keep the Mac engine running and the data fresh."
        topics={opsTopics}
      />

      {/* ── Concepts & Basics ───────────────────────────────────────── */}
      <Section
        label="Concepts & Basics"
        badge="📖"
        description="Trading basics, technical indicators, risk metrics, strategies, market context, how the LLM works, and data sources."
        topics={otherTopics}
      />

    </div>
  );
}

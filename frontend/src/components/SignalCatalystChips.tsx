/**
 * SignalCatalystChips — Phase C-3.
 *
 * Renders the catalyst overlay on a SignalDecision row:
 *   * A divergence / alignment banner when CatalystFlag is set
 *     (the Ecopetrol moment — tech disagrees with an event that
 *     matters now).
 *   * Severity-coloured chips for each active catalyst, scoped
 *     to the symbol within the ±30 day window.
 *
 * Drop into any view that renders a SignalDecision row. Compact
 * (single row of chips), so it can sit alongside the Action +
 * Confidence pill. The banner is its own block above the chip row
 * because the divergence signal is the highest-value cockpit chip
 * we can offer right now.
 *
 * Renders nothing when catalysts is empty AND no flag — no visual
 * weight when there's nothing to say.
 */
import type { CatalystSummary, SignalDecision } from "../api/types";

const SEVERITY_COLORS: Record<CatalystSummary["severity"], string> = {
  high:   "#dc2626",
  medium: "#ca8a04",
  low:    "#16a34a",
};

const KIND_ICONS: Record<string, string> = {
  election:      "🗳️",
  earnings:      "💼",
  central_bank:  "🏦",
  commodity:     "🛢️",
  regulatory:    "⚖️",
  "m&a":         "🤝",
  ex_dividend:   "💰",
  operator_note: "📝",
};

export function SignalCatalystChips({ decision }: { decision: SignalDecision }) {
  const catalysts = decision.catalysts ?? [];
  const flag = decision.catalystFlag ?? null;
  if (catalysts.length === 0 && !flag) return null;

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
      {flag && <FlagBanner flag={flag} />}
      {catalysts.length > 0 && (
        <div style={{ display: "flex", flexWrap: "wrap", gap: 6 }}>
          {catalysts.map((c) => (
            <CatalystChip key={c.id} catalyst={c} />
          ))}
        </div>
      )}
    </div>
  );
}

function FlagBanner({
  flag,
}: { flag: "tech_event_divergence" | "tech_event_alignment" }) {
  const isDivergence = flag === "tech_event_divergence";
  const color = isDivergence ? "#dc2626" : "#1fc16b";
  const label = isDivergence
    ? "TECH vs EVENT DIVERGENCE"
    : "TECH + EVENT ALIGNMENT";
  const body = isDivergence
    ? "Technical verdict is HOLD/WAIT but a high-severity catalyst is " +
      "imminent. Decide whether the catalyst story changes your call — " +
      "this is the Ecopetrol pattern."
    : "Technical BUY signal is reinforced by a high-severity catalyst " +
      "landing in the next ~2 weeks. Sizing + risk should reflect the " +
      "convergence.";
  return (
    <div
      style={{
        padding: "8px 12px",
        background: `${color}1a`,
        border: `1px solid ${color}55`,
        borderLeft: `4px solid ${color}`,
        borderRadius: 4,
        fontSize: 11,
        color: "var(--text)",
        lineHeight: 1.4,
      }}
    >
      <span
        style={{
          color, fontWeight: 700, letterSpacing: "0.05em",
          fontSize: 10, fontFamily: "monospace",
        }}
      >
        {label}
      </span>
      <div style={{ color: "var(--text-dim)", marginTop: 2 }}>
        {body}
      </div>
    </div>
  );
}

function CatalystChip({ catalyst }: { catalyst: CatalystSummary }) {
  const color = SEVERITY_COLORS[catalyst.severity];
  const icon = KIND_ICONS[catalyst.kind] ?? "❓";
  // Days-until label. Negative = past, 0 = today, positive = future.
  const dt = catalyst.daysUntil;
  const dateLabel = dt === null || dt === undefined
    ? "(undated)"
    : dt === 0
      ? "today"
      : dt > 0
        ? `in ${dt}d`
        : `${-dt}d ago`;

  const tooltip =
    `${catalyst.kind} · severity ${catalyst.severity} · ${dateLabel}\n` +
    `${catalyst.title}\n` +
    `source: ${catalyst.source}`;

  return (
    <span
      title={tooltip}
      style={{
        display: "inline-flex",
        alignItems: "center",
        gap: 4,
        padding: "2px 8px",
        fontSize: 10,
        fontFamily: "monospace",
        borderRadius: 4,
        background: `${color}26`,
        color,
        border: `1px solid ${color}55`,
        cursor: "default",
        maxWidth: 280,
      }}
    >
      <span>{icon}</span>
      <span style={{ fontWeight: 600 }}>{catalyst.kind}</span>
      <span style={{ opacity: 0.7 }}>·</span>
      <span style={{ opacity: 0.8 }}>{dateLabel}</span>
    </span>
  );
}

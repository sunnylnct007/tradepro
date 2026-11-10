/**
 * EventChips — the known dated events between today and a contract's expiry,
 * NAMED on the row they apply to.
 *
 * OWNER BASICS 3 (9 Nov 2026): "Before proposing any new short option, check
 * the calendar of known events between today and expiry and NAME any that
 * fall inside. Never miss one." The November 2026 election sat inside every
 * expiry on the board that week and no screen said so.
 *
 * Three states, never collapsed into one:
 *   events listed       → one chip per event, high severity in the warn tone
 *   none, verified      → a quiet "no events" so a blank cell never reads as
 *                         "not checked"
 *   not verified        → "could not check" with the gaps on hover — the
 *                         opposite of all-clear
 * The shape comes from Python `events.EventsWindow.to_dict()`; every lane
 * emits the same one, so this component is the only renderer.
 */
export type KnownEvent = {
  kind: string; label: string; on: string; title: string; scope: string;
  source: string; severity: "low" | "medium" | "high" | string; short: string;
};
export type EventsWindow = {
  start?: string; end?: string; events: KnownEvent[]; count: number; high: number;
  verified: boolean; gaps: string[]; names: string;
};

const ICON: Record<string, string> = {
  earnings: "💼", fomc: "🏦", central_bank: "🏦", cpi: "📈", jobs: "👷",
  election: "🗳️", ex_dividend: "💰", index_rebalance: "⚖️", regulatory: "⚖️",
  commodity: "🛢️", "m&a": "🤝", operator_note: "📝", sector_selloff: "📉",
};

const TONE = { warn: "#d29922", bad: "#f85149", dim: "var(--text-dim)", muted: "var(--text-muted)" };

function day(iso: string): string {
  const d = new Date(iso + "T00:00:00");
  return Number.isNaN(d.getTime()) ? iso : `${d.getDate()} ${d.toLocaleString("en-GB", { month: "short" })}`;
}

export function EventChips({ w, compact = false }: { w?: EventsWindow | null; compact?: boolean }) {
  if (!w) {
    return <span style={{ fontSize: 11, color: TONE.muted }} title="This row was produced before the events check existed; re-run the lane.">events not checked</span>;
  }
  const chips = (w.events ?? []).map((e) => (
    <span key={`${e.kind}-${e.on}-${e.scope}`}
          title={`${e.title} — ${e.on} (${e.scope === "MARKET" ? "market-wide" : e.scope}; ${e.source})`}
          style={{
            display: "inline-flex", alignItems: "center", gap: 4, padding: "1px 7px",
            borderRadius: 999, fontSize: 11, whiteSpace: "nowrap",
            border: `1px solid ${e.severity === "high" ? TONE.warn : "var(--border)"}`,
            color: e.severity === "high" ? TONE.warn : TONE.dim,
          }}>
      <span aria-hidden>{ICON[e.kind] ?? "•"}</span>
      {compact ? `${e.label} ${day(e.on)}` : e.short}
    </span>
  ));
  return (
    <span style={{ display: "inline-flex", gap: 4, flexWrap: "wrap", alignItems: "center" }}>
      {chips}
      {chips.length === 0 && w.verified && (
        <span style={{ fontSize: 11, color: TONE.muted }}>no events through expiry</span>
      )}
      {!w.verified && (
        <span title={(w.gaps ?? []).join("\n")}
              style={{ fontSize: 11, color: TONE.bad, borderBottom: `1px dotted ${TONE.bad}`, cursor: "help" }}>
          {chips.length ? "+ could not check fully" : "could not check events"}
        </span>
      )}
    </span>
  );
}

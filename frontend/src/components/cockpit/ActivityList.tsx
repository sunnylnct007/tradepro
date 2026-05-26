/**
 * ActivityList — chronological event feed: one row per event,
 * colour-coded by kind, click-through to the relevant detail page
 * (OMS row or session detail). Compact so it coexists with the three
 * existing order panels rather than replacing them — both views
 * serve different scan patterns (timeline vs. bucketed by state).
 */
import { Link } from "react-router-dom";
import { activityTone, type ActivityEvent } from "../../viz/activityFeed";

export function ActivityList({ events }: { events: ActivityEvent[] }) {
  return (
    <div
      style={{
        display: "flex", flexDirection: "column", gap: 4,
        maxHeight: 360, overflowY: "auto",
        paddingRight: 4,
      }}
    >
      {events.map((e, i) => (
        <ActivityRow key={i} event={e} />
      ))}
    </div>
  );
}

function ActivityRow({ event }: { event: ActivityEvent }) {
  const tone = activityTone(event.kind);
  const ts = event.time ? new Date(event.time) : null;
  const body = (
    <div
      style={{
        display: "grid",
        gridTemplateColumns: "70px 18px 1fr",
        gap: 8,
        padding: "4px 6px",
        borderRadius: 4,
        fontSize: 12,
        alignItems: "baseline",
        borderLeft: `3px solid ${tone.fg}`,
        background: "rgba(255,255,255,0.015)",
      }}
      title={`${event.kind} · ${event.strategyId ?? ""}`}
    >
      <span style={{ fontFamily: "monospace", fontSize: 11, color: "var(--text-muted)" }}>
        {ts ? ts.toLocaleTimeString([], { hour12: false }) : "—"}
      </span>
      <span style={{ color: tone.fg, textAlign: "center" }}>{tone.icon}</span>
      <span style={{ display: "flex", gap: 8, alignItems: "baseline" }}>
        <span style={{ color: tone.fg, fontWeight: 600, fontFamily: "monospace" }}>
          {event.kind.replace(/_/g, " ")}
        </span>
        <span style={{ fontFamily: "monospace" }}>{event.label}</span>
        {event.detail && (
          <span style={{ color: "var(--text-dim)", fontSize: 11 }}>{event.detail}</span>
        )}
        {event.strategyId && (
          <span style={{ color: "var(--text-muted)", fontSize: 10, marginLeft: "auto" }}>
            {event.strategyId}
          </span>
        )}
      </span>
    </div>
  );
  if (event.href) {
    return (
      <Link to={event.href} style={{ textDecoration: "none", color: "inherit" }}>
        {body}
      </Link>
    );
  }
  return body;
}

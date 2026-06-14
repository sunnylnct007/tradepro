/**
 * CatalystsSection — Phase C-1 of the catalyst overlay (north-star
 * gap #1 per project memory). Renders the dated-events registry the
 * trader needs visible BEFORE the technical signal verdict.
 *
 * What this delivers:
 *   * Active catalysts list (default 30d back / 30d ahead) sorted by
 *     severity then date, colour-coded so high-severity items
 *     surface at a glance.
 *   * Manual-add form so an operator can capture a known event the
 *     extractor missed (Colombia election, Bessent NDR, etc.).
 *   * Dismiss button — flips status to 'dismissed' for a noise row.
 *
 * Out of scope (later slices):
 *   * C-2: news/earnings extractor sinks call POST automatically.
 *   * C-3: catalysts layer into the cockpit decision row + LLM gate.
 *   * Per-watchlist filtering (today: every symbol).
 *
 * Design notes
 *   - Self-contained primitives (no DataHealthSection import) so the
 *     two panels can evolve independently.
 *   - Severity legend tooltip + status pill match the trader-memory
 *     "explainability required" rule — every colour has a definition.
 *   - The form submits via api.upsertCatalyst (POST /api/catalysts/);
 *     the ON CONFLICT in the endpoint means re-submitting the same
 *     event updates in place without a duplicate row.
 */
import { useEffect, useMemo, useState } from "react";
import { api } from "../../api/client";

type Catalyst = Awaited<ReturnType<typeof api.catalysts>>["catalysts"][number];

const SEVERITY_COLORS: Record<Catalyst["severity"], string> = {
  high:   "#dc2626",
  medium: "#ca8a04",
  low:    "#16a34a",
};
const SEVERITY_DEFINITION: Record<Catalyst["severity"], string> = {
  high:   "this is the entire reason a trade exists (e.g. Colombia election, FOMC)",
  medium: "material event that should influence sizing or timing",
  low:    "informational; flagged for awareness but unlikely to drive a decision",
};

// Map known kinds to a tiny emoji glyph so the row is scannable at
// a glance. Unknown kinds fall back to "?". The full kind label is
// always shown next to it.
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

export function CatalystsSection() {
  return (
    <Section title="Catalysts — dated events overlay (Phase C-1)">
      <RoadmapNote />
      <CatalystsList />
      <AddCatalystForm />
    </Section>
  );
}

function RoadmapNote() {
  return (
    <div
      style={{
        padding: "10px 14px",
        marginBottom: 14,
        background: "rgba(255,255,255,0.04)",
        borderLeft: "3px solid #ca8a04",
        borderRadius: 4,
        fontSize: 11,
        color: "var(--text-dim)",
        lineHeight: 1.55,
      }}
    >
      <strong style={{ color: "var(--text)" }}>Why this section exists.</strong>{" "}
      Pure-technical signals miss event-driven trades. The classic
      example: Ecopetrol on 2026-05-21 — technicals said WAIT, but the
      Colombia election in 10 days + oil at $105 was the entire reason
      the trade existed. C-1 (this panel) is the persistence +
      operator-visibility surface; C-2 wires the existing Python news
      extractor to POST rows automatically; C-3 layers catalysts into
      the cockpit decision row + LLM gate.
    </div>
  );
}

// ─── Active catalyst list ──────────────────────────────────────────

function CatalystsList() {
  const [rows, setRows] = useState<Catalyst[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [symbolFilter, setSymbolFilter] = useState<string>("");
  const [lookahead, setLookahead] = useState<number>(30);

  const load = async () => {
    setLoading(true);
    try {
      const r = await api.catalysts({
        symbol: symbolFilter.trim() || undefined,
        lookbackDays: 30,
        lookaheadDays: lookahead,
      });
      setRows(r.catalysts);
      setError(null);
    } catch (e) {
      setError(String(e));
    } finally {
      setLoading(false);
    }
  };
  useEffect(() => { void load(); }, [lookahead, symbolFilter]);

  const onDismiss = async (id: number) => {
    if (!window.confirm("Dismiss this catalyst? It will hide from the active list. Re-add manually if needed.")) return;
    try {
      await api.dismissCatalyst(id);
      setRows((prev) => prev.filter((r) => r.id !== id));
    } catch (e) {
      setError(String(e));
    }
  };

  return (
    <Subsection title="Active catalysts">
      <Legend />

      <div style={{
        display: "flex", gap: 10, alignItems: "center",
        marginBottom: 8, fontSize: 10,
      }}>
        <span style={{ color: "var(--text-muted)" }}>Symbol:</span>
        <input
          value={symbolFilter}
          onChange={(e) => setSymbolFilter(e.target.value.toUpperCase())}
          placeholder="(all)"
          style={{
            padding: "4px 8px", fontSize: 11, fontFamily: "monospace",
            border: "1px solid var(--border)", borderRadius: 3,
            background: "transparent", color: "var(--text)",
            width: 100,
          }}
        />
        <span style={{ marginLeft: 14, color: "var(--text-muted)" }}>Lookahead:</span>
        {[7, 30, 90].map((d) => (
          <button
            key={d}
            onClick={() => setLookahead(d)}
            style={{
              padding: "2px 8px", fontSize: 10, fontWeight: 600,
              border: `1px solid ${lookahead === d ? "var(--accent, #ca8a04)" : "var(--border)"}`,
              borderRadius: 3,
              background: lookahead === d ? "rgba(202, 138, 4, 0.12)" : "transparent",
              color: lookahead === d ? "#ca8a04" : "var(--text-dim)",
              cursor: "pointer",
            }}
          >
            {d}d
          </button>
        ))}
        <span style={{
          marginLeft: 14, fontSize: 10, color: "var(--text-muted)",
        }}>
          ({rows.length} active)
        </span>
      </div>

      {loading && <Muted>Loading…</Muted>}
      {error && <ErrorText>{error}</ErrorText>}
      {!loading && !error && rows.length === 0 && (
        <div style={{
          padding: "10px 14px", border: "1px dashed var(--border)",
          borderRadius: 4, fontSize: 11, color: "var(--text-dim)",
          lineHeight: 1.55,
        }}>
          <Pill color="#6b7280">EMPTY</Pill>{" "}
          No catalysts in the current window. Use the form below to
          add a known event manually, or wait for the C-2 extractor
          to sink rows automatically.
        </div>
      )}
      {!loading && rows.length > 0 && (
        <>
          <div
            style={{
              display: "grid",
              gridTemplateColumns:
                "60px 100px 100px 1fr 110px 100px 70px",
              gap: 8, alignItems: "center",
              paddingBottom: 4, marginBottom: 4,
              borderBottom: "1px solid var(--border)",
              fontSize: 9, color: "var(--text-muted)",
              textTransform: "uppercase", letterSpacing: "0.05em",
            }}
          >
            <span>Sev</span>
            <span>Symbol</span>
            <span>Kind</span>
            <span>Title</span>
            <span>Date</span>
            <span>Source</span>
            <span style={{ textAlign: "right" }}>Action</span>
          </div>
          {[...rows].sort((a, b) => {
            // Upcoming (>= today) first, soonest first; then past, most-recent
            // first. Was unsorted (API order = oldest-first), so already-reported
            // May earnings sat at the top. ISO dates compare chronologically.
            const today = new Date().toISOString().slice(0, 10);
            const da = (a.occurs_on ?? "").slice(0, 10);
            const db = (b.occurs_on ?? "").slice(0, 10);
            if (!da || !db) return da ? -1 : db ? 1 : 0;
            const aUp = da >= today, bUp = db >= today;
            if (aUp !== bUp) return aUp ? -1 : 1;
            return aUp ? da.localeCompare(db) : db.localeCompare(da);
          }).map((row) => (
            <CatalystRow key={row.id} row={row} onDismiss={() => onDismiss(row.id)} />
          ))}
        </>
      )}
    </Subsection>
  );
}

function Legend() {
  return (
    <div
      style={{
        display: "flex", flexWrap: "wrap", gap: 14,
        fontSize: 10, color: "var(--text-muted)",
        marginBottom: 8,
      }}
    >
      <span>
        <strong style={{ color: "var(--text-dim)" }}>Severity:</strong>{" "}
        {(["high", "medium", "low"] as const).map((s) => (
          <span key={s} style={{ marginRight: 8 }}
                title={SEVERITY_DEFINITION[s]}>
            <Pill color={SEVERITY_COLORS[s]}>{s}</Pill>
          </span>
        ))}
      </span>
    </div>
  );
}

function CatalystRow({ row, onDismiss }: { row: Catalyst; onDismiss: () => void }) {
  const icon = KIND_ICONS[row.kind] ?? "❓";
  const dateLabel = row.occurs_on ?? "—";
  // Days-until / days-since indicator. Negative = past, positive = future.
  const daysDelta = useMemo(() => {
    if (!row.occurs_on) return null;
    const today = new Date();
    today.setHours(0, 0, 0, 0);
    // occurs_on can be a bare date ("2026-05-19") OR a full ISO timestamp
    // ("2026-05-19T00:00:00"); appending "T00:00:00" to the latter produced an
    // invalid Date → "NaNd ago". Take just the date part first.
    const d = new Date(row.occurs_on.slice(0, 10) + "T00:00:00");
    return Math.round((d.getTime() - today.getTime()) / 86_400_000);
  }, [row.occurs_on]);
  const deltaLabel = daysDelta === null
    ? "(undated)"
    : daysDelta === 0
      ? "today"
      : daysDelta > 0
        ? `in ${daysDelta}d`
        : `${-daysDelta}d ago`;

  return (
    <div
      style={{
        display: "grid",
        gridTemplateColumns: "60px 100px 100px 1fr 110px 100px 70px",
        gap: 8, alignItems: "center",
        padding: "6px 0",
        borderTop: "1px solid var(--border)",
        fontSize: 11,
      }}
    >
      <span>
        <Pill color={SEVERITY_COLORS[row.severity]}>{row.severity}</Pill>
      </span>
      <span style={{ fontFamily: "monospace", fontWeight: 600 }}>
        {row.symbol}
      </span>
      <span style={{ fontSize: 11 }}>
        <span style={{ marginRight: 4 }}>{icon}</span>
        <span style={{ fontFamily: "monospace", color: "var(--text-dim)" }}>
          {row.kind}
        </span>
      </span>
      <span style={{ lineHeight: 1.3 }}>
        <div>{row.title}</div>
        {row.note && (
          <div style={{ fontSize: 10, color: "var(--text-muted)", marginTop: 2 }}>
            {row.note}
          </div>
        )}
      </span>
      <span style={{ fontFamily: "monospace", fontSize: 11 }}>
        <div>{dateLabel}</div>
        <div style={{ fontSize: 9, color: "var(--text-muted)" }}>
          {deltaLabel}
        </div>
      </span>
      <span style={{
        fontFamily: "monospace", fontSize: 10,
        color: "var(--text-muted)",
      }}>
        {row.source}
      </span>
      <span style={{ textAlign: "right" }}>
        <button
          onClick={onDismiss}
          style={{
            padding: "3px 8px", fontSize: 10, fontWeight: 600,
            border: "1px solid var(--border)", borderRadius: 3,
            background: "transparent", color: "var(--text-muted)",
            cursor: "pointer",
          }}
          title="Hide from the active list (flips status to dismissed)"
        >
          Dismiss
        </button>
      </span>
    </div>
  );
}

// ─── Manual-add form ───────────────────────────────────────────────

function AddCatalystForm() {
  const [symbol, setSymbol] = useState("");
  const [kind, setKind] = useState("operator_note");
  const [occursOn, setOccursOn] = useState<string>(
    new Date().toISOString().slice(0, 10),
  );
  const [title, setTitle] = useState("");
  const [severity, setSeverity] = useState<"low" | "medium" | "high">("medium");
  const [note, setNote] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [feedback, setFeedback] = useState<string | null>(null);

  const canSubmit =
    symbol.trim().length > 0 && title.trim().length > 0 && !submitting;

  const submit = async () => {
    setSubmitting(true);
    setFeedback(null);
    try {
      const r = await api.upsertCatalyst({
        symbol: symbol.trim().toUpperCase(),
        kind: kind.trim() || "operator_note",
        occursOn: occursOn || null,
        title: title.trim(),
        source: "operator",
        severity,
        status: "active",
        payload: {},
        note: note.trim() || null,
      });
      setFeedback(`✓ saved id=${r.id} — refresh to see it in the list`);
      // Soft reset — keep symbol so the operator can add several
      // catalysts for the same name without re-typing.
      setTitle("");
      setNote("");
    } catch (e) {
      setFeedback(String(e));
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <Subsection title="Add catalyst (operator)">
      <div style={{
        fontSize: 10, color: "var(--text-muted)",
        marginBottom: 8, lineHeight: 1.55,
      }}>
        Captures a known event the extractor missed. Source is stamped
        as <code>operator</code> so the row stands apart from
        extractor-supplied catalysts in audit. Re-submitting the same
        (symbol, kind, date, operator) updates in place via the
        endpoint's ON CONFLICT.
      </div>
      <div style={{
        display: "grid",
        gridTemplateColumns: "100px 120px 130px 1fr 110px",
        gap: 8, alignItems: "center",
      }}>
        <input
          value={symbol}
          onChange={(e) => setSymbol(e.target.value.toUpperCase())}
          placeholder="SYMBOL"
          style={inputStyle}
        />
        <select
          value={kind}
          onChange={(e) => setKind(e.target.value)}
          style={inputStyle}
        >
          {[
            "operator_note", "election", "earnings", "central_bank",
            "commodity", "regulatory", "m&a", "ex_dividend",
          ].map((k) => (
            <option key={k} value={k}>{k}</option>
          ))}
        </select>
        <input
          type="date"
          value={occursOn}
          onChange={(e) => setOccursOn(e.target.value)}
          style={inputStyle}
        />
        <input
          value={title}
          onChange={(e) => setTitle(e.target.value)}
          placeholder="Title (what's the event?)"
          style={inputStyle}
        />
        <select
          value={severity}
          onChange={(e) => setSeverity(e.target.value as "low" | "medium" | "high")}
          style={inputStyle}
          title="High = this is the entire reason a trade exists"
        >
          <option value="low">low</option>
          <option value="medium">medium</option>
          <option value="high">high</option>
        </select>
      </div>
      <div style={{ marginTop: 8, display: "flex", gap: 8, alignItems: "center" }}>
        <input
          value={note}
          onChange={(e) => setNote(e.target.value)}
          placeholder="Note (optional — operator commentary)"
          style={{ ...inputStyle, flex: 1 }}
        />
        <button
          disabled={!canSubmit}
          onClick={submit}
          style={{
            padding: "6px 14px", fontSize: 12, fontWeight: 600,
            border: "none", borderRadius: 4,
            background: canSubmit ? "#1fc16b" : "var(--text-muted)",
            color: "white",
            cursor: canSubmit ? "pointer" : "default",
          }}
        >
          {submitting ? "Saving…" : "Add catalyst"}
        </button>
      </div>
      {feedback && (
        <div style={{
          marginTop: 6, fontSize: 10,
          color: feedback.startsWith("✓") ? "#1fc16b" : "var(--down)",
        }}>
          {feedback}
        </div>
      )}
    </Subsection>
  );
}

// ─── Shared primitives (intentionally self-contained) ──────────────

const inputStyle: React.CSSProperties = {
  padding: "5px 8px", fontSize: 11,
  border: "1px solid var(--border)", borderRadius: 4,
  background: "transparent", color: "var(--text)",
  fontFamily: "monospace",
};

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section
      style={{
        border: "1px solid var(--border)",
        borderRadius: 8,
        padding: 14,
        marginBottom: 14,
        background: "var(--surface-1, rgba(255,255,255,0.02))",
      }}
    >
      <h3 style={{
        margin: "0 0 10px", fontSize: 13, fontWeight: 700,
        letterSpacing: "0.04em", textTransform: "uppercase",
        color: "var(--text-dim)",
      }}>
        {title}
      </h3>
      {children}
    </section>
  );
}

function Subsection({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div style={{ marginBottom: 18 }}>
      <h4 style={{
        margin: "0 0 6px", fontSize: 11, fontWeight: 700,
        color: "var(--text)", letterSpacing: "0.03em",
      }}>
        {title}
      </h4>
      {children}
    </div>
  );
}

function Pill({ color, children }: { color: string; children: React.ReactNode }) {
  return (
    <span style={{
      display: "inline-block",
      padding: "2px 8px",
      fontSize: 10, fontWeight: 700, fontFamily: "monospace",
      borderRadius: 4,
      background: `${color}26`,
      color, border: `1px solid ${color}55`,
    }}>
      {children}
    </span>
  );
}

function Muted({ children }: { children: React.ReactNode }) {
  return <div style={{ color: "var(--text-muted)", fontSize: 12 }}>{children}</div>;
}

function ErrorText({ children }: { children: React.ReactNode }) {
  return (
    <div style={{ color: "var(--down)", fontSize: 12, marginTop: 4 }}>
      {children}
    </div>
  );
}

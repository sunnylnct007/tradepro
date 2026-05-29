/**
 * StrategyBrokerMapSection — operator-editable strategy → broker
 * routing. The .NET ApproveAsync path consults `strategy_broker_map`
 * at order-approval time; whatever the trader sets here decides where
 * the next order from that strategy actually executes.
 *
 * Design goals (per project memory: explainability + risk-aversion +
 * trust-before-breadth):
 *
 *  - Show the full resolution priority at the top so the trader can
 *    interpret what they're looking at without leaving the page.
 *  - Render EVERY registered strategy (joined from the Mac-pushed
 *    catalog), not just the mapped ones — an unmapped strategy is
 *    important to surface because it's silently using the global
 *    default and the trader may not realise that.
 *  - Per-row "effective broker" badge — what would actually route
 *    NOW for that strategy (mapped value or fallback). Removes the
 *    "is this row using fallback?" ambiguity.
 *  - Save per-row (not all-or-nothing) so a botched edit on one row
 *    doesn't risk clobbering another row's known-good mapping.
 *  - Confirm prompt before flipping an existing mapping (audit log
 *    insight: switching brokers mid-day affects every subsequent
 *    order; cheap two-click confirmation is worth it).
 *  - Show updated_at_utc + updated_by per row so an investigator can
 *    answer "who flipped IG_DEMO to T212_DEMO this morning?".
 */
import { useEffect, useMemo, useState } from "react";
import { api } from "../../api/client";

type MapRow = Awaited<ReturnType<typeof api.strategyBrokerMap>>["mappings"][number];
type CatalogStrategy = Awaited<ReturnType<typeof api.paperStrategyCatalog>>["strategies"][number];

// Sentinel returned by the dropdown when the trader picks "use global
// default" — the row is DELETEd in that case, not PUT to a string.
const USE_GLOBAL_SENTINEL = "__USE_GLOBAL_DEFAULT__";

interface JoinedRow {
  strategyId: string;
  // Empty when the strategy isn't registered in the catalog yet — DB
  // has a row for it but the Mac worker hasn't pushed the catalog.
  inCatalog: boolean;
  // Empty when the strategy is in the catalog but has no DB row —
  // routes through the global default.
  mapping: MapRow | null;
  effectiveBroker: string | null;
}

function joinCatalogAndMap(
  catalog: CatalogStrategy[],
  mappings: MapRow[],
  defaultBroker: string | null,
): JoinedRow[] {
  const byId = new Map<string, MapRow>();
  for (const m of mappings) byId.set(m.strategy_id, m);
  const seen = new Set<string>();
  const rows: JoinedRow[] = [];

  for (const c of catalog) {
    const m = byId.get(c.name) ?? null;
    rows.push({
      strategyId: c.name,
      inCatalog: true,
      mapping: m,
      effectiveBroker: m?.broker ?? defaultBroker ?? null,
    });
    seen.add(c.name);
  }
  // DB rows for strategies the catalog doesn't know about (e.g. an
  // old mapping for a strategy that was renamed). Surface them so an
  // operator can clean up.
  for (const m of mappings) {
    if (seen.has(m.strategy_id)) continue;
    rows.push({
      strategyId: m.strategy_id,
      inCatalog: false,
      mapping: m,
      effectiveBroker: m.broker,
    });
  }
  rows.sort((a, b) => a.strategyId.localeCompare(b.strategyId));
  return rows;
}

export function StrategyBrokerMapSection() {
  const [validBrokers, setValidBrokers] = useState<string[]>([]);
  const [defaultBroker, setDefaultBroker] = useState<string | null>(null);
  const [mappings, setMappings] = useState<MapRow[]>([]);
  const [catalog, setCatalog] = useState<CatalogStrategy[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  const load = async () => {
    setLoading(true);
    setError(null);
    try {
      // Two independent fetches; one failing shouldn't blank the
      // whole panel — the catalog being unavailable just means we
      // render mappings without the "registered but unmapped" rows.
      const [mapResp, catResp] = await Promise.allSettled([
        api.strategyBrokerMap(),
        api.paperStrategyCatalog(),
      ]);
      if (mapResp.status === "fulfilled") {
        setValidBrokers(mapResp.value.validBrokers);
        setDefaultBroker(mapResp.value.defaultBroker);
        setMappings(mapResp.value.mappings);
      } else {
        setError(`Couldn't load mappings: ${mapResp.reason}`);
      }
      if (catResp.status === "fulfilled") {
        setCatalog(catResp.value.strategies);
      } else {
        // Non-fatal — leave catalog empty.
        // eslint-disable-next-line no-console
        console.warn("paperStrategyCatalog failed:", catResp.reason);
      }
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { void load(); }, []);

  const joined = useMemo(
    () => joinCatalogAndMap(catalog, mappings, defaultBroker),
    [catalog, mappings, defaultBroker],
  );

  if (loading && joined.length === 0) {
    return (
      <Section title="Strategy → broker routing">
        <div style={{ color: "var(--text-muted)", fontSize: 12 }}>Loading…</div>
      </Section>
    );
  }

  return (
    <Section title="Strategy → broker routing">
      <ResolutionPriorityNote defaultBroker={defaultBroker} />
      {error && (
        <div style={{ color: "var(--down)", fontSize: 12, marginBottom: 10 }}>
          {error}
        </div>
      )}
      {joined.length === 0 && (
        <div style={{ color: "var(--text-muted)", fontSize: 12 }}>
          No strategies known. The paper-strategy catalog hasn't been pushed
          from the Mac worker yet, and the strategy_broker_map table is empty.
        </div>
      )}
      {joined.length > 0 && (
        <div
          style={{
            display: "grid",
            gridTemplateColumns: "minmax(160px, 1.2fr) 180px 180px minmax(160px, 1fr) auto",
            gap: 10, alignItems: "center",
            paddingBottom: 6, marginBottom: 4,
            borderBottom: "1px solid var(--border)",
            fontSize: 10, color: "var(--text-muted)",
            textTransform: "uppercase", letterSpacing: "0.05em",
          }}
        >
          <span>Strategy</span>
          <span>Effective broker</span>
          <span>Override</span>
          <span>Note</span>
          <span style={{ textAlign: "right" }}>Actions</span>
        </div>
      )}
      {joined.map((row) => (
        <StrategyBrokerRow
          key={row.strategyId}
          row={row}
          validBrokers={validBrokers}
          defaultBroker={defaultBroker}
          onSaved={() => void load()}
        />
      ))}
    </Section>
  );
}

function ResolutionPriorityNote({ defaultBroker }: { defaultBroker: string | null }) {
  return (
    <div
      style={{
        padding: "8px 12px",
        marginBottom: 10,
        background: "rgba(255,255,255,0.04)",
        borderLeft: "3px solid var(--neutral)",
        borderRadius: 4,
        fontSize: 11,
        color: "var(--text-dim)",
        lineHeight: 1.5,
      }}
    >
      <strong style={{ color: "var(--text)" }}>Broker resolution priority:</strong>{" "}
      1. per-call override on the trade-plan request body
      &nbsp;→&nbsp; 2. this row's <em>Override</em> column
      &nbsp;→&nbsp; 3. global default (currently{" "}
      <code style={{ background: "rgba(0,0,0,0.2)", padding: "1px 5px", borderRadius: 3 }}>
        {defaultBroker ?? "(unset → T212_DEMO)"}
      </code>)
      &nbsp;→&nbsp; 4. hardcoded T212_DEMO.{" "}
      Changes take effect on the NEXT order any strategy emits.
    </div>
  );
}

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

function StrategyBrokerRow({
  row, validBrokers, defaultBroker, onSaved,
}: {
  row: JoinedRow;
  validBrokers: string[];
  defaultBroker: string | null;
  onSaved: () => void;
}) {
  const initialBroker = row.mapping?.broker ?? USE_GLOBAL_SENTINEL;
  const [draftBroker, setDraftBroker] = useState<string>(initialBroker);
  const [draftNote, setDraftNote] = useState<string>(row.mapping?.note ?? "");
  const [saving, setSaving] = useState(false);
  const [feedback, setFeedback] = useState<string | null>(null);

  const dirty =
    draftBroker !== initialBroker ||
    (draftNote || null) !== (row.mapping?.note ?? null);

  const isOverride = draftBroker !== USE_GLOBAL_SENTINEL;

  const save = async () => {
    setSaving(true);
    setFeedback(null);
    try {
      // Confirm before flipping an existing mapping to a different
      // broker — the audit message asks for an "are you sure" because
      // mid-day broker flips affect every subsequent order.
      const wasMapped = !!row.mapping;
      const changingBroker = wasMapped && draftBroker !== row.mapping!.broker;
      if (changingBroker && isOverride) {
        const ok = window.confirm(
          `Switch ${row.strategyId} from ${row.mapping!.broker} to ${draftBroker}? ` +
          "This affects every subsequent order from this strategy.",
        );
        if (!ok) {
          setSaving(false);
          return;
        }
      }

      if (!isOverride) {
        if (wasMapped) {
          const ok = window.confirm(
            `Remove the explicit mapping for ${row.strategyId}? ` +
            `It will fall back to the global default ` +
            `(${defaultBroker ?? "T212_DEMO"}).`,
          );
          if (!ok) {
            setSaving(false);
            return;
          }
          await api.deleteStrategyBrokerMap(row.strategyId);
        }
        // else: no DB row and trader picked "use global" — no-op.
      } else {
        await api.updateStrategyBrokerMap(row.strategyId, {
          broker: draftBroker,
          note: draftNote || null,
        });
      }
      setFeedback("✓ saved");
      onSaved();
    } catch (e) {
      setFeedback(String(e));
    } finally {
      setSaving(false);
    }
  };

  const effectiveBadgeColor =
    row.effectiveBroker?.startsWith("IG") ? "#3b82f6" :
    row.effectiveBroker?.startsWith("T212") ? "#10b981" :
    row.effectiveBroker?.startsWith("IBKR") ? "#f59e0b" :
    "#9ca3af";

  return (
    <div
      style={{
        display: "grid",
        gridTemplateColumns: "minmax(160px, 1.2fr) 180px 180px minmax(160px, 1fr) auto",
        gap: 10, alignItems: "center",
        padding: "10px 0",
        borderTop: "1px solid var(--border)",
      }}
    >
      {/* Strategy + provenance */}
      <div>
        <div style={{ fontSize: 12, fontWeight: 600 }}>{row.strategyId}</div>
        <div style={{ fontSize: 10, color: "var(--text-muted)", marginTop: 2 }}>
          {!row.inCatalog && (
            <span style={{ color: "var(--down)" }}>
              ⚠ not in catalog — old / renamed strategy?
            </span>
          )}
          {row.mapping && (
            <span>
              updated {new Date(row.mapping.updated_at_utc).toLocaleString()}
              {" "}by {row.mapping.updated_by}
            </span>
          )}
          {!row.mapping && row.inCatalog && (
            <span>using global default — no explicit mapping</span>
          )}
        </div>
      </div>

      {/* Effective broker (what would actually route NOW) */}
      <div>
        <span
          style={{
            display: "inline-block",
            padding: "3px 8px",
            fontSize: 11, fontWeight: 600, fontFamily: "monospace",
            borderRadius: 4,
            background: `${effectiveBadgeColor}26`,
            color: effectiveBadgeColor,
            border: `1px solid ${effectiveBadgeColor}55`,
          }}
        >
          {row.effectiveBroker ?? "(unset)"}
        </span>
        {!row.mapping && (
          <div style={{ fontSize: 9, color: "var(--text-muted)", marginTop: 2 }}>
            via global default
          </div>
        )}
      </div>

      {/* Override dropdown */}
      <select
        value={draftBroker}
        onChange={(e) => setDraftBroker(e.target.value)}
        style={{
          padding: "5px 8px", fontSize: 12,
          border: "1px solid var(--border)", borderRadius: 4,
          background: "transparent", color: "var(--text)",
          fontFamily: "monospace",
        }}
      >
        <option value={USE_GLOBAL_SENTINEL}>(use global default)</option>
        {validBrokers.map((b) => (
          <option key={b} value={b}>{b}</option>
        ))}
      </select>

      {/* Note */}
      <input
        type="text"
        value={draftNote}
        onChange={(e) => setDraftNote(e.target.value)}
        placeholder={isOverride ? "why this override?" : "(no note)"}
        disabled={!isOverride}
        style={{
          padding: "5px 8px", fontSize: 12,
          border: "1px solid var(--border)", borderRadius: 4,
          background: "transparent", color: "var(--text)",
          opacity: isOverride ? 1 : 0.4,
        }}
      />

      {/* Actions */}
      <div style={{ display: "flex", flexDirection: "column", alignItems: "flex-end", gap: 4 }}>
        <button
          disabled={!dirty || saving}
          onClick={save}
          style={{
            padding: "5px 12px", fontSize: 12, fontWeight: 600,
            border: "none", borderRadius: 4,
            background: !dirty || saving ? "var(--text-muted)" : "#1fc16b",
            color: "white", cursor: !dirty || saving ? "default" : "pointer",
          }}
        >
          {saving ? "Saving…" : dirty ? "Save" : "Saved"}
        </button>
        {feedback && (
          <span style={{
            fontSize: 10,
            color: feedback.startsWith("✓") ? "#1fc16b" : "var(--down)",
            maxWidth: 180, textAlign: "right",
          }}>
            {feedback}
          </span>
        )}
      </div>
    </div>
  );
}

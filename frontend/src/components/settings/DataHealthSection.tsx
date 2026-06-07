/**
 * DataHealthSection — operator-facing visibility for the trustworthy-
 * data-layer roadmap (CURRENT_BACKTEST_LIMITATIONS.md + ROADMAP).
 *
 * Three sub-panels, each rendering a different concern:
 *
 *   1. Data assumptions registry
 *      Auditable list of every assumption TradePro makes about its
 *      data + backtest evidence. Severity + status colour-coded so a
 *      trader can see at a glance "what does this system pretend is
 *      true that isn't?".
 *
 *   2. Provider preferences
 *      Editable provider chain per (asset_class × resolution). The
 *      Phase-B data layer will consume this; for Phase A it's a
 *      visible knob that establishes the editing surface.
 *
 *   3. Backfill request
 *      Phase-A: shows a clearly-disabled button + tooltip explaining
 *      Phase C is the functional version. Lets the operator see the
 *      pending capability without it pretending to work.
 *
 * Design follows the project memory principles:
 *   * Explainability — every status / colour has a legend
 *   * Risk-aversion — confirm prompts before flipping a preference
 *   * Trust-before-breadth — visible "this is a Phase-A placeholder"
 *     badges so nothing pretends to do more than it does
 */
import { useEffect, useMemo, useState } from "react";
import { api } from "../../api/client";

type Assumption = Awaited<ReturnType<typeof api.dataAssumptions>>["assumptions"][number];
type Preference = Awaited<ReturnType<typeof api.dataSourcePreferences>>["preferences"][number];

const SEVERITY_COLORS: Record<Assumption["severity"], string> = {
  CRITICAL: "#dc2626",
  HIGH: "#ea580c",
  MEDIUM: "#ca8a04",
  LOW: "#65a30d",
  INFORMATIONAL: "#6b7280",
};
const STATUS_COLORS: Record<Assumption["status"], string> = {
  HONEST: "#16a34a",
  PARTIAL: "#ca8a04",
  OPTIMISTIC: "#ea580c",
  FICTIONAL: "#dc2626",
};
const STATUS_DEFINITION: Record<Assumption["status"], string> = {
  HONEST: "system tells the truth about this",
  PARTIAL: "true within limits; the limits matter for some decisions",
  OPTIMISTIC: "the system claims better than reality; expect drift",
  FICTIONAL: "the claim has no grounding; treat as unreliable",
};

export function DataHealthSection() {
  return (
    <Section title="Data Health & Trustworthy-Data Roadmap">
      <RoadmapNote />
      <AssumptionsPanel />
      <PreferencesPanel />
      <IbkrHarvesterPanel />
      <BarCacheActivityPanel />
      <CoverageMatrixPanel />
      <FillQualityPanel />
      <BackfillPanel />
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
        borderLeft: "3px solid var(--neutral)",
        borderRadius: 4,
        fontSize: 11,
        color: "var(--text-dim)",
        lineHeight: 1.55,
      }}
    >
      <strong style={{ color: "var(--text)" }}>
        Why this section exists.
      </strong>{" "}
      TradePro's backtests are trustworthy for daily strategies and
      effectively fictional for intraday strategies past 7 days
      (yfinance 1m history ceiling). This panel surfaces every
      assumption the system makes + lets the operator see the
      remediation roadmap as it ships. See{" "}
      <code style={{
        background: "rgba(0,0,0,0.2)", padding: "1px 5px", borderRadius: 3,
      }}>CURRENT_BACKTEST_LIMITATIONS.md</code>{" "}
      and the ROADMAP "Trustworthy data layer" section for the full
      design. Phase A (this panel) ships the visibility framework;
      Phases B–I close the gaps progressively.
    </div>
  );
}

// ─── Assumptions panel ───────────────────────────────────────────────

function AssumptionsPanel() {
  const [rows, setRows] = useState<Assumption[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [expanded, setExpanded] = useState<Set<string>>(new Set());

  useEffect(() => {
    (async () => {
      try {
        const r = await api.dataAssumptions();
        setRows(r.assumptions);
        setError(null);
      } catch (e) {
        setError(String(e));
      } finally {
        setLoading(false);
      }
    })();
  }, []);

  const toggle = (id: string) => {
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  return (
    <Subsection title="Data assumptions registry">
      <Legend />
      {loading && <Muted>Loading…</Muted>}
      {error && <ErrorText>{error}</ErrorText>}
      {!loading && rows.length === 0 && !error && (
        <Muted>No assumptions recorded.</Muted>
      )}
      {rows.map((row) => (
        <AssumptionRow
          key={row.id}
          row={row}
          isOpen={expanded.has(row.id)}
          onToggle={() => toggle(row.id)}
        />
      ))}
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
        {(["CRITICAL", "HIGH", "MEDIUM", "LOW", "INFORMATIONAL"] as const).map((s) => (
          <span key={s} style={{ marginRight: 8 }}>
            <Pill color={SEVERITY_COLORS[s]}>{s}</Pill>
          </span>
        ))}
      </span>
      <span>
        <strong style={{ color: "var(--text-dim)" }}>Status:</strong>{" "}
        {(["HONEST", "PARTIAL", "OPTIMISTIC", "FICTIONAL"] as const).map((s) => (
          <span key={s} style={{ marginRight: 8 }} title={STATUS_DEFINITION[s]}>
            <Pill color={STATUS_COLORS[s]}>{s}</Pill>
          </span>
        ))}
      </span>
    </div>
  );
}

function AssumptionRow({
  row, isOpen, onToggle,
}: { row: Assumption; isOpen: boolean; onToggle: () => void }) {
  return (
    <div
      style={{
        padding: "10px 0",
        borderTop: "1px solid var(--border)",
        cursor: "pointer",
      }}
      onClick={onToggle}
    >
      <div
        style={{
          display: "grid",
          gridTemplateColumns: "auto 100px 100px 1fr auto",
          gap: 10, alignItems: "center",
        }}
      >
        <span style={{
          fontSize: 12, fontFamily: "monospace", color: "var(--text-muted)",
          minWidth: 12, textAlign: "center",
        }}>
          {isOpen ? "▼" : "▶"}
        </span>
        <Pill color={SEVERITY_COLORS[row.severity]}>{row.severity}</Pill>
        <Pill color={STATUS_COLORS[row.status]}>{row.status}</Pill>
        <div style={{ fontSize: 12, color: "var(--text)" }}>
          {row.description}
        </div>
        <div style={{ fontSize: 10, color: "var(--text-muted)" }}>
          {row.id}
        </div>
      </div>
      {isOpen && (
        <div style={{ marginTop: 8, marginLeft: 22, fontSize: 11, lineHeight: 1.55 }}>
          <DetailRow label="Affects" value={row.affects.join(", ")} mono />
          <DetailRow label="Consequence" value={row.consequence} />
          <DetailRow label="Remedy (roadmap)" value={row.remedy} />
          {row.mitigation && (
            <DetailRow label="Mitigation today" value={row.mitigation} />
          )}
          <DetailRow
            label="Last reviewed"
            value={`${new Date(row.last_reviewed_at_utc).toLocaleString()} by ${row.last_reviewed_by}`}
            small
          />
        </div>
      )}
    </div>
  );
}

function DetailRow({
  label, value, mono = false, small = false,
}: { label: string; value: string; mono?: boolean; small?: boolean }) {
  return (
    <div style={{ display: "flex", gap: 10, marginTop: 4 }}>
      <span style={{
        color: "var(--text-muted)",
        minWidth: 130,
        fontSize: small ? 10 : 11,
        fontWeight: 600,
      }}>
        {label}
      </span>
      <span style={{
        color: "var(--text-dim)",
        fontSize: small ? 10 : 11,
        fontFamily: mono ? "monospace" : undefined,
      }}>
        {value}
      </span>
    </div>
  );
}

// ─── Preferences panel ───────────────────────────────────────────────

function PreferencesPanel() {
  const [validProviders, setValidProviders] = useState<string[]>([]);
  const [rows, setRows] = useState<Preference[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  const load = async () => {
    setLoading(true);
    try {
      const r = await api.dataSourcePreferences();
      setValidProviders(r.validProviders);
      setRows(r.preferences);
      setError(null);
    } catch (e) {
      setError(String(e));
    } finally {
      setLoading(false);
    }
  };
  useEffect(() => { void load(); }, []);

  return (
    <Subsection title="Provider preferences (per asset class × resolution)">
      <div style={{
        fontSize: 10, color: "var(--text-muted)", marginBottom: 8, lineHeight: 1.55,
      }}>
        The data layer (Phase B) reads this table to decide which
        provider to try first for each fetch. Comma-separated chain;
        leftmost is tried first, fall back rightward on failure.
        Editing here doesn't move bars yet — Phase B wires consumption.
      </div>
      {loading && <Muted>Loading…</Muted>}
      {error && <ErrorText>{error}</ErrorText>}
      {!loading && rows.length === 0 && !error && (
        <Muted>No preferences configured.</Muted>
      )}
      {rows.length > 0 && (
        <div
          style={{
            display: "grid",
            gridTemplateColumns: "120px 80px 1fr minmax(180px, 1.5fr) 110px",
            gap: 10, alignItems: "center",
            paddingBottom: 6, marginBottom: 4,
            borderBottom: "1px solid var(--border)",
            fontSize: 10, color: "var(--text-muted)",
            textTransform: "uppercase", letterSpacing: "0.05em",
          }}
        >
          <span>Asset class</span>
          <span>Resolution</span>
          <span>Provider chain</span>
          <span>Notes</span>
          <span style={{ textAlign: "right" }}>Actions</span>
        </div>
      )}
      {rows.map((row) => (
        <PreferenceRow
          key={`${row.asset_class}/${row.resolution}`}
          row={row}
          validProviders={validProviders}
          onSaved={() => void load()}
        />
      ))}
    </Subsection>
  );
}

function PreferenceRow({
  row, validProviders, onSaved,
}: { row: Preference; validProviders: string[]; onSaved: () => void }) {
  const [draftChain, setDraftChain] = useState<string>(row.provider_chain.join(","));
  const [draftNotes, setDraftNotes] = useState<string>(row.notes ?? "");
  const [saving, setSaving] = useState(false);
  const [feedback, setFeedback] = useState<string | null>(null);

  const dirty =
    draftChain.replace(/\s/g, "") !== row.provider_chain.join(",") ||
    (draftNotes || null) !== row.notes;

  const parsedChain = useMemo(
    () => draftChain.split(",").map((s) => s.trim()).filter(Boolean),
    [draftChain],
  );
  const unknownProviders = parsedChain.filter(
    (p) => !validProviders.includes(p),
  );

  const save = async () => {
    if (unknownProviders.length > 0) {
      setFeedback(`unknown providers: ${unknownProviders.join(", ")}`);
      return;
    }
    const ok = window.confirm(
      `Update provider chain for ${row.asset_class}/${row.resolution} to ` +
      `[${parsedChain.join(", ")}]? The Phase B data layer will pick the ` +
      `first provider on the next fetch.`,
    );
    if (!ok) return;
    setSaving(true);
    setFeedback(null);
    try {
      await api.updateDataSourcePreference(row.asset_class, row.resolution, {
        providerChain: parsedChain,
        notes: draftNotes || null,
      });
      setFeedback("✓ saved");
      onSaved();
    } catch (e) {
      setFeedback(String(e));
    } finally {
      setSaving(false);
    }
  };

  return (
    <div
      style={{
        display: "grid",
        gridTemplateColumns: "120px 80px 1fr minmax(180px, 1.5fr) 110px",
        gap: 10, alignItems: "center",
        padding: "10px 0",
        borderTop: "1px solid var(--border)",
      }}
    >
      <span style={{ fontSize: 12, fontFamily: "monospace" }}>{row.asset_class}</span>
      <span style={{ fontSize: 12, fontFamily: "monospace" }}>{row.resolution}</span>
      <input
        value={draftChain}
        onChange={(e) => setDraftChain(e.target.value)}
        placeholder="yfinance,ig,finnhub"
        style={{
          padding: "5px 8px", fontSize: 12, fontFamily: "monospace",
          border: `1px solid ${unknownProviders.length > 0 ? "var(--down)" : "var(--border)"}`,
          borderRadius: 4, background: "transparent", color: "var(--text)",
        }}
      />
      <input
        value={draftNotes}
        onChange={(e) => setDraftNotes(e.target.value)}
        placeholder="(no notes)"
        style={{
          padding: "5px 8px", fontSize: 11,
          border: "1px solid var(--border)", borderRadius: 4,
          background: "transparent", color: "var(--text)",
        }}
      />
      <div style={{ display: "flex", flexDirection: "column", alignItems: "flex-end", gap: 4 }}>
        <button
          disabled={!dirty || saving || unknownProviders.length > 0}
          onClick={save}
          style={{
            padding: "5px 12px", fontSize: 12, fontWeight: 600,
            border: "none", borderRadius: 4,
            background:
              !dirty || saving || unknownProviders.length > 0
                ? "var(--text-muted)" : "#1fc16b",
            color: "white",
            cursor:
              !dirty || saving || unknownProviders.length > 0
                ? "default" : "pointer",
          }}
        >
          {saving ? "Saving…" : dirty ? "Save" : "Saved"}
        </button>
        {feedback && (
          <span style={{
            fontSize: 10,
            color: feedback.startsWith("✓") ? "#1fc16b" : "var(--down)",
            maxWidth: 200, textAlign: "right",
          }}>
            {feedback}
          </span>
        )}
      </div>
    </div>
  );
}

// ─── Bar cache activity panel (Phase B-2) ────────────────────────────

type BarEvent = Awaited<ReturnType<typeof api.barCacheEvents>>["events"][number];
type BarHealth = Awaited<ReturnType<typeof api.barCacheHealth>>["health"][number];

const RESULT_COLORS: Record<string, string> = {
  complete: "#16a34a",
  fetched_complete: "#16a34a",
  fetched_partial: "#ca8a04",
  manifest_violation: "#dc2626",
  provider_error: "#dc2626",
  rate_limited: "#ea580c",
  no_provider: "#dc2626",
};

// ─── IBKR Harvester Panel ──────────────────────────────────────────────────────
//
// Shows IBKR bar-cache fetch history (filtered to provider_used='ibkr') plus
// a "Fetch Bars" form so the operator can harvest historical bars on demand
// without leaving the Settings page.
//
// Key UX choices (per project memory):
//   • Visible "what does IBKR give us" explainer — depth table so newcomers
//     understand WHY this panel exists (context for the numbers)
//   • Pills / badge for connection status, no dropdowns
//   • Trigger is a confirm-gated form — real-money context; can't be undone

type IbkrStatus = Awaited<ReturnType<typeof api.ibkrBarStatus>>;
type IbkrEvent  = IbkrStatus["events"][number];

// ─────────────────────────────────────────────────────────────────────────────
// HarvestReadinessGate — prominent, actionable "NOT STARTED" state shown until
// the first IBKR bar fetch succeeds. Replaces the old tiny dashed box.
//
// State machine:
//   A: endpointErr set         → backend not deployed (404/network)
//   B: status null (no err)    → endpoint up but status is null somehow
//   C: status.event_count === 0 → endpoint up, migration done, no fetches yet
//
// All states show a checklist so the operator knows exactly what's blocking.
// ─────────────────────────────────────────────────────────────────────────────
function HarvestReadinessGate({
  endpointErr,
  status,
  onRefresh,
}: {
  endpointErr: string | null;
  status: IbkrStatus | null;
  onRefresh: () => void;
}) {
  const endpointDeployed = !endpointErr;
  const migrationApplied = !!(status && status.valid_resolutions && status.valid_resolutions.length > 0);
  const twsConnected     = !!(status && status.connection_status === "ok");
  const fetchDone        = !!(status && status.event_count > 0);

  // Deduce overall state for the banner colour
  const blocking = !endpointDeployed;
  const tone = blocking ? "#ef4444" : "#f59e0b";
  const label = blocking ? "DEPLOY REQUIRED" : "HARVEST NOT STARTED";
  const emoji = blocking ? "⛔" : "🟡";

  const checkRow = (
    done: boolean,
    title: string,
    detail: string,
    actionLink?: { href: string; text: string },
  ) => (
    <div style={{
      display: "flex", gap: 8, padding: "5px 0",
      borderBottom: "1px solid var(--border-faint, #2a2d35)",
      alignItems: "flex-start",
    }}>
      <span style={{
        fontSize: 13, flexShrink: 0, marginTop: 1,
        color: done ? "#1fc16b" : tone,
      }}>
        {done ? "✓" : "✗"}
      </span>
      <div>
        <span style={{ fontSize: 11, fontWeight: 600, color: done ? "var(--text-dim)" : "var(--text)" }}>
          {title}
        </span>
        {!done && (
          <div style={{ fontSize: 10, color: "var(--text-muted)", marginTop: 2 }}>
            {detail}
            {actionLink && (
              <> — <a href={actionLink.href} style={{ color: "#63b3ed" }}>{actionLink.text}</a></>
            )}
          </div>
        )}
      </div>
    </div>
  );

  return (
    <div style={{
      border: `1px solid ${tone}`,
      borderLeft: `4px solid ${tone}`,
      borderRadius: 6,
      background: blocking ? "rgba(239,68,68,0.06)" : "rgba(245,158,11,0.06)",
      padding: "12px 14px",
      marginBottom: 14,
    }}>
      {/* ── Header ─────────────────────────────────────────────────── */}
      <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 10 }}>
        <span style={{ fontSize: 15 }}>{emoji}</span>
        <strong style={{ fontSize: 13, color: tone }}>
          IBKR BAR CACHE — {label}
        </strong>
        <span style={{ fontSize: 10, color: "var(--text-muted)", marginLeft: "auto" }}>
          0 symbols cached · 0 fetches recorded
        </span>
        <button
          onClick={onRefresh}
          style={{
            padding: "2px 8px", fontSize: 10, fontWeight: 600,
            border: "1px solid var(--border)", borderRadius: 3,
            background: "transparent", color: "var(--text-dim)", cursor: "pointer",
          }}
        >
          Refresh
        </button>
      </div>

      {/* ── Readiness checklist ─────────────────────────────────────── */}
      <div style={{ marginBottom: 8 }}>
        {checkRow(
          true,
          "IBKRProvider loaded in Python worker",
          "Already done — ib_insync provider is registered in the Mac data worker.",
        )}
        {checkRow(
          endpointDeployed,
          "Backend redeployed (DataTrustEndpoints.cs + /ibkr/* routes)",
          endpointErr
            ? `Endpoint returned: ${endpointErr.slice(0, 80)}. ` +
              "The backend on AWS (http://16.60.201.137/) needs to be rebuilt " +
              "and redeployed to pick up the new /ibkr/status + /ibkr/fetch-bars routes."
            : "",
        )}
        {checkRow(
          migrationApplied,
          "Migration 045 applied (CHECK constraint + ibkr chain seeding)",
          "Run 045_data_source_preferences_add_ibkr.sql on the live Postgres DB. " +
          "This adds 'ibkr' to the provider_chain CHECK constraint and prepends " +
          "ibkr to the us_equity/us_etf 1m chains.",
        )}
        {checkRow(
          twsConnected,
          "TWS / IB Gateway online and accepting connections",
          status
            ? `Current: connection_status=${status.connection_status}. ${status.connection_hint}`
            : "Status unknown until backend is deployed. Start TWS on the Mac, ensure " +
              "API is enabled (File › Global Configuration › API › Settings), " +
              "and that TRADEPRO_IBKR_HOST/PORT env vars match.",
        )}
        {checkRow(
          fetchDone,
          "First IBKR bar fetch triggered and recorded",
          "Use the Fetch Bars form below, or trigger via MCP: ibkr_fetch_bars(). " +
          "Start with AAPL at 1m for the last year to validate the pipeline end-to-end.",
        )}
      </div>

      {/* ── Why this matters ────────────────────────────────────────── */}
      <div style={{
        fontSize: 10, color: "var(--text-muted)",
        lineHeight: 1.55, marginTop: 4,
        borderTop: "1px solid var(--border-faint, #2a2d35)", paddingTop: 6,
      }}>
        Until at least one IBKR fetch completes, the strategy universe runs on
        yfinance's 7-day 1m depth limit — backtests, the capital gate, and
        any walk-forward tests will see at most one week of intraday history.
        IBKR provides ≈1 year at 1m, ≈3 years at 5m/15m/30m, and decades at 1d.
      </div>
    </div>
  );
}

const IBKR_RESULT_COLORS: Record<string, string> = {
  complete:           "#1fc16b",
  fetched_complete:   "#1fc16b",
  fetched_partial:    "#ca8a04",
  provider_error:     "#dc2626",
  rate_limited:       "#ea580c",
  no_provider:        "#dc2626",
  manifest_violation: "#dc2626",
};

function IbkrHarvesterPanel() {
  const [status, setStatus]     = useState<IbkrStatus | null>(null);
  const [loading, setLoading]   = useState(true);
  const [fetchErr, setFetchErr] = useState<string | null>(null);

  // Fetch-bars form state
  const [sym, setSym]         = useState("AAPL");
  const [ac, setAc]           = useState("us_equity");
  const [res, setRes]         = useState("1m");
  const [fromDate, setFrom]   = useState(() => {
    const d = new Date();
    d.setFullYear(d.getFullYear() - 1);
    return d.toISOString().slice(0, 10);
  });
  const [toDate, setTo]       = useState(() =>
    new Date().toISOString().slice(0, 10));
  const [busy, setBusy]       = useState(false);
  const [actionFeedback, setActionFeedback] = useState<string | null>(null);

  const load = () => {
    setLoading(true);
    setFetchErr(null);
    api.ibkrBarStatus({ limit: 30 })
      .then(setStatus)
      .catch((e: unknown) => setFetchErr(String(e)))
      .finally(() => setLoading(false));
  };

  useEffect(() => { load(); }, []);

  const onFetch = async () => {
    const confirmed = window.confirm(
      `Enqueue an IBKR bar fetch?\n\n` +
      `  ${sym.trim()} (${ac}) @ ${res}\n` +
      `  ${fromDate} → ${toDate}\n\n` +
      `TWS / IB Gateway must be running at the configured host:port. ` +
      `The Mac data-worker will call IBKRProvider.fetch() through the ` +
      `configured provider chain. Bars are written to the local bar cache ` +
      `(~/.tradepro/bar_cache). This is additive — existing partitions ` +
      `are not overwritten.`
    );
    if (!confirmed) return;
    setBusy(true);
    setActionFeedback(null);
    try {
      const res2 = await api.ibkrFetchBars({
        canonical:  sym.trim(),
        asset_class: ac,
        resolution: res,
        from:       fromDate,
        to:         toDate,
      });
      if ((res2 as any).error)
        setActionFeedback(`✗ ${(res2 as any).error}`);
      else
        setActionFeedback(`✓ queued — job ${String((res2 as any).request_id ?? "").slice(0, 8)}…`);
      load(); // refresh event list
    } catch (e) {
      setActionFeedback(`✗ ${String(e)}`);
    } finally {
      setBusy(false);
    }
  };

  const depthRows = status
    ? Object.entries(status.depth_summary)
    : [["1m","≈1 year"],["5m","≈3 years"],["15m","≈3.5 years"],["1h","≈3.5 years"],["1d","decades"]];

  return (
    <Subsection title="IBKR Bar Harvester">
      {/* ── Explainer ─────────────────────────────────────────────── */}
      <div style={{
        padding: "8px 12px",
        background: "rgba(99,179,237,0.07)",
        borderLeft: "3px solid #63b3ed",
        borderRadius: 4,
        fontSize: 11,
        color: "var(--text-dim)",
        lineHeight: 1.55,
        marginBottom: 12,
      }}>
        <strong style={{ color: "var(--text)" }}>
          Why IBKR? — it closes the yfinance 7-day 1m cap.
        </strong>{" "}
        IBKRProvider fetches historical bars via <code>ib_insync</code> (TWS
        or IB Gateway). Depth per resolution:
        <span style={{ display: "inline-flex", gap: 8, flexWrap: "wrap", marginLeft: 8 }}>
          {depthRows.map(([r, d]) => (
            <span key={String(r)} style={{ fontFamily: "monospace" }}>
              <strong>{r}</strong> → {d}
            </span>
          ))}
        </span>
        . Chain falls through to IG → yfinance on connection failure.
      </div>

      {/* ── Harvest readiness gate ─────────────────────────────────── */}
      {/*  Shows whenever the cache is empty or the endpoint isn't live  */}
      {!loading && (fetchErr || !status || status.event_count === 0) && (
        <HarvestReadinessGate
          endpointErr={fetchErr}
          status={status}
          onRefresh={load}
        />
      )}

      {/* ── Connection status badge (only once harvest has started) ── */}
      {!loading && !fetchErr && status && status.event_count > 0 && (
        <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 12 }}>
          <span style={{ fontSize: 11, fontWeight: 600, color: "var(--text-dim)" }}>
            IBKR
          </span>
          <Pill color={
            status.connection_status === "ok" ? "#1fc16b" :
            status.connection_status === "degraded" ? "#ca8a04" : "#dc2626"
          }>
            {status.connection_status}
          </Pill>
          {status.connection_hint && (
            <span style={{ fontSize: 10, color: "var(--text-muted)" }}>
              {status.connection_hint}
            </span>
          )}
          {status.last_fetch_at_utc && (
            <span style={{ fontSize: 10, color: "var(--text-muted)", marginLeft: 4 }}>
              · last fetch {status.last_fetch_at_utc.slice(0, 19).replace("T"," ")} UTC
            </span>
          )}
          <button
            onClick={load}
            disabled={loading}
            style={{
              marginLeft: "auto",
              padding: "2px 8px", fontSize: 10, fontWeight: 600,
              border: "1px solid var(--border)", borderRadius: 3,
              background: "transparent", color: "var(--text-dim)",
              cursor: loading ? "default" : "pointer",
            }}
          >
            {loading ? "Refreshing…" : "Refresh"}
          </button>
        </div>
      )}
      {loading && (
        <div style={{ marginBottom: 12 }}>
          <Pill color="#6b7280">loading…</Pill>
        </div>
      )}

      {/* ── Fetch-bars form ────────────────────────────────────────── */}
      <div style={{
        display: "grid",
        gridTemplateColumns: "100px 110px 70px 120px 120px 1fr",
        gap: 6, alignItems: "end",
        marginBottom: 10,
      }}>
        {[
          { label: "Symbol", value: sym, setter: setSym, placeholder: "AAPL" },
          { label: "Asset class", value: ac, setter: setAc, placeholder: "us_equity" },
          { label: "Resolution", value: res, setter: setRes, placeholder: "1m" },
          { label: "From", value: fromDate, setter: setFrom, placeholder: "YYYY-MM-DD" },
          { label: "To", value: toDate, setter: setTo, placeholder: "YYYY-MM-DD" },
        ].map(({ label, value, setter, placeholder }) => (
          <div key={label} style={{ display: "flex", flexDirection: "column", gap: 3 }}>
            <label style={{ fontSize: 10, color: "var(--text-dim)", fontWeight: 600 }}>
              {label}
            </label>
            <input
              value={value}
              onChange={e => setter(e.target.value)}
              placeholder={placeholder}
              style={{
                padding: "4px 6px", fontSize: 11,
                background: "var(--bg-alt, #1a1d23)",
                border: "1px solid var(--border)",
                borderRadius: 3, color: "var(--text)",
                fontFamily: "monospace",
              }}
            />
          </div>
        ))}
        <div style={{ display: "flex", flexDirection: "column", gap: 3 }}>
          <label style={{ fontSize: 10, color: "transparent" }}>·</label>
          <button
            onClick={onFetch}
            disabled={busy}
            style={{
              padding: "5px 12px", fontSize: 11, fontWeight: 700,
              border: "1px solid #63b3ed", borderRadius: 3,
              background: busy ? "transparent" : "rgba(99,179,237,0.12)",
              color: busy ? "var(--text-muted)" : "#63b3ed",
              cursor: busy ? "default" : "pointer",
            }}
            title="Enqueue an IBKR bar fetch (additive; does not overwrite)"
          >
            {busy ? "Queuing…" : "Fetch Bars"}
          </button>
        </div>
      </div>
      {actionFeedback && (
        <div style={{
          fontSize: 11, marginBottom: 8,
          color: actionFeedback.startsWith("✓") ? "#1fc16b" : "var(--down)",
        }}>
          {actionFeedback}
        </div>
      )}

      {/* ── Recent IBKR fetch events ───────────────────────────────── */}
      <div style={{ fontSize: 11, color: "var(--text-dim)", marginBottom: 6 }}>
        Recent IBKR fetches
        {status && status.event_count > 0 && (
          <span style={{ marginLeft: 6, fontFamily: "monospace", color: "var(--text-muted)" }}>
            ({status.event_count} total · {status.success_count_last_n} successful)
          </span>
        )}
      </div>

      {status && status.event_count > 0 ? (
        <div style={{ overflowX: "auto" }}>
          <table style={{
            width: "100%", borderCollapse: "collapse",
            fontSize: 10, fontFamily: "monospace",
          }}>
            <thead>
              <tr style={{ borderBottom: "1px solid var(--border)", textAlign: "left" }}>
                {["Time (UTC)","Symbol","AC","Res","Range","Result","Rows","ms"].map(h => (
                  <th key={h} style={{ padding: "3px 8px", color: "var(--text-dim)", fontWeight: 600 }}>
                    {h}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {(status?.events ?? []).map(ev => (
                <IbkrEventRow key={ev.id} ev={ev} />
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Subsection>
  );
}

function IbkrEventRow({ ev }: { ev: IbkrEvent }) {
  const color = IBKR_RESULT_COLORS[ev.result] ?? "#6b7280";
  const rangeStr = ev.range_start_utc
    ? `${ev.range_start_utc.slice(0,10)} → ${ev.range_end_utc.slice(0,10)}`
    : "—";
  return (
    <tr
      style={{ borderBottom: "1px solid var(--border-faint, #2a2d35)" }}
      title={ev.error_message ?? undefined}
    >
      <td style={{ padding: "3px 8px", color: "var(--text-dim)" }}>
        {ev.occurred_at_utc.slice(0, 19).replace("T", " ")}
      </td>
      <td style={{ padding: "3px 8px", fontWeight: 600 }}>{ev.canonical}</td>
      <td style={{ padding: "3px 8px", color: "var(--text-dim)" }}>{ev.asset_class}</td>
      <td style={{ padding: "3px 8px" }}>{ev.resolution}</td>
      <td style={{ padding: "3px 8px", color: "var(--text-dim)" }}>{rangeStr}</td>
      <td style={{ padding: "3px 8px" }}>
        <Pill color={color}>{ev.result}</Pill>
        {ev.error_class && (
          <span style={{ marginLeft: 4, color: "var(--down)", fontSize: 9 }}>
            ({ev.error_class})
          </span>
        )}
      </td>
      <td style={{ padding: "3px 8px", textAlign: "right" }}>
        {ev.rows_returned != null ? ev.rows_returned.toLocaleString() : "—"}
      </td>
      <td style={{ padding: "3px 8px", textAlign: "right", color: "var(--text-dim)" }}>
        {ev.latency_ms != null ? ev.latency_ms.toLocaleString() : "—"}
      </td>
    </tr>
  );
}

function BarCacheActivityPanel() {
  const [events, setEvents] = useState<BarEvent[]>([]);
  const [health, setHealth] = useState<BarHealth[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = async () => {
    setLoading(true);
    try {
      const [e, h] = await Promise.allSettled([
        api.barCacheEvents({ limit: 25 }),
        api.barCacheHealth(),
      ]);
      if (e.status === "fulfilled") setEvents(e.value.events);
      else setError(`couldn't load events: ${e.reason}`);
      if (h.status === "fulfilled") setHealth(h.value.health);
      // health failure is non-fatal — events alone are still useful
      setError(null);
    } finally {
      setLoading(false);
    }
  };
  useEffect(() => { void load(); }, []);

  if (loading && events.length === 0 && health.length === 0) {
    return (
      <Subsection title="Bar cache activity">
        <Muted>Loading…</Muted>
      </Subsection>
    );
  }

  return (
    <Subsection title="Bar cache activity">
      <div
        style={{
          fontSize: 10, color: "var(--text-muted)",
          marginBottom: 8, lineHeight: 1.55,
        }}
      >
        Telemetry from the trustworthy bar cache (Phase B-1 + B-2). Each
        BarStore fetch emits one event below. Per-symbol health is the
        last-touch snapshot. If nothing's here, no fetches have hit this
        backend yet — run the CLI with{" "}
        <code style={{
          background: "rgba(0,0,0,0.2)", padding: "1px 5px", borderRadius: 3,
          fontSize: 10,
        }}>
          tradepro-bar-cache-get --api-base &lt;url&gt;
        </code>{" "}
        to populate.
      </div>
      {error && <ErrorText>{error}</ErrorText>}

      <h5 style={{
        margin: "10px 0 6px", fontSize: 10, fontWeight: 700,
        color: "var(--text-dim)", letterSpacing: "0.05em",
        textTransform: "uppercase",
      }}>
        Per-symbol coverage ({health.length})
      </h5>
      {health.length === 0 && <Muted>No health snapshots yet.</Muted>}
      {health.length > 0 && (
        <div
          style={{
            display: "grid",
            gridTemplateColumns:
              "100px 80px 100px 120px 110px 110px 70px 200px",
            gap: 8, alignItems: "center",
            paddingBottom: 4, marginBottom: 4,
            borderBottom: "1px solid var(--border)",
            fontSize: 9, color: "var(--text-muted)",
            textTransform: "uppercase", letterSpacing: "0.05em",
          }}
        >
          <span>Canonical</span>
          <span>Asset</span>
          <span>Last result</span>
          <span>Last provider</span>
          <span>Coverage start</span>
          <span>Coverage end</span>
          <span>Gaps</span>
          <span style={{ textAlign: "right" }}>Actions</span>
        </div>
      )}
      {health.map((row) => (
        <HealthRow key={`${row.canonical}/${row.asset_class}`} row={row} />
      ))}

      <h5 style={{
        margin: "16px 0 6px", fontSize: 10, fontWeight: 700,
        color: "var(--text-dim)", letterSpacing: "0.05em",
        textTransform: "uppercase",
      }}>
        Recent fetch events (showing last {events.length})
      </h5>
      {events.length === 0 && <Muted>No telemetry events yet.</Muted>}
      {events.length > 0 && (
        <div
          style={{
            display: "grid",
            gridTemplateColumns:
              "130px 80px 60px 70px 110px 110px 1fr",
            gap: 8, alignItems: "center",
            paddingBottom: 4, marginBottom: 4,
            borderBottom: "1px solid var(--border)",
            fontSize: 9, color: "var(--text-muted)",
            textTransform: "uppercase", letterSpacing: "0.05em",
          }}
        >
          <span>When</span>
          <span>Symbol</span>
          <span>Res</span>
          <span>Latency</span>
          <span>Result</span>
          <span>Provider</span>
          <span>Chain</span>
        </div>
      )}
      {events.map((ev) => (
        <div
          key={ev.id}
          style={{
            display: "grid",
            gridTemplateColumns:
              "130px 80px 60px 70px 110px 110px 1fr",
            gap: 8, alignItems: "center",
            padding: "5px 0",
            borderTop: "1px solid var(--border)",
            fontSize: 10,
          }}
        >
          <span style={{ fontFamily: "monospace", color: "var(--text-dim)" }}>
            {new Date(ev.occurred_at_utc).toLocaleString()}
          </span>
          <span style={{ fontFamily: "monospace", fontWeight: 600 }}>
            {ev.canonical}
          </span>
          <span style={{ fontFamily: "monospace" }}>{ev.resolution}</span>
          <span style={{ fontFamily: "monospace", textAlign: "right" }}>
            {ev.latency_ms}ms
          </span>
          <span>
            <Pill color={RESULT_COLORS[ev.result] ?? "#6b7280"}>
              {ev.result}
            </Pill>
          </span>
          <span style={{ fontFamily: "monospace", color: "var(--text-dim)" }}>
            {ev.provider_used ?? "—"}
          </span>
          <span style={{ fontFamily: "monospace", color: "var(--text-muted)" }}>
            {(ev.source_chain ?? []).join(" → ")}
          </span>
        </div>
      ))}
    </Subsection>
  );
}

function HealthRow({ row }: { row: BarHealth }) {
  const [busyKind, setBusyKind] = useState<"validate" | "backfill" | "reload" | null>(null);
  const [feedback, setFeedback] = useState<string | null>(null);
  const [reloadModalOpen, setReloadModalOpen] = useState(false);

  const onValidate = async () => {
    const ok = window.confirm(
      `Enqueue a data_validate op for ${row.canonical} (${row.asset_class})?\n\n` +
      `The Mac data-worker will walk every cached partition for this ` +
      `symbol and report which ones are complete vs incomplete. ` +
      `Non-destructive — only reads files.`,
    );
    if (!ok) return;
    setBusyKind("validate");
    setFeedback(null);
    try {
      const res = await api.runDataValidate({
        canonical: row.canonical,
        asset_class: row.asset_class,
      });
      setFeedback(`✓ queued (${res.request_id.slice(0, 8)}…)`);
    } catch (e) {
      setFeedback(String(e));
    } finally {
      setBusyKind(null);
    }
  };

  const onBackfill = async () => {
    // Reasonable defaults: pick up from the existing coverage_end_date
    // when present, otherwise default to one year back. The operator
    // can override either with prompt() inputs (good enough for v1;
    // a proper modal lands in a follow-up if the field overhead bites).
    const today = new Date().toISOString().slice(0, 10);
    const defaultFrom = row.coverage_end_date ?? _oneYearAgoIso();
    const fromDate = window.prompt(
      `Backfill ${row.canonical} (${row.asset_class}) starting from which date?\n` +
      `Format: YYYY-MM-DD`,
      defaultFrom,
    );
    if (fromDate == null || !fromDate.trim()) return;
    const toDate = window.prompt(
      `…up to which date?\nFormat: YYYY-MM-DD (or leave as today)`,
      today,
    );
    if (toDate == null || !toDate.trim()) return;
    const resolution = window.prompt(
      `Which resolution? (1m / 5m / 15m / 30m / 1h / 1d)`,
      row.last_fetched_resolution ?? "1d",
    );
    if (resolution == null || !resolution.trim()) return;
    const confirmed = window.confirm(
      `Enqueue a data_backfill op?\n\n` +
      `  ${row.canonical} (${row.asset_class}) @ ${resolution.trim()}\n` +
      `  ${fromDate.trim()} → ${toDate.trim()}\n\n` +
      `The Mac data-worker will fetch bars through the configured ` +
      `provider chain (see Provider preferences above). Additive — ` +
      `existing partitions are not overwritten.`,
    );
    if (!confirmed) return;
    setBusyKind("backfill");
    setFeedback(null);
    try {
      const res = await api.runDataBackfill({
        canonical: row.canonical,
        asset_class: row.asset_class,
        resolution: resolution.trim(),
        from: fromDate.trim(),
        to: toDate.trim(),
      });
      setFeedback(`✓ queued (${res.request_id.slice(0, 8)}…)`);
    } catch (e) {
      setFeedback(String(e));
    } finally {
      setBusyKind(null);
    }
  };

  const validateBusy = busyKind === "validate";
  const backfillBusy = busyKind === "backfill";
  const reloadBusy = busyKind === "reload";
  const anyBusy = busyKind !== null;

  return (
    <div
      style={{
        display: "grid",
        gridTemplateColumns:
          "100px 80px 100px 120px 110px 110px 70px 200px",
        gap: 8, alignItems: "center",
        padding: "6px 0",
        borderTop: "1px solid var(--border)",
        fontSize: 11,
      }}
    >
      <span style={{ fontFamily: "monospace", fontWeight: 600 }}>
        {row.canonical}
      </span>
      <span style={{ fontFamily: "monospace", color: "var(--text-dim)" }}>
        {row.asset_class}
      </span>
      <span>
        {row.last_fetched_result ? (
          <Pill color={RESULT_COLORS[row.last_fetched_result] ?? "#6b7280"}>
            {row.last_fetched_result}
          </Pill>
        ) : <Muted>—</Muted>}
      </span>
      <span style={{
        fontFamily: "monospace", fontSize: 10,
        color: "var(--text-dim)",
      }}>
        {row.last_fetched_provider ?? "—"}
      </span>
      <span style={{ fontFamily: "monospace", fontSize: 10 }}>
        {row.coverage_start_date ?? "—"}
      </span>
      <span style={{ fontFamily: "monospace", fontSize: 10 }}>
        {row.coverage_end_date ?? "—"}
      </span>
      <span style={{
        color: row.missing_days_count > 0 ? "var(--down)" : "var(--text-dim)",
        fontWeight: row.missing_days_count > 0 ? 600 : 400,
      }}>
        {row.missing_days_count}
      </span>
      <div style={{ display: "flex", flexDirection: "column", alignItems: "flex-end", gap: 2 }}>
        <div style={{ display: "flex", gap: 4 }}>
          <button
            disabled={anyBusy}
            onClick={onValidate}
            style={{
              padding: "3px 8px", fontSize: 10, fontWeight: 600,
              border: "1px solid var(--border)", borderRadius: 3,
              background: "transparent",
              color: anyBusy ? "var(--text-muted)" : "var(--text)",
              cursor: anyBusy ? "default" : "pointer",
            }}
            title="Enqueue a data_validate op for this symbol"
          >
            {validateBusy ? "Queuing…" : "Validate"}
          </button>
          <button
            disabled={anyBusy}
            onClick={onBackfill}
            style={{
              padding: "3px 8px", fontSize: 10, fontWeight: 600,
              border: "1px solid var(--border)", borderRadius: 3,
              background: "transparent",
              color: anyBusy ? "var(--text-muted)" : "var(--text)",
              cursor: anyBusy ? "default" : "pointer",
            }}
            title="Enqueue a data_backfill op for this symbol (additive)"
          >
            {backfillBusy ? "Queuing…" : "Backfill"}
          </button>
          <button
            disabled={anyBusy}
            onClick={() => setReloadModalOpen(true)}
            style={{
              padding: "3px 8px", fontSize: 10, fontWeight: 600,
              border: "1px solid #dc2626", borderRadius: 3,
              background: "transparent",
              color: anyBusy ? "var(--text-muted)" : "#dc2626",
              cursor: anyBusy ? "default" : "pointer",
            }}
            title="Enqueue a data_reload op — OVERWRITES existing partitions"
          >
            {reloadBusy ? "Queuing…" : "Reload"}
          </button>
        </div>
        {feedback && (
          <span style={{
            fontSize: 9,
            color: feedback.startsWith("✓") ? "#1fc16b" : "var(--down)",
          }}>
            {feedback}
          </span>
        )}
      </div>

      {reloadModalOpen && (
        <ReloadConfirmModal
          row={row}
          onClose={() => setReloadModalOpen(false)}
          onConfirm={async ({ from, to, resolution, reason }) => {
            setReloadModalOpen(false);
            setBusyKind("reload");
            setFeedback(null);
            try {
              const res = await api.runDataReload({
                canonical: row.canonical,
                asset_class: row.asset_class,
                resolution,
                from,
                to,
                reason,
              });
              setFeedback(`✓ queued (${res.request_id.slice(0, 8)}…)`);
            } catch (e) {
              setFeedback(String(e));
            } finally {
              setBusyKind(null);
            }
          }}
        />
      )}
    </div>
  );
}

function ReloadConfirmModal({
  row, onClose, onConfirm,
}: {
  row: BarHealth;
  onClose: () => void;
  onConfirm: (args: {
    from: string;
    to: string;
    resolution: string;
    reason: string;
  }) => Promise<void>;
}) {
  const [resolution, setResolution] = useState(
    row.last_fetched_resolution ?? "1d",
  );
  const [from, setFrom] = useState(row.coverage_start_date ?? _oneYearAgoIso());
  const [to, setTo] = useState(
    row.coverage_end_date ?? new Date().toISOString().slice(0, 10),
  );
  const [reason, setReason] = useState("");
  const [typedCanonical, setTypedCanonical] = useState("");
  const [submitting, setSubmitting] = useState(false);

  const reasonValid = reason.trim().length >= 10;
  const canonicalMatches = typedCanonical.trim() === row.canonical;
  const canSubmit =
    reasonValid && canonicalMatches && from && to && resolution && !submitting;

  return (
    <div
      style={{
        position: "fixed", inset: 0,
        background: "rgba(0,0,0,0.7)",
        display: "flex", alignItems: "center", justifyContent: "center",
        zIndex: 10,
      }}
      onClick={onClose}
    >
      <div
        onClick={(e) => e.stopPropagation()}
        style={{
          background: "var(--bg)",
          border: "2px solid #dc2626",
          borderRadius: 8,
          padding: "20px 24px",
          maxWidth: 540,
          width: "92%",
          color: "var(--text)",
          boxShadow: "0 8px 32px rgba(0,0,0,0.6)",
        }}
      >
        <div style={{
          fontSize: 12, fontWeight: 700, color: "#dc2626",
          textTransform: "uppercase", letterSpacing: "0.05em",
          marginBottom: 4,
        }}>
          ⚠ Destructive operation
        </div>
        <h3 style={{ margin: "0 0 12px", fontSize: 16 }}>
          Reload {row.canonical} ({row.asset_class})
        </h3>
        <p style={{
          margin: "0 0 14px", fontSize: 12, color: "var(--text-dim)",
          lineHeight: 1.55,
        }}>
          This will <strong style={{ color: "#dc2626" }}>overwrite</strong>{" "}
          existing cached partitions for{" "}
          <code>{row.canonical}/{row.asset_class}</code> in the chosen
          range. The provider chain is consulted from scratch (force_refresh
          mode). Use this when a corp action drifted the cached prices, a
          provider revision is known-bad, or you need a clean re-fetch.
        </p>

        <Field label="Resolution">
          <input
            type="text"
            value={resolution}
            onChange={(e) => setResolution(e.target.value)}
            placeholder="1m / 5m / 15m / 30m / 1h / 1d"
            style={modalInputStyle}
          />
        </Field>
        <Field label="From">
          <input
            type="date"
            value={from}
            onChange={(e) => setFrom(e.target.value)}
            style={modalInputStyle}
          />
        </Field>
        <Field label="To">
          <input
            type="date"
            value={to}
            onChange={(e) => setTo(e.target.value)}
            style={modalInputStyle}
          />
        </Field>
        <Field label="Reason (≥10 chars, recorded in audit log)">
          <textarea
            value={reason}
            onChange={(e) => setReason(e.target.value)}
            placeholder='e.g. "AAPL 2:1 split on 2024-06-10 — yfinance adjusted prices drifted; re-pull through IG"'
            rows={2}
            style={{ ...modalInputStyle, resize: "vertical", minHeight: 48 }}
          />
          {!reasonValid && reason.length > 0 && (
            <div style={{ fontSize: 10, color: "#dc2626", marginTop: 2 }}>
              Reason must be at least 10 characters.
            </div>
          )}
        </Field>
        <Field label={`Type "${row.canonical}" to confirm`}>
          <input
            type="text"
            value={typedCanonical}
            onChange={(e) => setTypedCanonical(e.target.value)}
            placeholder={row.canonical}
            style={{
              ...modalInputStyle,
              borderColor: canonicalMatches
                ? "#16a34a" : "var(--border)",
            }}
          />
        </Field>

        <div style={{
          marginTop: 18, display: "flex", gap: 8,
          justifyContent: "flex-end",
        }}>
          <button
            onClick={onClose}
            style={{
              padding: "6px 14px", fontSize: 12,
              border: "1px solid var(--border)", borderRadius: 4,
              background: "transparent", color: "var(--text)",
              cursor: "pointer",
            }}
          >
            Cancel
          </button>
          <button
            disabled={!canSubmit}
            onClick={async () => {
              setSubmitting(true);
              try {
                await onConfirm({
                  from, to, resolution: resolution.trim(),
                  reason: reason.trim(),
                });
              } finally {
                setSubmitting(false);
              }
            }}
            style={{
              padding: "6px 14px", fontSize: 12, fontWeight: 600,
              border: "none", borderRadius: 4,
              background: canSubmit ? "#dc2626" : "var(--text-muted)",
              color: "white",
              cursor: canSubmit ? "pointer" : "default",
            }}
          >
            {submitting ? "Queuing…" : "Reload (overwrite)"}
          </button>
        </div>
      </div>
    </div>
  );
}

const modalInputStyle: React.CSSProperties = {
  width: "100%", padding: "6px 8px", fontSize: 12,
  border: "1px solid var(--border)", borderRadius: 4,
  background: "transparent", color: "var(--text)",
  fontFamily: "monospace",
};

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div style={{ marginBottom: 10 }}>
      <label style={{
        display: "block", fontSize: 10, fontWeight: 600,
        color: "var(--text-dim)", textTransform: "uppercase",
        letterSpacing: "0.04em", marginBottom: 3,
      }}>
        {label}
      </label>
      {children}
    </div>
  );
}

function _oneYearAgoIso(): string {
  const d = new Date();
  d.setFullYear(d.getFullYear() - 1);
  return d.toISOString().slice(0, 10);
}

// ─── Coverage matrix panel (Phase G-1) ───────────────────────────────
//
// Per-(canonical × month) status grid driven by the new
// /bar-cache/coverage-matrix endpoint. Rows = symbols, columns =
// months in the rolling window. Cells colour-coded so an operator
// can see at a glance which months need backfilling.
//
// G-1 minimum: visibility only. Click-to-drill modal lands in G-2
// (today the tooltip carries the last_result + provider + counts).

type CoverageMatrix = Awaited<ReturnType<typeof api.barCacheCoverageMatrix>>;
type CoverageCell = CoverageMatrix["rows"][number]["cells"][string];

const COVERAGE_STATUS_COLORS: Record<CoverageCell["status"], string> = {
  full:          "#16a34a",
  partial:       "#ca8a04",
  error:         "#dc2626",
  rate_limited:  "#ea580c",
  no_provider:   "#7c3aed",
  unknown:       "#3f3f46",  // dim — the "we just don't know" case
};
const COVERAGE_STATUS_LEGEND: Record<CoverageCell["status"], string> = {
  full:          "complete cache for the month",
  partial:       "cache hit but fewer bars than expected",
  error:         "manifest violation or provider failure",
  rate_limited:  "429 from provider; chain may still recover next fetch",
  no_provider:   "no provider configured for (asset_class, resolution)",
  unknown:       "no fetch telemetry for this month yet",
};

function CoverageMatrixPanel() {
  const [data, setData] = useState<CoverageMatrix | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [monthsWindow, setMonthsWindow] = useState<number>(12);
  // No asset_class filter pill yet (single asset_class in production
  // today is us_etf). The endpoint accepts the filter; the picker
  // ships when a second asset_class joins the live universe.

  const load = async (months: number) => {
    setLoading(true);
    try {
      const r = await api.barCacheCoverageMatrix({ months });
      setData(r);
      setError(null);
    } catch (e) {
      setError(String(e));
    } finally {
      setLoading(false);
    }
  };
  useEffect(() => { void load(monthsWindow); }, [monthsWindow]);

  return (
    <Subsection title="Coverage matrix (per symbol × month)">
      <div style={{
        fontSize: 10, color: "var(--text-muted)",
        marginBottom: 8, lineHeight: 1.55,
      }}>
        Last-known fetch status per (canonical, month) derived from{" "}
        <code style={{
          background: "rgba(0,0,0,0.2)", padding: "1px 5px", borderRadius: 3,
          fontSize: 10,
        }}>
          bar_cache_events
        </code>. Hover a cell for the underlying result + provider +
        row count. Coloured cells = real telemetry; grey cells = no
        fetch recorded for that month (use Backfill in the table above
        to populate). G-2 adds click-to-drill into the specific gap.
      </div>

      <div style={{
        display: "flex", gap: 10, alignItems: "center",
        marginBottom: 8, fontSize: 10,
      }}>
        <span style={{ color: "var(--text-muted)" }}>Window:</span>
        {[6, 12, 24].map((m) => (
          <button
            key={m}
            onClick={() => setMonthsWindow(m)}
            style={{
              padding: "2px 8px", fontSize: 10, fontWeight: 600,
              border: `1px solid ${monthsWindow === m ? "var(--accent, #1fc16b)" : "var(--border)"}`,
              borderRadius: 3,
              background: monthsWindow === m ? "rgba(31, 193, 107, 0.12)" : "transparent",
              color: monthsWindow === m ? "var(--accent, #1fc16b)" : "var(--text-dim)",
              cursor: "pointer",
            }}
          >
            {m}m
          </button>
        ))}
        <span style={{ marginLeft: 14, color: "var(--text-muted)" }}>Legend:</span>
        {(["full", "partial", "error", "rate_limited", "no_provider", "unknown"] as const).map((s) => (
          <span key={s} title={COVERAGE_STATUS_LEGEND[s]}>
            <Pill color={COVERAGE_STATUS_COLORS[s]}>{s}</Pill>
          </span>
        ))}
      </div>

      {loading && <Muted>Loading…</Muted>}
      {error && <ErrorText>{error}</ErrorText>}
      {!loading && !error && data && data.rows.length === 0 && (
        <Muted>
          No coverage telemetry yet for the last {monthsWindow} months.
          Run a fetch via <code>tradepro-bar-cache-get</code> or the
          per-symbol Validate / Backfill buttons above.
        </Muted>
      )}
      {!loading && data && data.rows.length > 0 && (
        <CoverageMatrixGrid data={data} />
      )}
    </Subsection>
  );
}

function CoverageMatrixGrid({ data }: { data: CoverageMatrix }) {
  // Sticky-left column for (canonical, asset_class) header so the
  // matrix scrolls horizontally on narrow screens without losing the
  // row identifier. Cells are square-ish (24×20) for density.
  return (
    <div
      style={{
        display: "grid",
        gridTemplateColumns: `160px repeat(${data.months.length}, 24px)`,
        gap: 2, alignItems: "center",
        overflowX: "auto",
        paddingBottom: 4,
      }}
    >
      {/* Header row */}
      <div style={{
        fontSize: 9, color: "var(--text-muted)",
        textTransform: "uppercase", letterSpacing: "0.05em",
        position: "sticky", left: 0, background: "var(--bg)",
        paddingRight: 6,
      }}>
        symbol · asset
      </div>
      {data.months.map((m) => (
        <div key={m} style={{
          fontSize: 8, fontFamily: "monospace",
          color: "var(--text-muted)", textAlign: "center",
          writingMode: "vertical-rl", height: 44,
        }}>
          {m}
        </div>
      ))}
      {/* Data rows */}
      {data.rows.map((row) => (
        <>
          <div
            key={`label-${row.canonical}-${row.asset_class}`}
            style={{
              fontSize: 11, fontFamily: "monospace",
              color: "var(--text)",
              position: "sticky", left: 0, background: "var(--bg)",
              paddingRight: 6, lineHeight: 1.2,
            }}
          >
            <strong>{row.canonical}</strong>{" "}
            <span style={{ color: "var(--text-muted)", fontSize: 9 }}>
              {row.asset_class}
            </span>
          </div>
          {data.months.map((m) => {
            const cell = row.cells[m];
            const status: CoverageCell["status"] = cell?.status ?? "unknown";
            const color = COVERAGE_STATUS_COLORS[status];
            const tooltip = cell
              ? `${m} · ${cell.last_result}` +
                (cell.last_provider ? ` · ${cell.last_provider}` : "") +
                (cell.rows_returned !== null && cell.rows_expected !== null
                  ? ` · ${cell.rows_returned}/${cell.rows_expected} bars`
                  : "") +
                ` · last seen ${new Date(cell.occurred_at_utc).toLocaleDateString()}`
              : `${m} · ${COVERAGE_STATUS_LEGEND.unknown}`;
            return (
              <div
                key={`${row.canonical}-${row.asset_class}-${m}`}
                title={tooltip}
                style={{
                  width: 24, height: 20,
                  background: `${color}55`,
                  border: `1px solid ${color}aa`,
                  borderRadius: 2,
                  cursor: "default",
                }}
              />
            );
          })}
        </>
      ))}
    </div>
  );
}

// ─── Fill-quality panel (Phase F-3) ──────────────────────────────────
//
// Reads /fill-quality which queries oms_fills where the F-2 L1 snapshot
// landed. Two tables: per-symbol aggregate (avg / median / p95 bps) on
// top, recent raw fills underneath. Empty state surfaces honestly
// ("no L1 captured yet") so the panel doesn't pretend to have data
// before F-2 produces it.

type FillQuality = Awaited<ReturnType<typeof api.fillQuality>>;

function FillQualityPanel() {
  const [data, setData] = useState<FillQuality | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [windowDays, setWindowDays] = useState<number>(30);

  const load = async (days: number) => {
    setLoading(true);
    try {
      const r = await api.fillQuality({ sinceDays: days, limit: 50 });
      setData(r);
      setError(null);
    } catch (e) {
      setError(String(e));
    } finally {
      setLoading(false);
    }
  };
  useEffect(() => { void load(windowDays); }, [windowDays]);

  return (
    <Subsection title="Fill quality — realised vs mid (Phase F-3)">
      <div style={{
        fontSize: 10, color: "var(--text-muted)",
        marginBottom: 8, lineHeight: 1.55,
      }}>
        Realised execution price vs the L1 mid captured at fill time
        (Phase F-2). <strong>Positive bps = worse than mid</strong>{" "}
        (BUY filled above, SELL filled below); negative = price
        improvement. Surfaces empty until live IG fills land — the
        bps math is built but the data flow only starts when the IG
        order poller hits its first ACCEPTED deal.
      </div>

      <div style={{
        display: "flex", gap: 10, alignItems: "center",
        marginBottom: 8, fontSize: 10,
      }}>
        <span style={{ color: "var(--text-muted)" }}>Window:</span>
        {[7, 30, 90].map((d) => (
          <button
            key={d}
            onClick={() => setWindowDays(d)}
            style={{
              padding: "2px 8px", fontSize: 10, fontWeight: 600,
              border: `1px solid ${windowDays === d ? "var(--accent, #1fc16b)" : "var(--border)"}`,
              borderRadius: 3,
              background: windowDays === d ? "rgba(31, 193, 107, 0.12)" : "transparent",
              color: windowDays === d ? "var(--accent, #1fc16b)" : "var(--text-dim)",
              cursor: "pointer",
            }}
          >
            {d}d
          </button>
        ))}
      </div>

      {loading && <Muted>Loading…</Muted>}
      {error && <ErrorText>{error}</ErrorText>}
      {!loading && !error && data && data.empty_state && (
        <div style={{
          padding: "10px 14px",
          border: "1px dashed var(--border)",
          borderRadius: 4,
          fontSize: 11,
          color: "var(--text-dim)",
          lineHeight: 1.55,
        }}>
          <Pill color="#6b7280">NO L1 CAPTURED YET</Pill>{" "}
          No fills in the last {data.window_days} days have an L1
          snapshot. F-2 captures bid/ask at IG-routed fills; this
          panel lights up once the poller hits its first ACCEPTED
          deal post-F-2 deploy. Paper / T212 fills don't carry L1
          and are excluded by design.
        </div>
      )}
      {!loading && !error && data && !data.empty_state && (
        <FillQualityTables data={data} />
      )}
    </Subsection>
  );
}

function FillQualityTables({ data }: { data: FillQuality }) {
  return (
    <>
      <h5 style={{
        margin: "10px 0 6px", fontSize: 10, fontWeight: 700,
        color: "var(--text-dim)", letterSpacing: "0.05em",
        textTransform: "uppercase",
      }}>
        Per-symbol aggregates ({data.per_symbol_aggregates.length})
      </h5>
      <div
        style={{
          display: "grid",
          gridTemplateColumns: "90px 80px 70px 80px 80px 80px 80px 80px",
          gap: 8, alignItems: "center",
          paddingBottom: 4, marginBottom: 4,
          borderBottom: "1px solid var(--border)",
          fontSize: 9, color: "var(--text-muted)",
          textTransform: "uppercase", letterSpacing: "0.05em",
        }}
      >
        <span>Broker</span>
        <span>Symbol</span>
        <span style={{ textAlign: "right" }}>N fills</span>
        <span style={{ textAlign: "right" }}>Avg bps</span>
        <span style={{ textAlign: "right" }}>Median</span>
        <span style={{ textAlign: "right" }}>P95</span>
        <span style={{ textAlign: "right" }}>Min</span>
        <span style={{ textAlign: "right" }}>Max</span>
      </div>
      {data.per_symbol_aggregates.map((row) => (
        <div
          key={`${row.broker}-${row.symbol}`}
          style={{
            display: "grid",
            gridTemplateColumns: "90px 80px 70px 80px 80px 80px 80px 80px",
            gap: 8, alignItems: "center",
            padding: "5px 0",
            borderTop: "1px solid var(--border)",
            fontSize: 11, fontFamily: "monospace",
          }}
        >
          <span>{row.broker}</span>
          <span style={{ fontWeight: 600 }}>{row.symbol}</span>
          <span style={{ textAlign: "right" }}>{row.n_fills}</span>
          <span style={{ textAlign: "right", color: bpsColor(row.avg_bps) }}>
            {formatBps(row.avg_bps)}
          </span>
          <span style={{ textAlign: "right", color: bpsColor(row.median_bps) }}>
            {formatBps(row.median_bps)}
          </span>
          <span style={{ textAlign: "right", color: bpsColor(row.p95_bps) }}>
            {formatBps(row.p95_bps)}
          </span>
          <span style={{ textAlign: "right" }}>{formatBps(row.min_bps)}</span>
          <span style={{ textAlign: "right" }}>{formatBps(row.max_bps)}</span>
        </div>
      ))}

      <h5 style={{
        margin: "16px 0 6px", fontSize: 10, fontWeight: 700,
        color: "var(--text-dim)", letterSpacing: "0.05em",
        textTransform: "uppercase",
      }}>
        Recent fills ({data.recent_fills.length})
      </h5>
      <div
        style={{
          display: "grid",
          gridTemplateColumns: "130px 80px 60px 90px 90px 90px 80px",
          gap: 8, alignItems: "center",
          paddingBottom: 4, marginBottom: 4,
          borderBottom: "1px solid var(--border)",
          fontSize: 9, color: "var(--text-muted)",
          textTransform: "uppercase", letterSpacing: "0.05em",
        }}
      >
        <span>Filled at</span>
        <span>Symbol</span>
        <span>Side</span>
        <span style={{ textAlign: "right" }}>Price</span>
        <span style={{ textAlign: "right" }}>Mid</span>
        <span style={{ textAlign: "right" }}>Spread</span>
        <span style={{ textAlign: "right" }}>Bps</span>
      </div>
      {data.recent_fills.map((row) => {
        const spreadBps =
          row.bid_at_fill !== null && row.ask_at_fill !== null
            && row.mid_at_fill !== null && row.mid_at_fill !== 0
            ? ((row.ask_at_fill - row.bid_at_fill) / row.mid_at_fill) * 10000
            : null;
        return (
          <div
            key={row.id}
            style={{
              display: "grid",
              gridTemplateColumns: "130px 80px 60px 90px 90px 90px 80px",
              gap: 8, alignItems: "center",
              padding: "5px 0",
              borderTop: "1px solid var(--border)",
              fontSize: 11, fontFamily: "monospace",
            }}
          >
            <span style={{ color: "var(--text-dim)" }}>
              {new Date(row.fill_at_utc).toLocaleString()}
            </span>
            <span style={{ fontWeight: 600 }}>{row.symbol}</span>
            <span style={{ color: row.side === "BUY" ? "#16a34a" : "#dc2626" }}>
              {row.side}
            </span>
            <span style={{ textAlign: "right" }}>{fmtPrice4(row.price)}</span>
            <span style={{ textAlign: "right" }}>
              {fmtPrice4(row.mid_at_fill)}
            </span>
            <span style={{ textAlign: "right", color: "var(--text-dim)" }}>
              {formatBps(spreadBps)}
            </span>
            <span style={{
              textAlign: "right", fontWeight: 600,
              color: bpsColor(row.realised_bps),
            }}>
              {formatBps(row.realised_bps)}
            </span>
          </div>
        );
      })}
    </>
  );
}

function formatBps(v: number | null): string {
  if (v === null || v === undefined) return "—";
  const sign = v > 0 ? "+" : "";
  return `${sign}${v.toFixed(2)}`;
}

/** Guarded price formatter. The fillQuality endpoint types `price` as a
 * required number, but real rows can arrive with a null/missing price (e.g.
 * pre-2026-06-02 IG fills booked without a price). Calling `.toFixed` on that
 * undefined crashed the whole Settings page, so format defensively. */
function fmtPrice4(v: number | null | undefined): string {
  return Number.isFinite(v as number) ? (v as number).toFixed(4) : "—";
}

function bpsColor(v: number | null): string {
  // Sign convention: positive = worse than mid (cost), negative = improvement.
  // Mid-zero (rare in practice) defaults to neutral.
  if (v === null || v === undefined) return "var(--text-dim)";
  if (v > 5) return "#dc2626";        // > +5bps = expensive
  if (v > 2) return "#ea580c";        // mild cost
  if (v >= -2) return "var(--text)";  // around mid
  return "#16a34a";                   // price improvement
}

// ─── Backfill panel (Phase-A placeholder) ────────────────────────────

function BackfillPanel() {
  return (
    <Subsection title="Data backfill / reload">
      <div
        style={{
          padding: "10px 14px",
          border: "1px dashed var(--border)",
          borderRadius: 4,
          fontSize: 11,
          color: "var(--text-dim)",
          lineHeight: 1.55,
        }}
      >
        <Pill color="#6b7280">PHASE C — not yet implemented</Pill>{" "}
        Operator-driven backfill + reload of bars per (asset_class ×
        symbol × resolution × date range) lands in Phase C of the
        Trustworthy data layer roadmap. Phase A (this PR) ships the
        visibility framework. Phase B builds the cache + provider
        chain consumers. Phase C wires this button to a real backfill
        job queue with per-job status surfaced here.{" "}
        <button
          disabled
          style={{
            padding: "4px 10px", fontSize: 11, fontWeight: 600,
            border: "1px solid var(--text-muted)", borderRadius: 4,
            background: "transparent", color: "var(--text-muted)",
            cursor: "not-allowed",
            marginLeft: 8,
          }}
          title="Phase C — not yet implemented"
        >
          Backfill (disabled)
        </button>
      </div>
    </Subsection>
  );
}

// ─── Shared layout primitives ─────────────────────────────────────────

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

/**
 * IT Data Browser — /admin/data
 *
 * Raw-table read-only viewer for IT investigation. Every Postgres table
 * the platform writes to is surfaced here as a collapsible CockpitCard.
 * No edit / delete — observation only.
 *
 * Tables covered:
 *   events            — domain event log (order_emitted, fill_received …)
 *   orders            — append-only order intents
 *   fills             — execution fills
 *   oms_order_events  — OMS state-machine audit trail
 *   strategy_versions — registered strategy registry
 *
 * OMS orders (oms_orders) and session_requests are already viewable on
 * /oms and /paper-live respectively — links provided rather than
 * duplicating the UI.
 */
import { useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api/client";
import type {
  AdminEventRow,
  AdminFillRow,
  AdminOmsEventRow,
  AdminOrderRow,
  AdminStrategyVersionRow,
} from "../api/client";
import { CockpitCard } from "../components/CockpitCard";
import { TestPlacementPanel } from "../components/cockpit/TestPlacementPanel";

// ── helpers ───────────────────────────────────────────────────────

function ts(iso: string | null | undefined) {
  if (!iso) return "—";
  return new Date(iso).toLocaleString("en-GB", { hour12: false });
}

function Pill({
  label,
  color,
}: {
  label: string;
  color?: string;
}) {
  return (
    <span
      style={{
        display: "inline-block",
        padding: "1px 7px",
        borderRadius: 999,
        fontSize: 11,
        fontWeight: 600,
        background: color ? `${color}22` : "rgba(255,255,255,0.08)",
        color: color ?? "var(--text-muted)",
        letterSpacing: "0.02em",
      }}
    >
      {label}
    </span>
  );
}

const EVENT_COLOR: Record<string, string> = {
  order_emitted: "#4f8cff",
  fill_received: "#1fc16b",
  order_risk_approved: "#1fc16b",
  order_risk_rejected: "#ef4444",
  heartbeat: "#9ca3af",
};

function eventColor(t: string) {
  return EVENT_COLOR[t] ?? "#d97706";
}

function sideColor(s: string) {
  return s === "BUY" ? "#1fc16b" : "#ef4444";
}

function riskColor(r: string | null) {
  if (r === "approve") return "#1fc16b";
  if (r === "reject") return "#ef4444";
  return "#9ca3af";
}

const TH: React.CSSProperties = {
  textAlign: "left",
  padding: "4px 10px",
  fontSize: 11,
  fontWeight: 700,
  color: "var(--text-muted)",
  borderBottom: "1px solid var(--border)",
  whiteSpace: "nowrap",
};

const TD: React.CSSProperties = {
  padding: "4px 10px",
  fontSize: 12,
  color: "var(--text)",
  verticalAlign: "top",
  borderBottom: "1px solid rgba(255,255,255,0.04)",
};

function TableWrap({ children }: { children: React.ReactNode }) {
  return (
    <div style={{ overflowX: "auto" }}>
      <table style={{ width: "100%", borderCollapse: "collapse" }}>
        {children}
      </table>
    </div>
  );
}

// ── filter bar ────────────────────────────────────────────────────

function FilterBar({
  fields,
  values,
  onChange,
  onRefresh,
}: {
  fields: { key: string; placeholder: string }[];
  values: Record<string, string>;
  onChange: (k: string, v: string) => void;
  onRefresh: () => void;
}) {
  return (
    <div style={{ display: "flex", gap: 6, flexWrap: "wrap", marginBottom: 10 }}>
      {fields.map((f) => (
        <input
          key={f.key}
          value={values[f.key] ?? ""}
          placeholder={f.placeholder}
          onChange={(e) => onChange(f.key, e.target.value)}
          style={{
            padding: "4px 8px",
            fontSize: 12,
            background: "var(--bg-hover)",
            border: "1px solid var(--border)",
            borderRadius: 6,
            color: "var(--text)",
            minWidth: 140,
          }}
        />
      ))}
      <button
        onClick={onRefresh}
        style={{ padding: "4px 12px", fontSize: 12, borderRadius: 6 }}
      >
        Refresh
      </button>
    </div>
  );
}

// ── events card ───────────────────────────────────────────────────

function EventsCard() {
  const [rows, setRows] = useState<AdminEventRow[]>([]);
  const [filters, setFilters] = useState<Record<string, string>>({});
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  async function load() {
    setLoading(true);
    setErr(null);
    try {
      const res = await api.adminEvents({
        event_type: filters.event_type || undefined,
        since_seq: filters.since_seq ? Number(filters.since_seq) : undefined,
        limit: filters.limit ? Number(filters.limit) : 100,
      });
      setRows(res.rows);
    } catch (e) {
      setErr(String(e));
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => { load(); }, []); // eslint-disable-line react-hooks/exhaustive-deps

  return (
    <CockpitCard id="admin-events" title="events" badge={loading ? "…" : rows.length} fullWidth>
      <FilterBar
        fields={[
          { key: "event_type", placeholder: "event_type filter" },
          { key: "since_seq", placeholder: "since seq" },
          { key: "limit", placeholder: "limit (default 100)" },
        ]}
        values={filters}
        onChange={(k, v) => setFilters((p) => ({ ...p, [k]: v }))}
        onRefresh={load}
      />
      {err && <p style={{ color: "var(--down)", fontSize: 12 }}>{err}</p>}
      <TableWrap>
        <thead>
          <tr>
            <th style={TH}>seq</th>
            <th style={TH}>event_type</th>
            <th style={TH}>aggregate_id</th>
            <th style={TH}>occurred_at</th>
            <th style={TH}>payload (truncated)</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.seq}>
              <td style={{ ...TD, fontFamily: "monospace" }}>{r.seq}</td>
              <td style={TD}>
                <Pill label={r.event_type} color={eventColor(r.event_type)} />
              </td>
              <td style={{ ...TD, fontFamily: "monospace", fontSize: 11, color: "var(--text-muted)" }}>
                {r.aggregate_id ?? "—"}
              </td>
              <td style={{ ...TD, whiteSpace: "nowrap" }}>{ts(r.occurred_at)}</td>
              <td style={{ ...TD, fontFamily: "monospace", fontSize: 11, maxWidth: 400, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                {r.payload_text.slice(0, 200)}
              </td>
            </tr>
          ))}
          {rows.length === 0 && !loading && (
            <tr>
              <td colSpan={5} style={{ ...TD, color: "var(--text-muted)" }}>No rows</td>
            </tr>
          )}
        </tbody>
      </TableWrap>
    </CockpitCard>
  );
}

// ── orders card ───────────────────────────────────────────────────

function OrdersCard() {
  const [rows, setRows] = useState<AdminOrderRow[]>([]);
  const [filters, setFilters] = useState<Record<string, string>>({});
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  async function load() {
    setLoading(true);
    setErr(null);
    try {
      const res = await api.adminOrders({
        symbol: filters.symbol || undefined,
        strategy: filters.strategy || undefined,
        mode: filters.mode || undefined,
        limit: filters.limit ? Number(filters.limit) : 100,
      });
      setRows(res.rows);
    } catch (e) {
      setErr(String(e));
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => { load(); }, []); // eslint-disable-line react-hooks/exhaustive-deps

  return (
    <CockpitCard id="admin-orders" title="orders (event-sourced)" badge={loading ? "…" : rows.length} fullWidth>
      <FilterBar
        fields={[
          { key: "symbol", placeholder: "symbol" },
          { key: "strategy", placeholder: "strategy" },
          { key: "mode", placeholder: "mode (paper_auto…)" },
          { key: "limit", placeholder: "limit" },
        ]}
        values={filters}
        onChange={(k, v) => setFilters((p) => ({ ...p, [k]: v }))}
        onRefresh={load}
      />
      {err && <p style={{ color: "var(--down)", fontSize: 12 }}>{err}</p>}
      <TableWrap>
        <thead>
          <tr>
            <th style={TH}>order_id</th>
            <th style={TH}>strategy</th>
            <th style={TH}>mode</th>
            <th style={TH}>symbol</th>
            <th style={TH}>side</th>
            <th style={TH}>qty</th>
            <th style={TH}>broker</th>
            <th style={TH}>risk</th>
            <th style={TH}>emitted_at</th>
            <th style={TH}>tag</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.order_id}>
              <td style={{ ...TD, fontFamily: "monospace", fontSize: 10 }}>
                {r.order_id.slice(0, 12)}…
              </td>
              <td style={{ ...TD, fontSize: 11 }}>
                {r.strategy_name}
                <span style={{ color: "var(--text-muted)", marginLeft: 4, fontSize: 10 }}>
                  v{r.strategy_version}
                </span>
              </td>
              <td style={TD}><Pill label={r.mode} /></td>
              <td style={{ ...TD, fontWeight: 600 }}>{r.symbol}</td>
              <td style={TD}><Pill label={r.side} color={sideColor(r.side)} /></td>
              <td style={{ ...TD, fontFamily: "monospace" }}>{r.quantity}</td>
              <td style={TD}>{r.broker}</td>
              <td style={TD}>
                <Pill
                  label={r.risk_decision ?? "pending"}
                  color={riskColor(r.risk_decision)}
                />
              </td>
              <td style={{ ...TD, whiteSpace: "nowrap" }}>{ts(r.emitted_at_utc)}</td>
              <td style={{ ...TD, color: "var(--text-muted)", fontSize: 11 }}>{r.tag ?? "—"}</td>
            </tr>
          ))}
          {rows.length === 0 && !loading && (
            <tr><td colSpan={10} style={{ ...TD, color: "var(--text-muted)" }}>No rows</td></tr>
          )}
        </tbody>
      </TableWrap>
    </CockpitCard>
  );
}

// ── fills card ────────────────────────────────────────────────────

function FillsCard() {
  const [rows, setRows] = useState<AdminFillRow[]>([]);
  const [filters, setFilters] = useState<Record<string, string>>({});
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  async function load() {
    setLoading(true);
    setErr(null);
    try {
      const res = await api.adminFills({
        order_id: filters.order_id || undefined,
        limit: filters.limit ? Number(filters.limit) : 100,
      });
      setRows(res.rows);
    } catch (e) {
      setErr(String(e));
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => { load(); }, []); // eslint-disable-line react-hooks/exhaustive-deps

  return (
    <CockpitCard id="admin-fills" title="fills" badge={loading ? "…" : rows.length} fullWidth>
      <FilterBar
        fields={[
          { key: "order_id", placeholder: "order_id filter" },
          { key: "limit", placeholder: "limit" },
        ]}
        values={filters}
        onChange={(k, v) => setFilters((p) => ({ ...p, [k]: v }))}
        onRefresh={load}
      />
      {err && <p style={{ color: "var(--down)", fontSize: 12 }}>{err}</p>}
      <TableWrap>
        <thead>
          <tr>
            <th style={TH}>fill_id</th>
            <th style={TH}>order_id</th>
            <th style={TH}>fill_qty</th>
            <th style={TH}>fill_price</th>
            <th style={TH}>commission</th>
            <th style={TH}>broker_order_id</th>
            <th style={TH}>filled_at</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.fill_id}>
              <td style={{ ...TD, fontFamily: "monospace" }}>{r.fill_id}</td>
              <td style={{ ...TD, fontFamily: "monospace", fontSize: 10 }}>
                {r.order_id.slice(0, 12)}…
              </td>
              <td style={{ ...TD, fontFamily: "monospace" }}>{r.fill_qty}</td>
              <td style={{ ...TD, fontFamily: "monospace", color: "#1fc16b" }}>
                {r.fill_price.toFixed(4)}
              </td>
              <td style={{ ...TD, fontFamily: "monospace" }}>{r.commission}</td>
              <td style={{ ...TD, fontFamily: "monospace", fontSize: 11, color: "var(--text-muted)" }}>
                {r.broker_order_id ?? "—"}
              </td>
              <td style={{ ...TD, whiteSpace: "nowrap" }}>{ts(r.filled_at_utc)}</td>
            </tr>
          ))}
          {rows.length === 0 && !loading && (
            <tr><td colSpan={7} style={{ ...TD, color: "var(--text-muted)" }}>No rows</td></tr>
          )}
        </tbody>
      </TableWrap>
    </CockpitCard>
  );
}

// ── OMS events card ───────────────────────────────────────────────

function OmsEventsCard() {
  const [rows, setRows] = useState<AdminOmsEventRow[]>([]);
  const [filters, setFilters] = useState<Record<string, string>>({});
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  async function load() {
    setLoading(true);
    setErr(null);
    try {
      const res = await api.adminOmsEvents({
        order_id: filters.order_id || undefined,
        limit: filters.limit ? Number(filters.limit) : 100,
      });
      setRows(res.rows);
    } catch (e) {
      setErr(String(e));
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => { load(); }, []); // eslint-disable-line react-hooks/exhaustive-deps

  const STATE_COLOR: Record<string, string> = {
    PENDING_APPROVAL: "#f59e0b",
    SUBMITTED: "#4f8cff",
    WORKING: "#4f8cff",
    FILLED: "#1fc16b",
    PARTIALLY_FILLED: "#06A77D",
    CANCELLED: "#9ca3af",
    REJECTED: "#ef4444",
  };

  return (
    <CockpitCard id="admin-oms-events" title="oms_order_events (audit trail)" badge={loading ? "…" : rows.length} fullWidth>
      <FilterBar
        fields={[
          { key: "order_id", placeholder: "order_id (UUID)" },
          { key: "limit", placeholder: "limit" },
        ]}
        values={filters}
        onChange={(k, v) => setFilters((p) => ({ ...p, [k]: v }))}
        onRefresh={load}
      />
      {err && <p style={{ color: "var(--down)", fontSize: 12 }}>{err}</p>}
      <TableWrap>
        <thead>
          <tr>
            <th style={TH}>id</th>
            <th style={TH}>order_id</th>
            <th style={TH}>event_type</th>
            <th style={TH}>prior_state</th>
            <th style={TH}>new_state</th>
            <th style={TH}>actor</th>
            <th style={TH}>occurred_at</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.id}>
              <td style={{ ...TD, fontFamily: "monospace" }}>{r.id}</td>
              <td style={{ ...TD, fontFamily: "monospace", fontSize: 10 }}>
                {String(r.order_id).slice(0, 8)}…
              </td>
              <td style={TD}><Pill label={r.event_type} color="#4f8cff" /></td>
              <td style={TD}>
                {r.prior_state
                  ? <Pill label={r.prior_state} color={STATE_COLOR[r.prior_state]} />
                  : <span style={{ color: "var(--text-muted)" }}>—</span>}
              </td>
              <td style={TD}>
                <Pill label={r.new_state} color={STATE_COLOR[r.new_state] ?? "#9ca3af"} />
              </td>
              <td style={{ ...TD, color: "var(--text-muted)", fontSize: 11 }}>{r.actor}</td>
              <td style={{ ...TD, whiteSpace: "nowrap" }}>{ts(r.occurred_at_utc)}</td>
            </tr>
          ))}
          {rows.length === 0 && !loading && (
            <tr><td colSpan={7} style={{ ...TD, color: "var(--text-muted)" }}>No rows</td></tr>
          )}
        </tbody>
      </TableWrap>
    </CockpitCard>
  );
}

// ── strategy versions card ────────────────────────────────────────

function StrategyVersionsCard() {
  const [rows, setRows] = useState<AdminStrategyVersionRow[]>([]);
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const loaded = useRef(false);

  useEffect(() => {
    if (loaded.current) return;
    loaded.current = true;
    setLoading(true);
    api.adminStrategyVersions()
      .then((r) => setRows(r.rows))
      .catch((e) => setErr(String(e)))
      .finally(() => setLoading(false));
  }, []);

  return (
    <CockpitCard id="admin-strategy-versions" title="strategy_versions" badge={loading ? "…" : rows.length} defaultOpen={false} fullWidth>
      {err && <p style={{ color: "var(--down)", fontSize: 12 }}>{err}</p>}
      <TableWrap>
        <thead>
          <tr>
            <th style={TH}>name</th>
            <th style={TH}>version</th>
            <th style={TH}>layer</th>
            <th style={TH}>code_hash</th>
            <th style={TH}>registered_at</th>
            <th style={TH}>deprecated</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={`${r.name}-${r.version}`}>
              <td style={{ ...TD, fontWeight: 600 }}>{r.name}</td>
              <td style={{ ...TD, fontFamily: "monospace" }}>{r.version}</td>
              <td style={TD}><Pill label={r.layer} color={r.layer === "paper" ? "#4f8cff" : "#d97706"} /></td>
              <td style={{ ...TD, fontFamily: "monospace", fontSize: 10, color: "var(--text-muted)" }}>
                {r.code_hash.slice(0, 10)}…
              </td>
              <td style={{ ...TD, whiteSpace: "nowrap" }}>{ts(r.registered_at)}</td>
              <td style={TD}>
                {r.deprecated_at
                  ? <Pill label="deprecated" color="#ef4444" />
                  : <Pill label="active" color="#1fc16b" />}
              </td>
            </tr>
          ))}
          {rows.length === 0 && !loading && (
            <tr><td colSpan={6} style={{ ...TD, color: "var(--text-muted)" }}>No registered strategy versions yet</td></tr>
          )}
        </tbody>
      </TableWrap>
    </CockpitCard>
  );
}

// ── page ──────────────────────────────────────────────────────────

export function AdminDataBrowser() {
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
      <div style={{ marginBottom: 8 }}>
        <h2 style={{ margin: 0, fontSize: 18, fontWeight: 700 }}>IT Data Browser</h2>
        <p style={{ margin: "4px 0 0", fontSize: 12, color: "var(--text-muted)" }}>
          Read-only view of raw Postgres tables. Rows newest-first, limit 100 by default.
          Also see:&nbsp;
          <Link to="/oms" style={{ color: "var(--accent)" }}>OMS orders</Link>
          &nbsp;·&nbsp;
          <Link to="/paper-live" style={{ color: "var(--accent)" }}>Session queue</Link>
        </p>
      </div>

      <div style={{ display: "grid", gridTemplateColumns: "1fr", gap: 12 }}>
        <CockpitCard
          id="admin-test-placement"
          title="Test placement (manual OMS → T212 demo) — IT smoke test"
          fullWidth
          defaultOpen={false}
        >
          <TestPlacementPanel onPlaced={() => { /* admin context — no parent refetch needed */ }} />
        </CockpitCard>
        <EventsCard />
        <OrdersCard />
        <FillsCard />
        <OmsEventsCard />
        <StrategyVersionsCard />
      </div>
    </div>
  );
}

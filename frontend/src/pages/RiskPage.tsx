/**
 * /risk — Risk module audit page.
 *
 * Three sections, all today-only by default (per the no-clutter
 * principle); explicit since= toggle for historical lookup later.
 *
 *   1. System state controls — current mode + Freeze / Panic /
 *      Resume buttons. The operator's kill switch.
 *
 *   2. Today's risk events — what got blocked + why. Grouped by
 *      decision (BLOCKED vs ALLOWED). Sortable by time.
 *
 *   3. Blacklist — operator-curated symbol blocklist + add/remove.
 *
 * Background loops on the relevant endpoints; refreshes after every
 * operator action so the UI mirrors what the backend believes.
 */
import { useCallback, useEffect, useState } from "react";
import { CockpitCard } from "../components/CockpitCard";
import { config } from "../config";

interface RiskEvent {
  id: number;
  orderId: string | null;
  strategyId: string;
  symbol: string;
  side: string;
  qty: number;
  broker: string;
  decision: string;
  gate: string;
  reason: string;
  occurredAtUtc: string;
}

interface RiskSummary {
  since: string;
  byDecision: Record<string, number>;
  blockedByGate: Array<{ gate: string; count: number }>;
}

interface SystemState {
  mode: string;
  reason: string | null;
  setAtUtc: string | null;
  setBy: string | null;
}

interface BlacklistEntry {
  ticker: string;
  reason: string | null;
  addedAtUtc: string;
  addedBy: string;
}

export function RiskPage() {
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 14, maxWidth: 1200 }}>
      <div>
        <h1 style={{ margin: 0, fontSize: 22, fontWeight: 700 }}>Risk</h1>
        <p style={{ margin: "4px 0 0", fontSize: 12, color: "var(--text-muted)" }}>
          System state · today's pre-trade events · symbol blacklist
        </p>
      </div>
      <SystemStateCard />
      <RiskSummaryCard />
      <RiskEventsCard />
      <BlacklistCard />
    </div>
  );
}

// ─── System state card ─────────────────────────────────────────────────────

function SystemStateCard() {
  const [state, setState] = useState<SystemState | null>(null);
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const r = await fetch(`${config.apiBaseUrl}/api/system/state`);
      if (r.ok) setState(await r.json() as SystemState);
    } catch { /* silent */ }
  }, []);
  useEffect(() => { void load(); }, [load]);

  const callMode = async (mode: "freeze" | "panic" | "resume") => {
    setBusy(true); setErr(null);
    try {
      const body = mode === "resume" && !reason.trim()
        ? { reason: "manual resume" }
        : { reason: reason.trim() };
      const r = await fetch(`${config.apiBaseUrl}/api/system/${mode}`, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify(body),
      });
      if (!r.ok) {
        const j = await r.json().catch(() => ({}));
        setErr(j.error ?? `HTTP ${r.status}`);
      } else {
        setReason("");
        await load();
      }
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  const mode = state?.mode ?? "normal";
  const modeColor = mode === "panic" ? "#ef4444" : mode === "frozen" ? "#f59e0b" : "#1fc16b";
  return (
    <CockpitCard id="risk-system" title="System state" fullWidth>
      <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
        <div style={{ display: "flex", alignItems: "center", gap: 16, flexWrap: "wrap" }}>
          <div>
            <div style={{ fontSize: 10, color: "var(--text-muted)", textTransform: "uppercase", letterSpacing: "0.06em" }}>Current</div>
            <div style={{
              fontSize: 20, fontWeight: 800, color: modeColor,
              textTransform: "uppercase", letterSpacing: "0.08em",
            }}>{mode}</div>
          </div>
          {state?.reason && (
            <div style={{ fontSize: 12, color: "var(--text-dim)" }}>
              <div style={{ fontSize: 10, color: "var(--text-muted)", textTransform: "uppercase", letterSpacing: "0.06em" }}>Reason</div>
              {state.reason}
            </div>
          )}
          {state?.setBy && (
            <div style={{ fontSize: 12, color: "var(--text-dim)" }}>
              <div style={{ fontSize: 10, color: "var(--text-muted)", textTransform: "uppercase", letterSpacing: "0.06em" }}>Set by</div>
              {state.setBy}
              {state.setAtUtc ? <> · {new Date(state.setAtUtc).toLocaleString()}</> : null}
            </div>
          )}
        </div>
        <div style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "center" }}>
          <input
            type="text"
            placeholder="Reason (required for freeze / panic)"
            value={reason}
            onChange={(e) => setReason(e.target.value)}
            style={{ flex: 1, minWidth: 200, padding: "8px 10px", fontSize: 13,
              background: "var(--surface-1, #0b1220)",
              border: "1px solid var(--border)", borderRadius: 6, color: "var(--text)" }}
          />
          <button onClick={() => void callMode("freeze")} disabled={busy || mode === "frozen"}
            style={btnStyle("#f59e0b", busy)}>Freeze</button>
          <button onClick={() => void callMode("panic")} disabled={busy || mode === "panic"}
            style={btnStyle("#ef4444", busy)}>Panic</button>
          <button onClick={() => void callMode("resume")} disabled={busy || mode === "normal"}
            style={btnStyle("#1fc16b", busy)}>Resume</button>
        </div>
        {err && (
          <div style={{ fontSize: 12, color: "#ef4444" }}>Action failed: {err}</div>
        )}
      </div>
    </CockpitCard>
  );
}

function btnStyle(color: string, busy: boolean): React.CSSProperties {
  return {
    padding: "8px 14px", fontSize: 12, fontWeight: 700,
    borderRadius: 6, border: `1px solid ${color}`,
    background: `${color}22`, color, cursor: busy ? "not-allowed" : "pointer",
    opacity: busy ? 0.5 : 1, letterSpacing: "0.04em", textTransform: "uppercase",
  };
}

// ─── Risk summary card ─────────────────────────────────────────────────────

function RiskSummaryCard() {
  const [data, setData] = useState<RiskSummary | null>(null);
  useEffect(() => {
    let cancelled = false;
    const load = async () => {
      try {
        const r = await fetch(`${config.apiBaseUrl}/api/risk/summary`);
        if (r.ok && !cancelled) setData(await r.json() as RiskSummary);
      } catch { /* silent */ }
    };
    void load();
    const t = setInterval(load, 30_000);
    return () => { cancelled = true; clearInterval(t); };
  }, []);
  if (!data) return null;
  const allowed = data.byDecision.ALLOWED ?? 0;
  const blocked = data.byDecision.BLOCKED ?? 0;
  return (
    <CockpitCard id="risk-summary" title="Today's risk-gate decisions">
      <div style={{ display: "flex", gap: 18, flexWrap: "wrap" }}>
        <Stat label="Allowed" value={allowed} color="#1fc16b" />
        <Stat label="Blocked" value={blocked} color="#ef4444" />
        {data.blockedByGate.length > 0 && (
          <div style={{ display: "flex", flexDirection: "column", gap: 2 }}>
            <span style={{ fontSize: 10, color: "var(--text-muted)", textTransform: "uppercase", letterSpacing: "0.06em" }}>
              Blocks by gate
            </span>
            <span style={{ fontSize: 13 }}>
              {data.blockedByGate.map((g) => `${g.gate}: ${g.count}`).join(" · ")}
            </span>
          </div>
        )}
      </div>
    </CockpitCard>
  );
}

function Stat({ label, value, color }: { label: string; value: number; color: string }) {
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 2 }}>
      <span style={{ fontSize: 10, color: "var(--text-muted)", textTransform: "uppercase", letterSpacing: "0.06em" }}>{label}</span>
      <span style={{ fontSize: 22, fontWeight: 700, color }}>{value}</span>
    </div>
  );
}

// ─── Risk events table ─────────────────────────────────────────────────────

function RiskEventsCard() {
  const [events, setEvents] = useState<RiskEvent[]>([]);
  useEffect(() => {
    let cancelled = false;
    const load = async () => {
      try {
        const r = await fetch(`${config.apiBaseUrl}/api/risk/events?limit=100`);
        if (r.ok && !cancelled) {
          const d = await r.json() as { events: RiskEvent[] };
          setEvents(d.events ?? []);
        }
      } catch { /* silent */ }
    };
    void load();
    const t = setInterval(load, 30_000);
    return () => { cancelled = true; clearInterval(t); };
  }, []);
  return (
    <CockpitCard id="risk-events" title={`Today's risk events (${events.length})`} fullWidth>
      {events.length === 0 ? (
        <div style={{ padding: 14, fontSize: 13, color: "var(--text-dim)" }}>
          No events today.
        </div>
      ) : (
        <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 12 }}>
          <thead>
            <tr style={{ color: "var(--text-muted)" }}>
              <Th>Time</Th><Th>Strategy</Th><Th>Symbol</Th><Th>Side</Th>
              <Th right>Qty</Th><Th>Decision</Th><Th>Gate</Th><Th>Reason</Th>
            </tr>
          </thead>
          <tbody>
            {events.map((e) => (
              <tr key={e.id} style={{ borderTop: "1px solid var(--border)" }}>
                <Td small mono>{new Date(e.occurredAtUtc).toLocaleTimeString()}</Td>
                <Td small>{e.strategyId}</Td>
                <Td><strong>{e.symbol}</strong></Td>
                <Td small style={{ color: e.side === "BUY" ? "#1fc16b" : "#ef4444" }}>{e.side}</Td>
                <Td right mono>{e.qty}</Td>
                <Td><DecisionPill decision={e.decision} /></Td>
                <Td small mono>{e.gate}</Td>
                <Td small>{e.reason}</Td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </CockpitCard>
  );
}

function DecisionPill({ decision }: { decision: string }) {
  const color = decision === "ALLOWED" ? "#1fc16b"
    : decision === "BLOCKED" ? "#ef4444"
    : decision === "SIZE_ADJUSTED" ? "#f59e0b"
    : decision === "KILL_SWITCH" ? "#7f1d1d" : "var(--text-muted)";
  return (
    <span style={{
      display: "inline-block", padding: "1px 6px", borderRadius: 999,
      background: `${color}22`, color, fontSize: 10, fontWeight: 700,
      letterSpacing: "0.04em", textTransform: "uppercase",
    }}>{decision}</span>
  );
}

// ─── Blacklist ─────────────────────────────────────────────────────────────

function BlacklistCard() {
  const [list, setList] = useState<BlacklistEntry[]>([]);
  const [ticker, setTicker] = useState("");
  const [reason, setReason] = useState("");
  const [err, setErr] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const r = await fetch(`${config.apiBaseUrl}/api/risk/blacklist`);
      if (r.ok) {
        const d = await r.json() as { blacklist: BlacklistEntry[] };
        setList(d.blacklist ?? []);
      }
    } catch { /* silent */ }
  }, []);
  useEffect(() => { void load(); }, [load]);

  const add = async () => {
    if (!ticker.trim()) return;
    setErr(null);
    try {
      const r = await fetch(`${config.apiBaseUrl}/api/risk/blacklist`, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ ticker: ticker.trim(), reason: reason.trim() || null }),
      });
      if (!r.ok) {
        const j = await r.json().catch(() => ({}));
        setErr(j.error ?? `HTTP ${r.status}`);
      } else {
        setTicker(""); setReason(""); await load();
      }
    } catch (e) { setErr(e instanceof Error ? e.message : String(e)); }
  };

  const remove = async (t: string) => {
    try {
      const r = await fetch(
        `${config.apiBaseUrl}/api/risk/blacklist/${encodeURIComponent(t)}`,
        { method: "DELETE" },
      );
      if (r.ok) await load();
    } catch { /* silent */ }
  };

  return (
    <CockpitCard id="risk-blacklist" title={`Symbol blacklist (${list.length})`} fullWidth>
      <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
        <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
          <input type="text" placeholder="Ticker (e.g. AAPL)" value={ticker}
            onChange={(e) => setTicker(e.target.value)}
            style={{ width: 120, padding: "8px 10px", fontSize: 13,
              background: "var(--surface-1, #0b1220)",
              border: "1px solid var(--border)", borderRadius: 6, color: "var(--text)" }} />
          <input type="text" placeholder="Reason (optional)" value={reason}
            onChange={(e) => setReason(e.target.value)}
            style={{ flex: 1, minWidth: 200, padding: "8px 10px", fontSize: 13,
              background: "var(--surface-1, #0b1220)",
              border: "1px solid var(--border)", borderRadius: 6, color: "var(--text)" }} />
          <button onClick={() => void add()} disabled={!ticker.trim()}
            style={btnStyle("#4f8cff", false)}>Add</button>
        </div>
        {err && <div style={{ fontSize: 12, color: "#ef4444" }}>{err}</div>}
        {list.length === 0 ? (
          <div style={{ fontSize: 12, color: "var(--text-dim)", padding: "6px 0" }}>
            Empty — no symbols are blacklisted.
          </div>
        ) : (
          <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 12 }}>
            <thead>
              <tr style={{ color: "var(--text-muted)" }}>
                <Th>Ticker</Th><Th>Reason</Th><Th>Added</Th><Th>By</Th><Th />
              </tr>
            </thead>
            <tbody>
              {list.map((b) => (
                <tr key={b.ticker} style={{ borderTop: "1px solid var(--border)" }}>
                  <Td><strong>{b.ticker}</strong></Td>
                  <Td small>{b.reason ?? "—"}</Td>
                  <Td small mono>{new Date(b.addedAtUtc).toLocaleDateString()}</Td>
                  <Td small>{b.addedBy}</Td>
                  <Td>
                    <button onClick={() => void remove(b.ticker)}
                      style={{ padding: "2px 8px", fontSize: 10, borderRadius: 4,
                        border: "1px solid var(--border)", background: "transparent",
                        color: "var(--text-dim)", cursor: "pointer" }}>
                      remove
                    </button>
                  </Td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </CockpitCard>
  );
}

// ─── Shared cells ──────────────────────────────────────────────────────────

function Th({ children, right }: { children?: React.ReactNode; right?: boolean }) {
  return (
    <th style={{
      padding: "4px 8px", textAlign: right ? "right" : "left",
      fontWeight: 600, fontSize: 10,
      textTransform: "uppercase", letterSpacing: "0.06em",
    }}>{children}</th>
  );
}

function Td({
  children, right, mono, small, style,
}: {
  children?: React.ReactNode;
  right?: boolean;
  mono?: boolean;
  small?: boolean;
  style?: React.CSSProperties;
}) {
  return (
    <td style={{
      padding: "4px 8px",
      textAlign: right ? "right" : "left",
      fontFamily: mono ? "ui-monospace, Menlo, monospace" : undefined,
      fontSize: small ? 11 : 12,
      color: small ? "var(--text-dim)" : "var(--text)",
      ...style,
    }}>{children}</td>
  );
}

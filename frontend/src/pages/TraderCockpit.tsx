import { useCallback, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { CockpitCard } from "../components/CockpitCard";
import { api, OmsOrderRow } from "../api/client";
import { config } from "../config";

/**
 * Trader cockpit — every piece of context the trader needs in one
 * screen as show/hide widgets. Per the trader's UX rule (single-
 * screen, no nav). All business logic is server-side; this file is
 * pure composition: fetch from existing endpoints, render in cards.
 *
 * Widgets (v1):
 *   ⚠ Warnings        — system errors (T212 down, daemon stale, drift)
 *   💵 Cash           — free balance, invested, total
 *   ⏳ Intents        — orders generated, awaiting approval (inline approve)
 *   ⛵ Submitted      — orders placed, awaiting fill
 *   ✓ Recent fills    — trades executed in the last 24h
 *   📊 Positions      — open positions + P&L + drift indicator
 *
 * Each widget is a `CockpitCard` whose open/closed state persists
 * per-card in localStorage so the operator's layout survives reloads.
 */

type T212Cash = Awaited<ReturnType<typeof api.t212Cash>>;

type DecisionEntry = {
  barTs: string | null;
  symbol: string;
  action: string;
  reason: string;
  detail: Record<string, unknown>;
};
type LatestSession = {
  strategy: string;
  requestId: string;
  completedAtUtc: string | null;
  decisions: DecisionEntry[];
};
type T212PosResp = {
  enabled: boolean;
  mode: string;
  positionCount: number;
  positions: Array<{
    ticker: string;
    yahooSymbol: string | null;
    quantity: number;
    averagePricePaid: number | null;
    currentPrice: number | null;
    unrealisedPct: number | null;
    unrealisedAbs: number | null;
    currency: string | null;
  }>;
  error?: string | null;
  fromCache?: boolean;
  ageSeconds?: number;
};

const ACCOUNT_KEY = "cockpit.account";

export function TraderCockpit() {
  const [account, setAccount] = useState<"demo" | "live">(() => {
    if (typeof window === "undefined") return "demo";
    return (localStorage.getItem(ACCOUNT_KEY) as "demo" | "live") || "demo";
  });
  useEffect(() => {
    try { localStorage.setItem(ACCOUNT_KEY, account); } catch { /* noop */ }
  }, [account]);

  const [orders, setOrders] = useState<OmsOrderRow[]>([]);
  const [ordersErr, setOrdersErr] = useState<string | null>(null);
  const [cash, setCash] = useState<T212Cash | null>(null);
  const [cashErr, setCashErr] = useState<string | null>(null);
  const [positions, setPositions] = useState<T212PosResp | null>(null);
  const [posErr, setPosErr] = useState<string | null>(null);
  const [acting, setActing] = useState<string | null>(null);
  // Latest completed session per strategy so the trader can validate
  // signal output (fire-buy / fire-sell decisions) at-a-glance even
  // when nothing actually executes at the broker — useful for FX
  // strategies on CFD where T212 has no public API for placement.
  const [latestSessions, setLatestSessions] = useState<LatestSession[]>([]);

  // Loaders — each fires independently so one slow source doesn't
  // block the others rendering.
  const loadOrders = useCallback(async () => {
    try {
      const { orders } = await api.omsOrders(undefined, 100);
      setOrders(orders);
      setOrdersErr(null);
    } catch (e) {
      setOrdersErr(String(e));
    }
  }, []);
  const loadCash = useCallback(async () => {
    try {
      const c = await api.t212Cash(account);
      setCash(c);
      setCashErr(null);
    } catch (e) {
      setCashErr(String(e));
    }
  }, [account]);
  const loadPositions = useCallback(async () => {
    try {
      const resp = await fetch(
        `${config.apiBaseUrl}/api/integrations/trading212/positions?account=${account}`,
      );
      if (!resp.ok) throw new Error(`${resp.status} ${resp.statusText}`);
      const d = await resp.json();
      setPositions(d as T212PosResp);
      setPosErr(null);
    } catch (e) {
      setPosErr(String(e));
    }
  }, [account]);

  useEffect(() => { void loadOrders(); }, [loadOrders]);
  useEffect(() => { void loadCash(); void loadPositions(); }, [loadCash, loadPositions]);

  // Latest sessions — pull the most recent Completed per strategy
  // and extract the decision trace. Server-side data; we just project
  // to a flat row list for rendering. Polls every 30s.
  const loadSessions = useCallback(async () => {
    try {
      const { sessions } = await api.opsSessions(undefined, 20);
      const completed = sessions.filter((s) => s.status === "Completed");
      // Group by strategy and pick the newest per strategy.
      const byStrategy = new Map<string, LatestSession>();
      for (const s of completed) {
        const rs = (s.resultSummary ?? {}) as Record<string, unknown>;
        const strategy = (rs.strategy as string) || (s.payload as Record<string, unknown>)?.strategy as string;
        if (!strategy) continue;
        if (byStrategy.has(strategy)) continue;
        const strategies = (rs.strategies as Array<Record<string, unknown>>) || [];
        const decisions: DecisionEntry[] = [];
        for (const st of strategies) {
          const ds = st.decisions as Array<Record<string, unknown>>;
          if (!Array.isArray(ds)) continue;
          for (const d of ds) {
            decisions.push({
              barTs: (d.bar_ts as string) || null,
              symbol: (d.symbol as string) || "",
              action: (d.action as string) || "",
              reason: (d.reason as string) || "",
              detail: (d.detail as Record<string, unknown>) || {},
            });
          }
        }
        // Sort newest-first; cap to 30 per strategy to keep render light.
        decisions.sort((a, b) => (b.barTs ?? "").localeCompare(a.barTs ?? ""));
        byStrategy.set(strategy, {
          strategy,
          requestId: s.requestId,
          completedAtUtc: s.completedAtUtc,
          decisions: decisions.slice(0, 30),
        });
      }
      setLatestSessions(Array.from(byStrategy.values()));
    } catch {
      // Non-fatal; signals panel just won't update.
    }
  }, []);
  useEffect(() => { void loadSessions(); }, [loadSessions]);
  useEffect(() => {
    const t = setInterval(() => void loadSessions(), 30_000);
    return () => clearInterval(t);
  }, [loadSessions]);

  // Auto-refresh orders every 15s when ANY are open (not terminal).
  useEffect(() => {
    const hasOpen = orders.some((o) =>
      ["PENDING_APPROVAL", "SUBMITTED", "WORKING", "PARTIALLY_FILLED"].includes(o.state),
    );
    if (!hasOpen) return;
    const t = setInterval(() => void loadOrders(), 15_000);
    return () => clearInterval(t);
  }, [orders, loadOrders]);

  // Bucket orders by state for the three order widgets.
  const pending = orders.filter((o) => o.state === "PENDING_APPROVAL");
  const submitted = orders.filter((o) =>
    ["SUBMITTED", "WORKING", "PARTIALLY_FILLED"].includes(o.state),
  );
  const recent = orders
    .filter((o) => o.state === "FILLED" || o.state === "PARTIALLY_FILLED")
    .slice(0, 25);

  // Warnings — server-side data with frontend pattern-matching for
  // surfacing only. No new computation: just summarise what's wrong.
  const warnings: { tone: "warn" | "down"; text: string }[] = [];
  if (cashErr) warnings.push({ tone: "down", text: `Cash fetch failed: ${cashErr}` });
  else if (cash && cash.error) warnings.push({ tone: "down", text: `T212 cash: ${cash.error}` });
  else if (cash && !cash.enabled) warnings.push({ tone: "warn", text: cash.message ?? "T212 cash unavailable" });
  if (posErr) warnings.push({ tone: "down", text: `Positions fetch failed: ${posErr}` });
  else if (positions && positions.error) warnings.push({ tone: "down", text: `T212 positions: ${positions.error}` });
  if (ordersErr) warnings.push({ tone: "down", text: `OMS fetch failed: ${ordersErr}` });
  const rejectedRecent = orders.filter((o) =>
    o.state === "REJECTED" || o.state === "EXPIRED"
  ).length;
  if (rejectedRecent > 0) {
    warnings.push({
      tone: "warn",
      text: `${rejectedRecent} order${rejectedRecent === 1 ? " was" : "s were"} rejected — check /oms`,
    });
  }

  const act = async (orderId: string, kind: "approve" | "reject" | "cancel") => {
    setActing(orderId + ":" + kind);
    try {
      if (kind === "approve") await api.omsApprove(orderId);
      else if (kind === "reject") {
        const r = prompt("Reject reason?", "") || "rejected";
        await api.omsReject(orderId, r);
      } else {
        const r = prompt("Cancel reason?", "user_cancel") || "user_cancel";
        await api.omsCancel(orderId, r);
      }
      await loadOrders();
    } catch (e) {
      setOrdersErr(String(e));
    } finally {
      setActing(null);
    }
  };

  const approveAll = async () => {
    if (!pending.length) return;
    if (!confirm(`Approve all ${pending.length} pending intent${pending.length === 1 ? "" : "s"}?`)) return;
    for (const o of pending) {
      try { await api.omsApprove(o.id); } catch { /* keep going through the queue */ }
    }
    await loadOrders();
  };

  return (
    <div style={{ padding: 20, maxWidth: 1280, margin: "0 auto" }}>
      {/* ── Account selector strip ──────────────────────────────── */}
      <div style={{ display: "flex", alignItems: "center", gap: 12, marginBottom: 14 }}>
        <h1 style={{ margin: 0, fontSize: 22 }}>Trader cockpit</h1>
        <div style={{ display: "flex", gap: 4 }}>
          {(["demo", "live"] as const).map((a) => (
            <button
              key={a}
              onClick={() => setAccount(a)}
              style={{
                padding: "3px 10px", fontSize: 11, borderRadius: 999,
                border: `1px solid ${
                  account === a
                    ? a === "live" ? "#ef4444" : "#4f8cff"
                    : "var(--border)"
                }`,
                background: account === a
                  ? a === "live" ? "rgba(239,68,68,0.10)" : "rgba(79,140,255,0.10)"
                  : "transparent",
                color: account === a
                  ? a === "live" ? "#ef4444" : "#4f8cff"
                  : "var(--text-dim)",
                cursor: "pointer", letterSpacing: "0.04em",
                textTransform: "uppercase", fontWeight: 600,
              }}
              title={a === "live" ? "Trading 212 LIVE (real money)" : "Trading 212 demo (paper)"}
            >
              {a}
            </button>
          ))}
        </div>
      </div>

      {/* ── Warnings (only visible when any) ────────────────────── */}
      {warnings.length > 0 && (
        <CockpitCard
          id="warnings"
          title="Warnings"
          badge={warnings.length}
          tone="warn"
          defaultOpen
        >
          {warnings.map((w, i) => (
            <div
              key={i}
              style={{
                fontSize: 12, padding: "4px 0",
                color: w.tone === "down" ? "var(--down)" : "#d97706",
              }}
            >
              {w.text}
            </div>
          ))}
        </CockpitCard>
      )}

      {/* ── Trigger (compact run form) ──────────────────────────── */}
      <CockpitCard id="trigger" title="Trigger session" defaultOpen={false}>
        <TriggerPanel onTriggered={() => { void loadOrders(); void loadSessions(); }} />
      </CockpitCard>

      {/* ── Manual test placement (skip the strategy, smoke the chain) */}
      <CockpitCard id="testorder" title="Test placement (manual OMS → T212 demo)" defaultOpen={false}>
        <TestPlacementPanel onPlaced={() => void loadOrders()} />
      </CockpitCard>

      {/* ── Cash ─────────────────────────────────────────────────── */}
      <CockpitCard
        id="cash"
        title="Cash"
        badge={cash?.free != null ? `${cash.currency ?? ""} ${cash.free.toLocaleString()}` : undefined}
        tone="ok"
      >
        {!cash ? (
          <span style={{ fontSize: 12, color: "var(--text-muted)" }}>Loading…</span>
        ) : !cash.enabled || cash.error ? (
          <span style={{ fontSize: 12, color: "var(--text-muted)" }}>
            {cash.error ?? cash.message ?? "Cash unavailable"}
          </span>
        ) : (
          <div style={{ display: "flex", gap: 22, flexWrap: "wrap" }}>
            <Stat label="Free" value={fmtMoney(cash.free, cash.currency)} big tone="ok" />
            <Stat label="Invested" value={fmtMoney(cash.invested, cash.currency)} />
            <Stat label="Total" value={fmtMoney(cash.total, cash.currency)} />
            {cash.ppl != null && (
              <Stat
                label="Open P&L"
                value={fmtMoney(cash.ppl, cash.currency)}
                tone={(cash.ppl ?? 0) >= 0 ? "ok" : "down"}
              />
            )}
            <span style={{ marginLeft: "auto", fontSize: 10, color: "var(--text-muted)" }}>
              T212 {cash.mode.toUpperCase()} · Invest (CFD cash separate)
            </span>
          </div>
        )}
      </CockpitCard>

      {/* ── Pending intents (action required) ───────────────────── */}
      <CockpitCard
        id="intents"
        title="Order generated — awaiting approval"
        badge={pending.length || undefined}
        tone={pending.length > 0 ? "warn" : "default"}
        defaultOpen
        actions={pending.length > 0 ? (
          <button
            onClick={approveAll}
            style={miniButton("ok")}
            title="Approve every pending intent"
          >
            approve all
          </button>
        ) : null}
      >
        {pending.length === 0 ? (
          <span style={{ fontSize: 12, color: "var(--text-muted)" }}>
            No intents waiting. The next strategy run will populate this list.
          </span>
        ) : (
          <OrdersTable
            rows={pending}
            acting={acting}
            onApprove={(id) => act(id, "approve")}
            onReject={(id) => act(id, "reject")}
            onCancel={(id) => act(id, "cancel")}
            allowApprove
          />
        )}
      </CockpitCard>

      {/* ── Submitted (in flight at broker) ─────────────────────── */}
      <CockpitCard
        id="submitted"
        title="Order placed — awaiting fill"
        badge={submitted.length || undefined}
      >
        {submitted.length === 0 ? (
          <span style={{ fontSize: 12, color: "var(--text-muted)" }}>Nothing in flight.</span>
        ) : (
          <OrdersTable
            rows={submitted}
            acting={acting}
            onApprove={() => {}}
            onReject={() => {}}
            onCancel={(id) => act(id, "cancel")}
          />
        )}
      </CockpitCard>

      {/* ── Recent fills ────────────────────────────────────────── */}
      <CockpitCard
        id="fills"
        title="Trade executed (recent)"
        badge={recent.length || undefined}
        tone={recent.length > 0 ? "ok" : "default"}
      >
        {recent.length === 0 ? (
          <span style={{ fontSize: 12, color: "var(--text-muted)" }}>No fills yet.</span>
        ) : (
          <OrdersTable rows={recent} acting={null} onApprove={() => {}} onReject={() => {}} onCancel={() => {}} />
        )}
      </CockpitCard>

      {/* ── Strategy signals (validate without broker) ──────────── */}
      {latestSessions.length > 0 && (
        <CockpitCard
          id="signals"
          title="Strategy signals — latest run per strategy"
          badge={latestSessions.reduce((n, s) =>
            n + s.decisions.filter((d) => d.action.startsWith("fire-")).length, 0) || undefined}
          tone="ok"
        >
          {latestSessions.map((s) => {
            const fired = s.decisions.filter((d) => d.action.startsWith("fire-"));
            const skipped = s.decisions.filter((d) => d.action.startsWith("skip-"));
            return (
              <div key={s.strategy} style={{ marginBottom: 14 }}>
                <div style={{ display: "flex", gap: 10, alignItems: "baseline", marginBottom: 4 }}>
                  <span style={{ fontWeight: 600, fontSize: 13 }}>{s.strategy}</span>
                  <span style={{ fontSize: 10, color: "var(--text-muted)" }}>
                    completed {s.completedAtUtc ? new Date(s.completedAtUtc).toLocaleTimeString() : "—"}
                    {" · "}{fired.length} fire · {skipped.length} skip
                  </span>
                  <Link
                    to={`/paper-live/session/${encodeURIComponent(s.requestId)}`}
                    style={{ marginLeft: "auto", fontSize: 10, color: "var(--text-muted)" }}
                  >
                    session detail →
                  </Link>
                </div>
                {fired.length === 0 ? (
                  <div style={{ fontSize: 11, color: "var(--text-dim)" }}>
                    No fire-* decisions this run (strategy chose not to trade — common when signal is in the dead zone).
                  </div>
                ) : (
                  <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 11 }}>
                    <thead>
                      <tr style={{ color: "var(--text-dim)" }}>
                        <th style={posTh}>Bar UTC</th>
                        <th style={posTh}>Symbol</th>
                        <th style={posTh}>Action</th>
                        <th style={posTh}>Reason</th>
                        <th style={posTh}>Detail</th>
                      </tr>
                    </thead>
                    <tbody>
                      {fired.slice(0, 10).map((d, i) => (
                        <tr key={i} style={{ borderTop: "1px solid var(--border)" }}>
                          <td style={{ ...posTd, fontFamily: "monospace", color: "var(--text-muted)" }}>
                            {d.barTs ? d.barTs.slice(11, 19) : "—"}
                          </td>
                          <td style={posTd}>{d.symbol}</td>
                          <td style={{ ...posTd, color: "#1fc16b", fontFamily: "monospace" }}>{d.action}</td>
                          <td style={posTd}>{d.reason}</td>
                          <td style={{ ...posTd, fontFamily: "monospace", fontSize: 10, color: "var(--text-muted)" }}>
                            {Object.keys(d.detail).length ? JSON.stringify(d.detail) : ""}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                )}
              </div>
            );
          })}
          <div style={{ fontSize: 10, color: "var(--text-muted)", marginTop: 4 }}>
            Showing top 10 fire-* decisions per strategy. Full timeline (incl. skip-* with reasons) in Session Detail.
          </div>
        </CockpitCard>
      )}

      {/* ── Positions ────────────────────────────────────────────── */}
      <CockpitCard
        id="positions"
        title="Overall position"
        badge={positions?.enabled ? positions.positionCount || undefined : undefined}
      >
        {!positions ? (
          <span style={{ fontSize: 12, color: "var(--text-muted)" }}>Loading…</span>
        ) : !positions.enabled ? (
          <span style={{ fontSize: 12, color: "var(--text-muted)" }}>
            T212 {account} not connected.
          </span>
        ) : positions.positionCount === 0 ? (
          <span style={{ fontSize: 12, color: "var(--text-muted)" }}>
            No open positions in T212 {account}.
          </span>
        ) : (
          <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 12 }}>
            <thead>
              <tr style={{ color: "var(--text-dim)" }}>
                <th style={posTh}>Ticker</th>
                <th style={{ ...posTh, textAlign: "right" }}>Qty</th>
                <th style={{ ...posTh, textAlign: "right" }}>Avg cost</th>
                <th style={{ ...posTh, textAlign: "right" }}>Now</th>
                <th style={{ ...posTh, textAlign: "right" }}>P&L %</th>
                <th style={{ ...posTh, textAlign: "right" }}>P&L</th>
              </tr>
            </thead>
            <tbody>
              {positions.positions.map((p) => (
                <tr key={p.ticker} style={{ borderTop: "1px solid var(--border)" }}>
                  <td style={posTd}>{p.ticker}</td>
                  <td style={{ ...posTd, textAlign: "right", fontFamily: "monospace" }}>{p.quantity}</td>
                  <td style={{ ...posTd, textAlign: "right", fontFamily: "monospace" }}>{p.averagePricePaid?.toFixed(4) ?? "—"}</td>
                  <td style={{ ...posTd, textAlign: "right", fontFamily: "monospace" }}>{p.currentPrice?.toFixed(4) ?? "—"}</td>
                  <td style={{ ...posTd, textAlign: "right", fontFamily: "monospace", color: (p.unrealisedPct ?? 0) >= 0 ? "#1fc16b" : "#ef4444" }}>
                    {p.unrealisedPct != null ? `${p.unrealisedPct >= 0 ? "+" : ""}${p.unrealisedPct.toFixed(2)}%` : "—"}
                  </td>
                  <td style={{ ...posTd, textAlign: "right", fontFamily: "monospace", color: (p.unrealisedAbs ?? 0) >= 0 ? "#1fc16b" : "#ef4444" }}>
                    {p.unrealisedAbs != null ? `${p.unrealisedAbs >= 0 ? "+" : ""}${p.unrealisedAbs.toFixed(2)}` : "—"}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
        <div style={{ marginTop: 8, fontSize: 10, color: "var(--text-muted)" }}>
          Detailed view: <Link to="/portfolio" style={{ color: "var(--text-muted)" }}>Portfolio →</Link>
          {" · "}Per-order drill-in: <Link to="/oms" style={{ color: "var(--text-muted)" }}>OMS →</Link>
        </div>
      </CockpitCard>
    </div>
  );
}

function OrdersTable({
  rows, acting, onApprove, onReject, onCancel, allowApprove,
}: {
  rows: OmsOrderRow[];
  acting: string | null;
  onApprove: (id: string) => void;
  onReject: (id: string) => void;
  onCancel: (id: string) => void;
  allowApprove?: boolean;
}) {
  return (
    <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 12 }}>
      <thead>
        <tr style={{ color: "var(--text-dim)" }}>
          <th style={posTh}>Time</th>
          <th style={posTh}>Strategy</th>
          <th style={posTh}>Symbol</th>
          <th style={posTh}>Side</th>
          <th style={{ ...posTh, textAlign: "right" }}>Qty</th>
          <th style={posTh}>State</th>
          <th style={posTh}>Actions</th>
        </tr>
      </thead>
      <tbody>
        {rows.map((o) => {
          const busy = acting?.startsWith(o.id);
          return (
            <tr key={o.id} style={{ borderTop: "1px solid var(--border)" }}>
              <td style={{ ...posTd, fontFamily: "monospace", color: "var(--text-muted)" }}>
                {o.createdAtUtc.slice(11, 19)}
              </td>
              <td style={posTd}>{o.strategyId ?? "—"}</td>
              <td style={posTd}>{o.symbol}</td>
              <td style={{ ...posTd, color: o.side === "BUY" ? "#1fc16b" : "#ef4444" }}>{o.side}</td>
              <td style={{ ...posTd, textAlign: "right", fontFamily: "monospace" }}>{o.qty}</td>
              <td style={posTd}>
                <span style={{ fontSize: 10, padding: "1px 6px", borderRadius: 999, background: "rgba(255,255,255,0.06)", color: "var(--text-dim)" }}>
                  {o.state}
                </span>
              </td>
              <td style={{ ...posTd, whiteSpace: "nowrap" }}>
                {allowApprove && o.state === "PENDING_APPROVAL" && (
                  <>
                    <button onClick={() => onApprove(o.id)} disabled={busy} style={miniButton("ok")}>approve</button>{" "}
                    <button onClick={() => onReject(o.id)} disabled={busy} style={miniButton("down")}>reject</button>{" "}
                  </>
                )}
                {["PENDING_APPROVAL", "SUBMITTED", "WORKING", "PARTIALLY_FILLED"].includes(o.state) && (
                  <button onClick={() => onCancel(o.id)} disabled={busy} style={miniButton("muted")}>cancel</button>
                )}
                <Link
                  to="/oms"
                  style={{ ...miniButton("muted"), textDecoration: "none", marginLeft: 4 }}
                >
                  →
                </Link>
              </td>
            </tr>
          );
        })}
      </tbody>
    </table>
  );
}

function Stat({
  label, value, big, tone,
}: {
  label: string;
  value: string;
  big?: boolean;
  tone?: "ok" | "down";
}) {
  const fg = tone === "ok" ? "#1fc16b" : tone === "down" ? "#ef4444" : "var(--text)";
  return (
    <div>
      <div style={{ fontSize: 10, color: "var(--text-muted)", letterSpacing: "0.04em", textTransform: "uppercase" }}>{label}</div>
      <div style={{ fontSize: big ? 20 : 14, fontWeight: big ? 700 : 500, color: fg, fontFamily: "monospace" }}>{value}</div>
    </div>
  );
}

/**
 * TestPlacementPanel — operator smoke test for the OMS → T212 demo
 * chain. Bypasses the strategy / Mac daemon entirely: submits an
 * OrderIntent directly to /api/oms/orders and auto-approves so the
 * .NET OmsService → Trading212DemoClient → T212 demo path runs end-
 * to-end. Useful for verifying the broker wiring before triggering a
 * real strategy session, and for sanity checks after a redeploy.
 *
 * Defaults: BUY 1 AAPL (small enough to not move T212's demo cash
 * meaningfully; symbol T212 always has). Operator can override.
 *
 * After approve, the fill poller picks up T212's fill within ~30s
 * and transitions OMS to FILLED — visible in cockpit "Order placed"
 * → "Trade executed".
 */
function TestPlacementPanel({ onPlaced }: { onPlaced: () => void }) {
  const [symbol, setSymbol] = useState("AAPL");
  const [qty, setQty] = useState(1);
  const [side, setSide] = useState<"BUY" | "SELL">("BUY");
  const [submitting, setSubmitting] = useState(false);
  const [feedback, setFeedback] = useState<string | null>(null);

  const fire = async () => {
    setSubmitting(true);
    setFeedback(null);
    try {
      // Generate a uuid for ClientOrderId — browser crypto API gives
      // us this without an extra library. OMS dedupes on it so a
      // double-click doesn't double-place.
      const clientOrderId = crypto.randomUUID();
      const enqueued = await api.omsEnqueue({
        ClientOrderId: clientOrderId,
        Broker: "T212_DEMO",
        Symbol: symbol.toUpperCase(),
        Side: side,
        Qty: qty,
        OrderType: "MKT",
        StrategyId: "manual_test_cockpit",
        PlacedBy: "HUMAN",
        TimeInForce: "DAY",
      });
      // Auto-approve so it actually places at T212.
      await api.omsApprove(enqueued.id);
      setFeedback(
        `✓ Enqueued + approved ${side} ${qty} ${symbol.toUpperCase()} — watch "Order placed" / "Trade executed" panels.`,
      );
      onPlaced();
    } catch (e) {
      setFeedback(`Failed: ${e}`);
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div>
      <div style={{ fontSize: 11, color: "var(--text-muted)", marginBottom: 10, lineHeight: 1.5 }}>
        Bypasses the strategy + Mac daemon — creates an OMS intent
        directly + auto-approves so the .NET OmsService → T212 demo
        chain runs end-to-end. Use after a redeploy to verify nothing
        broke before triggering a real strategy run.
      </div>
      <div style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "end" }}>
        <FieldGroup label="Symbol">
          <input
            type="text"
            value={symbol}
            onChange={(e) => setSymbol(e.target.value)}
            style={triggerInput}
          />
        </FieldGroup>
        <FieldGroup label="Side">
          <div style={{ display: "flex", gap: 4 }}>
            {(["BUY", "SELL"] as const).map((s) => (
              <button
                key={s}
                onClick={() => setSide(s)}
                style={{
                  ...triggerInput,
                  width: 56,
                  cursor: "pointer",
                  color: side === s
                    ? s === "BUY" ? "#1fc16b" : "#ef4444"
                    : "var(--text-dim)",
                  borderColor: side === s
                    ? s === "BUY" ? "#1fc16b" : "#ef4444"
                    : "var(--border)",
                  fontWeight: side === s ? 600 : 400,
                  textAlign: "center",
                }}
              >
                {s}
              </button>
            ))}
          </div>
        </FieldGroup>
        <FieldGroup label="Qty">
          <input
            type="number"
            min={1}
            max={1000}
            value={qty}
            onChange={(e) => setQty(Math.max(1, Number(e.target.value) || 1))}
            style={{ ...triggerInput, width: 80 }}
          />
        </FieldGroup>
        <button
          onClick={fire}
          disabled={submitting}
          style={{
            padding: "6px 14px", fontSize: 12, fontWeight: 600,
            background: submitting ? "var(--text-muted)" : "#4f8cff",
            color: "white", border: "none", borderRadius: 4,
            cursor: submitting ? "wait" : "pointer",
          }}
        >
          {submitting ? "Placing…" : `Fire ${side} ${qty} ${symbol.toUpperCase()}`}
        </button>
      </div>
      {feedback && (
        <div style={{
          marginTop: 8, fontSize: 11,
          color: feedback.startsWith("✓") ? "#1fc16b" : "var(--down)",
        }}>
          {feedback}
        </div>
      )}
    </div>
  );
}

/**
 * TriggerPanel — compact form to fire a strategy session without
 * navigating to /strategies. Loads the strategy catalog once,
 * renders the strategies as pills (no dropdowns per memory rule),
 * click expands an inline form with symbol + date + lookback +
 * Run. Defaults pre-filled from strategy.default_lookback_days.
 */
function TriggerPanel({ onTriggered }: { onTriggered: () => void }) {
  type Strat = Awaited<ReturnType<typeof api.paperStrategies>>["strategies"][number];
  const [strategies, setStrategies] = useState<Strat[]>([]);
  const [selected, setSelected] = useState<Strat | null>(null);
  const [symbol, setSymbol] = useState("");
  const todayIso = new Date().toISOString().slice(0, 10);
  const [date, setDate] = useState(todayIso);
  const [lookback, setLookback] = useState<number | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [feedback, setFeedback] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    api.paperStrategies()
      .then((r) => { if (!cancelled) setStrategies(r.strategies); })
      .catch((e) => { if (!cancelled) setFeedback(`Strategy catalog failed: ${e}`); });
    return () => { cancelled = true; };
  }, []);

  const pick = (s: Strat) => {
    setSelected(s);
    setLookback(s.default_lookback_days ?? 0);
    // FX strategy defaults to G10 — no symbol needed.
    if (s.name === "ichimoku_fx_mr") setSymbol("");
    else if (!symbol) setSymbol("AAPL");
  };

  const run = async () => {
    if (!selected) return;
    const isFx = selected.name === "ichimoku_fx_mr";
    if (!isFx && !symbol.trim()) {
      setFeedback("Enter a symbol before triggering");
      return;
    }
    setSubmitting(true);
    setFeedback(null);
    try {
      await api.runIntraday({
        strategy: selected.name,
        symbols: isFx ? [] : [symbol.trim().toUpperCase()],
        session_date: date,
        lookback_days: lookback ?? 0,
        params: selected.default_params,
      });
      setFeedback(`✓ Queued ${selected.name} for ${date}`);
      onTriggered();
    } catch (e) {
      setFeedback(`Failed: ${e}`);
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div>
      {strategies.length === 0 ? (
        <div style={{ fontSize: 12, color: "var(--text-muted)" }}>
          Loading strategies…
        </div>
      ) : (
        <>
          <div style={{ display: "flex", flexWrap: "wrap", gap: 6, marginBottom: 10 }}>
            {strategies.map((s) => {
              const isSelected = selected?.name === s.name;
              const tone = s.source === "trader-quant" ? "#1fc16b"
                : s.source === "alpha-engine" ? "#4f8cff"
                : "var(--text-dim)";
              return (
                <button
                  key={s.name}
                  onClick={() => pick(s)}
                  style={{
                    padding: "4px 11px", fontSize: 11, borderRadius: 999,
                    border: `1px solid ${isSelected ? tone : "var(--border)"}`,
                    background: isSelected ? `${tone}1a` : "transparent",
                    color: isSelected ? tone : "var(--text-dim)",
                    cursor: "pointer", fontFamily: "monospace",
                  }}
                  title={s.summary}
                >
                  {s.name}
                </button>
              );
            })}
          </div>
          {selected && (
            <div style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "end" }}>
              <FieldGroup label="Symbol">
                <input
                  type="text"
                  placeholder={selected.name === "ichimoku_fx_mr" ? "G10 (auto)" : "AAPL"}
                  value={symbol}
                  onChange={(e) => setSymbol(e.target.value)}
                  disabled={selected.name === "ichimoku_fx_mr"}
                  style={triggerInput}
                />
              </FieldGroup>
              <FieldGroup label="Session date">
                <input
                  type="date"
                  value={date}
                  max={todayIso}
                  onChange={(e) => setDate(e.target.value)}
                  style={triggerInput}
                />
              </FieldGroup>
              <FieldGroup label="Lookback (days)">
                <input
                  type="number"
                  min={0}
                  max={365}
                  value={lookback ?? 0}
                  onChange={(e) => setLookback(Number(e.target.value))}
                  style={{ ...triggerInput, width: 70 }}
                />
              </FieldGroup>
              <button
                onClick={run}
                disabled={submitting}
                style={{
                  padding: "6px 14px", fontSize: 12, fontWeight: 600,
                  background: submitting ? "var(--text-muted)" : "#1fc16b",
                  color: "white", border: "none", borderRadius: 4,
                  cursor: submitting ? "wait" : "pointer",
                }}
              >
                {submitting ? "Queueing…" : "Run"}
              </button>
            </div>
          )}
          {feedback && (
            <div style={{
              marginTop: 8, fontSize: 11,
              color: feedback.startsWith("✓") ? "#1fc16b" : "var(--down)",
            }}>
              {feedback}
            </div>
          )}
          {selected && (
            <div style={{ marginTop: 8, fontSize: 10, color: "var(--text-muted)" }}>
              {selected.summary}
            </div>
          )}
        </>
      )}
    </div>
  );
}

function FieldGroup({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 2 }}>
      <span style={{ fontSize: 9, color: "var(--text-muted)", letterSpacing: "0.04em", textTransform: "uppercase" }}>
        {label}
      </span>
      {children}
    </div>
  );
}

const triggerInput: React.CSSProperties = {
  padding: "5px 8px", fontSize: 12,
  border: "1px solid var(--border)", borderRadius: 4,
  background: "transparent", color: "var(--text)",
};

function fmtMoney(n: number | null | undefined, ccy?: string | null): string {
  if (n == null) return "—";
  const c = ccy || "";
  return `${c} ${n.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
}

const posTh: React.CSSProperties = {
  textAlign: "left", padding: "4px 8px", fontSize: 10, fontWeight: 700,
  letterSpacing: "0.04em", textTransform: "uppercase",
};
const posTd: React.CSSProperties = { padding: "4px 8px" };

function miniButton(tone: "ok" | "down" | "muted"): React.CSSProperties {
  const fg = tone === "ok" ? "#1fc16b" : tone === "down" ? "#ef4444" : "var(--text-dim)";
  const border = tone === "ok" ? "#1fc16b" : tone === "down" ? "#ef4444" : "var(--border)";
  return {
    fontSize: 10, padding: "2px 7px",
    border: `1px solid ${border}`, borderRadius: 3,
    background: "transparent", color: fg, cursor: "pointer",
    fontWeight: 500,
  };
}

/**
 * DeskTabs — the tabbed work-area: Positions · Orders · Trades · Balances.
 * Underline-active, blue (IBKR style). Positions is the centerpiece (its own
 * component); the others wire to existing endpoints with simple dense tables:
 *   - Orders   → api.omsOrders() (live OMS orders, all states)
 *   - Trades   → api.omsOrders(400) filtered to FILLED/PARTIALLY_FILLED fills
 *                (larger window so real fills aren't lost behind CANCELLEDs);
 *                search by symbol + sortable columns + clickable row→ audit detail
 *   - Balances → per-broker cash from api.cashSummary(), native currency
 *
 * Tabs are pills (the user dislikes dropdowns on primary surfaces), so state
 * stays visible. Tables share the same overflow-x:auto + min-width treatment
 * as PositionsTable so they scroll within the card on a phone.
 */
import { useCallback, useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { api, type OmsOrderRow, type OmsOrderEventRow } from "../../api/client";
import { prettySymbol, bareSymbol, productOf } from "../../util/brokerSymbols";
import { fmtWhenDate } from "../../util/time";
import { fmtQty } from "../../util/numbers";
import { useSort } from "../../util/useSort";
import { PositionsByStrategy, type PositionRow } from "./PositionsByStrategy";
import { fmtMoney, fmtNum, signColour } from "./deskFormat";

type Tab = "positions" | "orders" | "trades" | "balances";

// api.omsOrders(undefined, ORDER_FETCH_LIMIT) returns the most-recent orders
// across ALL dates, newest-first, capped at this count (backend:
// PostgresOmsService.ListAsync → ORDER BY created_at_utc DESC LIMIT n). There
// is NO day window — so the honest label is "most recent N", not "today" or
// "last N days". Surfaced as a subheader on Orders & Trades so the user can
// tell at a glance exactly what period/scope they're looking at.
const ORDER_FETCH_LIMIT = 100;

// Trades tab fetches a larger window so real FILLED orders don't fall outside
// the cap. Most orders in practice are CANCELLED (strategy flips), so 400
// ensures we surface at least the last 50–100 actual fills across all dates.
const TRADES_FETCH_LIMIT = 400;

// Maximum fills shown in the Trades table (newest first after filter).
const TRADES_SHOW_MAX = 100;

const TABS: { key: Tab; label: string }[] = [
  { key: "positions", label: "Positions" },
  { key: "orders", label: "Orders" },
  { key: "trades", label: "Trades" },
  { key: "balances", label: "Balances" },
];

export function DeskTabs({
  onOpenSymbol,
  onPositionsRowsChange,
}: {
  onOpenSymbol?: (symbol: string) => void;
  /** Forwarded from PositionsByStrategy so Desk.tsx can keep a copy of rows
   * for the SymbolPositionCard in the right rail (avoids a second fetch). */
  onPositionsRowsChange?: (rows: PositionRow[]) => void;
}) {
  const [tab, setTab] = useState<Tab>("positions");

  return (
    <div
      style={{
        border: "1px solid #1b2233", borderRadius: 8,
        background: "rgba(255,255,255,0.015)", overflow: "hidden",
      }}
    >
      <div style={{ display: "flex", gap: 4, borderBottom: "1px solid #1b2233", padding: "0 8px" }}>
        {TABS.map((t) => {
          const active = tab === t.key;
          return (
            <button
              key={t.key}
              onClick={() => setTab(t.key)}
              style={{
                background: "transparent", border: "none", cursor: "pointer",
                padding: "10px 12px", fontSize: 12, fontWeight: 600,
                color: active ? "var(--accent, #4f8cff)" : "var(--text-muted)",
                borderBottom: `2px solid ${active ? "var(--accent, #4f8cff)" : "transparent"}`,
                marginBottom: -1,
              }}
            >
              {t.label}
            </button>
          );
        })}
      </div>
      <div style={{ padding: 10 }}>
        {tab === "positions" && (
          <PositionsByStrategy
            onSelectSymbol={onOpenSymbol}
            onRowsChange={onPositionsRowsChange}
          />
        )}
        {tab === "orders" && <OrdersTab onOpenSymbol={onOpenSymbol} />}
        {tab === "trades" && <TradesTab onOpenSymbol={onOpenSymbol} />}
        {tab === "balances" && <BalancesTab />}
      </div>
    </div>
  );
}

/** Orders — all live OMS orders, newest-first, capped (no day window). */
function OrdersTab({ onOpenSymbol }: { onOpenSymbol?: (symbol: string) => void }) {
  const orders = useOrders();
  return (
    <>
      <WindowLabel
        text={`Most recent ${ORDER_FETCH_LIMIT} orders · all states · newest first`}
        sub="Across all dates (not today-only) — capped, not a time window."
      />
      <OrderTable rows={orders.rows} loading={orders.loading} err={orders.err} empty="No orders." onOpenSymbol={onOpenSymbol} />
    </>
  );
}

/** Trades — FILLED/PARTIALLY_FILLED orders with search, sort, and audit detail. */
function TradesTab({ onOpenSymbol }: { onOpenSymbol?: (symbol: string) => void }) {
  const orders = useTrades();
  const [search, setSearch] = useState("");
  const [expandedId, setExpandedId] = useState<string | null>(null);

  // Filter to fills only, then apply symbol search.
  const fills = useMemo(() => {
    const f = orders.rows.filter(
      (o) => o.state === "FILLED" || o.state === "PARTIALLY_FILLED",
    );
    const q = search.trim().toUpperCase();
    if (!q) return f.slice(0, TRADES_SHOW_MAX);
    return f.filter((o) => bareSymbol(o.symbol).includes(q) || prettySymbol(o.symbol).toUpperCase().includes(q))
      .slice(0, TRADES_SHOW_MAX);
  }, [orders.rows, search]);

  const ACCESSORS = useMemo(() => ({
    date:     (o: OmsOrderRow) => o.lastStateChangeAtUtc,
    symbol:   (o: OmsOrderRow) => prettySymbol(o.symbol),
    side:     (o: OmsOrderRow) => o.side,
    qty:      (o: OmsOrderRow) => o.filledQty,
    strategy: (o: OmsOrderRow) => o.strategyId ?? "",
    price:    (o: OmsOrderRow) => o.avgFillPrice ?? 0,
  }), []);

  const { sorted, sortKey, dir, toggle } = useSort(fills, ACCESSORS, { key: "date", dir: "desc" });

  const handleRowClick = useCallback((id: string) => {
    setExpandedId((prev) => (prev === id ? null : id));
  }, []);

  return (
    <>
      <div style={{ display: "flex", alignItems: "center", gap: 10, padding: "2px 4px 8px" }}>
        <div style={{ flex: 1, lineHeight: 1.4 }}>
          <span style={{ fontSize: 11, fontWeight: 600, color: "var(--text-muted)" }}>
            Last {TRADES_SHOW_MAX} fills (from most recent {TRADES_FETCH_LIMIT} orders) · newest first
          </span>
          <span style={{ fontSize: 10, color: "var(--text-dim)", marginLeft: 8 }}>
            Across all dates — click a row to see the order audit trail.
          </span>
        </div>
        <input
          type="search"
          placeholder="Filter symbol…"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          style={{
            background: "#0d1117",
            border: "1px solid #1b2233",
            borderRadius: 4,
            color: "var(--text)",
            fontSize: 11,
            padding: "4px 8px",
            width: 140,
            outline: "none",
          }}
        />
      </div>
      <TradesTable
        rows={sorted}
        loading={orders.loading}
        err={orders.err}
        sortKey={sortKey}
        dir={dir}
        toggle={toggle}
        expandedId={expandedId}
        onRowClick={handleRowClick}
        onOpenSymbol={onOpenSymbol}
      />
    </>
  );
}

/** Sortable trades table with expandable audit row. */
function TradesTable({
  rows, loading, err, sortKey, dir, toggle, expandedId, onRowClick, onOpenSymbol,
}: {
  rows: OmsOrderRow[];
  loading: boolean;
  err: string | null;
  sortKey: string | null;
  dir: "asc" | "desc";
  toggle: (k: string) => void;
  expandedId: string | null;
  onRowClick: (id: string) => void;
  onOpenSymbol?: (symbol: string) => void;
}) {
  if (loading && rows.length === 0) return <Note>Loading…</Note>;
  if (err) return <Note tone="down">Unavailable: {err}</Note>;
  if (rows.length === 0) return <Note>No fills yet (searched {TRADES_FETCH_LIMIT} most-recent orders).</Note>;

  const Th = ({ col, label, right }: { col: string; label: string; right?: boolean }) => {
    const active = sortKey === col;
    const arrow = active ? (dir === "asc" ? " ↑" : " ↓") : "";
    return (
      <th
        style={{ ...(right ? TH_R : TH), cursor: "pointer", userSelect: "none", whiteSpace: "nowrap",
          color: active ? "var(--accent, #4f8cff)" : undefined }}
        onClick={() => toggle(col)}
      >
        {label}{arrow}
      </th>
    );
  };

  return (
    <div style={{ overflowX: "auto", maxWidth: "100%" }}>
      <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 12, minWidth: 680 }}>
        <thead>
          <tr style={{ color: "var(--text-dim)", borderBottom: "1px solid #1b2233" }}>
            <Th col="date"     label="Fill Time" />
            <Th col="symbol"   label="Symbol" />
            <Th col="strategy" label="Strategy" />
            <Th col="side"     label="Side" />
            <Th col="qty"      label="Filled Qty" right />
            <Th col="price"    label="Avg Fill Px" right />
            <th style={TH}>State</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((o) => (
            <>
              <tr
                key={o.id}
                onClick={() => onRowClick(o.id)}
                style={{
                  borderBottom: expandedId === o.id ? "none" : "1px solid #141b2b",
                  cursor: "pointer",
                  background: expandedId === o.id ? "rgba(79,140,255,0.06)" : undefined,
                }}
                title="Click to see order audit"
              >
                <td style={{ ...TD, color: "var(--text-muted)" }}>
                  {fmtWhenDate(o.lastStateChangeAtUtc)}
                </td>
                <td style={{ ...TD, fontWeight: 700 }}>
                  <SymbolCell raw={o.symbol} onOpenSymbol={onOpenSymbol} />
                </td>
                <td style={{ ...TD, color: "var(--text-muted)" }}>
                  <StrategyCell id={o.strategyId} />
                </td>
                <td style={{ ...TD, color: o.side === "BUY" ? "var(--up, #1fc16b)" : "var(--down, #ef4444)", fontWeight: 700 }}>
                  {o.side}
                </td>
                <td style={TD_R}>{fmtNum(fmtQty(o.filledQty))}</td>
                <td style={TD_R}>{fmtMoney(o.avgFillPrice)}</td>
                <td style={TD}>{o.state}</td>
              </tr>
              {expandedId === o.id && (
                <tr key={`${o.id}-detail`} style={{ borderBottom: "1px solid #141b2b" }}>
                  <td colSpan={7} style={{ padding: 0 }}>
                    <TradeAuditPanel order={o} />
                  </td>
                </tr>
              )}
            </>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/** Expanded audit panel for a single fill — shows order metadata + state
 * history from api.omsOrderAudit. Lazy-loaded on first expand. */
function TradeAuditPanel({ order }: { order: OmsOrderRow }) {
  const [audit, setAudit] = useState<{
    events: OmsOrderEventRow[];
    riskEvents: Array<{ occurred_at_utc: string; gate: string; decision: string; reason: string | null }>;
    llmEvals: Array<{ occurred_at_utc: string; purpose: string; decision: string; confidence: number | null; reasoning: string | null }>;
  } | null>(null);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    let live = true;
    api.omsOrderAudit(order.id)
      .then((r) => { if (live) { setAudit(r); setLoading(false); } })
      .catch((e) => { if (live) { setErr(e instanceof Error ? e.message : String(e)); setLoading(false); } });
    return () => { live = false; };
  }, [order.id]);

  return (
    <div style={{
      background: "rgba(13,17,23,0.7)",
      borderTop: "1px solid #1b2233",
      borderBottom: "1px solid #1b2233",
      padding: "10px 14px",
      fontSize: 11,
    }}>
      {/* Order metadata strip */}
      <div style={{ display: "flex", gap: 20, flexWrap: "wrap", marginBottom: 8, color: "var(--text-muted)" }}>
        <span><b>Order ID</b> <Link to="/oms" style={{ color: "var(--accent, #4f8cff)", fontFamily: "monospace" }}>{order.clientOrderId}</Link></span>
        <span><b>Broker</b> {order.broker}</span>
        <span><b>Type</b> {order.orderType}</span>
        {order.limitPrice != null && <span><b>Limit</b> {fmtMoney(order.limitPrice)}</span>}
        {order.brokerOrderId && <span><b>Broker OID</b> {order.brokerOrderId}</span>}
        <span><b>Placed by</b> {order.placedBy}</span>
        <span><b>Created</b> {fmtWhenDate(order.createdAtUtc)}</span>
      </div>

      {loading && <span style={{ color: "var(--text-dim)" }}>Loading audit…</span>}
      {err   && <span style={{ color: "var(--down, #ef4444)" }}>Audit unavailable: {err}</span>}

      {audit && (
        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 12 }}>
          {/* State transitions */}
          <div>
            <div style={{ fontWeight: 700, color: "var(--text-muted)", marginBottom: 4 }}>
              State history ({audit.events.length})
            </div>
            {audit.events.length === 0 ? (
              <span style={{ color: "var(--text-dim)" }}>No events.</span>
            ) : (
              <div style={{ display: "flex", flexDirection: "column", gap: 3 }}>
                {audit.events.map((ev) => (
                  <div key={ev.id} style={{ display: "flex", gap: 8, color: "var(--text-muted)" }}>
                    <span style={{ color: "var(--text-dim)", whiteSpace: "nowrap" }}>
                      {fmtWhenDate(ev.occurredAtUtc)}
                    </span>
                    <span style={{ fontWeight: 600 }}>{ev.eventType}</span>
                    {ev.priorState && <span style={{ color: "var(--text-dim)" }}>{ev.priorState} → {ev.newState}</span>}
                    <span style={{ color: "var(--text-dim)" }}>by {ev.actor}</span>
                  </div>
                ))}
              </div>
            )}
          </div>

          {/* Decision: risk events + LLM evals */}
          <div>
            <div style={{ fontWeight: 700, color: "var(--text-muted)", marginBottom: 4 }}>
              Decision ({audit.riskEvents.length} risk · {audit.llmEvals.length} LLM)
            </div>
            {audit.riskEvents.length === 0 && audit.llmEvals.length === 0 ? (
              <span style={{ color: "var(--text-dim)" }}>No gate events.</span>
            ) : (
              <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
                {audit.riskEvents.map((ev, i) => (
                  <div key={i} style={{ display: "flex", gap: 8, flexWrap: "wrap", color: "var(--text-muted)" }}>
                    <span style={{ color: "var(--text-dim)", whiteSpace: "nowrap" }}>{fmtWhenDate(ev.occurred_at_utc)}</span>
                    <span style={{ fontWeight: 600 }}>{ev.gate}</span>
                    <span style={{ color: ev.decision === "PASS" ? "var(--up, #1fc16b)" : "var(--down, #ef4444)", fontWeight: 700 }}>{ev.decision}</span>
                    {ev.reason && <span style={{ color: "var(--text-dim)", fontStyle: "italic" }}>{ev.reason}</span>}
                  </div>
                ))}
                {audit.llmEvals.map((ev, i) => (
                  <div key={i} style={{ display: "flex", gap: 8, flexWrap: "wrap", color: "var(--text-muted)" }}>
                    <span style={{ color: "var(--text-dim)", whiteSpace: "nowrap" }}>{fmtWhenDate(ev.occurred_at_utc)}</span>
                    <span style={{ fontWeight: 600 }}>LLM · {ev.purpose}</span>
                    <span style={{ color: ev.decision === "APPROVE" ? "var(--up, #1fc16b)" : "var(--down, #ef4444)", fontWeight: 700 }}>{ev.decision}</span>
                    {ev.confidence != null && <span style={{ color: "var(--text-dim)" }}>conf {(ev.confidence * 100).toFixed(0)}%</span>}
                    {ev.reasoning && (
                      <span style={{ color: "var(--text-dim)", fontStyle: "italic", maxWidth: 320,
                        overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}
                        title={ev.reasoning}>
                        "{ev.reasoning}"
                      </span>
                    )}
                  </div>
                ))}
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  );
}

/** Small subheader that states the scope/window a table covers, so the user
 * can tell at a glance what period they're looking at (the user dislikes
 * unlabelled numbers — every surface must be self-explanatory). */
function WindowLabel({ text, sub }: { text: string; sub: string }) {
  return (
    <div style={{ padding: "2px 4px 10px", lineHeight: 1.4 }} title={sub}>
      <span style={{ fontSize: 11, fontWeight: 600, color: "var(--text-muted)" }}>{text}</span>
      <span style={{ fontSize: 10, color: "var(--text-dim)", marginLeft: 8 }}>{sub}</span>
    </div>
  );
}

/** Chart symbol for an OMS order row, or null when there's no honest Yahoo
 * candle symbol (IG FX/CFD epics, options, futures). Equity tickers reduce to
 * the bare symbol which Yahoo accepts. */
function orderChartSymbol(raw: string): string | null {
  return productOf(raw) === "Equity" ? bareSymbol(raw) : null;
}

function useOrdersWithLimit(limit: number) {
  const [rows, setRows] = useState<OmsOrderRow[]>([]);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => {
    let live = true;
    const load = () => {
      api.omsOrders(undefined, limit)
        .then((r) => { if (live) { setRows(r.orders); setErr(null); } })
        .catch((e) => { if (live) setErr(e instanceof Error ? e.message : String(e)); })
        .finally(() => { if (live) setLoading(false); });
    };
    void load();
    const t = setInterval(load, 60_000);
    return () => { live = false; clearInterval(t); };
  }, [limit]);
  return { rows, loading, err };
}

/** Orders tab: most-recent ORDER_FETCH_LIMIT orders (all states). */
function useOrders() { return useOrdersWithLimit(ORDER_FETCH_LIMIT); }

/** Trades tab: fetch a larger window so fills don't fall outside the cap. */
function useTrades() { return useOrdersWithLimit(TRADES_FETCH_LIMIT); }

function OrderTable({
  rows, loading, err, empty, showFill, onOpenSymbol,
}: {
  rows: OmsOrderRow[];
  loading: boolean;
  err: string | null;
  empty: string;
  showFill?: boolean;
  onOpenSymbol?: (symbol: string) => void;
}) {
  if (loading && rows.length === 0) return <Note>Loading…</Note>;
  if (err) return <Note tone="down">Unavailable: {err}</Note>;
  if (rows.length === 0) return <Note>{empty}</Note>;
  return (
    <div style={{ overflowX: "auto", maxWidth: "100%" }}>
      <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 12, minWidth: 720 }}>
        <thead>
          <tr style={{ color: "var(--text-dim)", borderBottom: "1px solid #1b2233" }}>
            <th style={TH}>Date / Time</th>
            <th style={TH}>Symbol</th>
            <th style={TH}>Strategy</th>
            <th style={TH}>Side</th>
            <th style={TH_R}>Qty</th>
            <th style={TH_R}>{showFill ? "Fill Px" : "Limit"}</th>
            <th style={TH}>State</th>
            <th style={TH}>Order</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((o) => (
            <tr key={o.id} style={{ borderBottom: "1px solid #141b2b" }}>
              {/* Date + time (not just HH:MM:SS). Orders show when the order was
                  created; the Trades tab (showFill) shows the fill / terminal
                  state-change time. */}
              <td style={{ ...TD, color: "var(--text-muted)" }}>
                {fmtWhenDate(showFill ? o.lastStateChangeAtUtc : (o.createdAtUtc || o.lastStateChangeAtUtc))}
              </td>
              <td style={{ ...TD, fontWeight: 700 }}>
                <SymbolCell raw={o.symbol} onOpenSymbol={onOpenSymbol} />
              </td>
              <td style={{ ...TD, color: "var(--text-muted)" }}>
                <StrategyCell id={o.strategyId} />
              </td>
              <td style={{ ...TD, color: o.side === "BUY" ? "var(--up, #1fc16b)" : "var(--down, #ef4444)", fontWeight: 700 }}>
                {o.side}
              </td>
              <td style={TD_R}>{fmtNum(fmtQty(showFill ? o.filledQty : o.qty))}</td>
              <td style={TD_R}>{showFill ? fmtMoney(o.avgFillPrice) : fmtMoney(o.limitPrice)}</td>
              <td style={TD}>{o.state}</td>
              <td style={TD}>
                <Link to="/oms" title="Open in OMS" style={{ color: "var(--accent, #4f8cff)", fontFamily: "monospace" }}>
                  {o.clientOrderId.slice(0, 10)}
                </Link>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/** Balances — per-broker cash, native currency (never summed). */
function BalancesTab() {
  const [rows, setRows] = useState<Awaited<ReturnType<typeof api.cashSummary>>["brokers"]>([]);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => {
    let live = true;
    const load = () => {
      // cashSummary covers T212/IG/IBKR-LIVE. The IBKR PAPER clone (DUP656969)
      // isn't there — the live IBKRClient only sees IBKR_LIVE — so pull it from
      // broker_account_state (the daemon push) and append it.
      Promise.all([api.cashSummary(), api.accountState().catch(() => null)])
        .then(([cash, acct]) => {
          if (!live) return;
          const have = new Set(cash.brokers.map((b) => (b.broker || "").toLowerCase()));
          const clones = (acct?.accounts ?? [])
            .filter((a) => !have.has((a.broker || "").toLowerCase()))
            .map((a) => ({
              broker: a.broker,
              label: a.broker === "IBKR_PAPER" ? "IBKR Paper (algo clone)" : a.broker,
              currency: a.currency,
              total: a.netLiquidation,
              available: a.totalCash,
              invested: a.netLiquidation != null && a.totalCash != null
                ? a.netLiquidation - a.totalCash : null,
              openPnl: a.unrealisedPnl,
              status: "ok" as const,
            } as Awaited<ReturnType<typeof api.cashSummary>>["brokers"][number]));
          setRows([...cash.brokers, ...clones]);
          setErr(null);
        })
        .catch((e) => { if (live) setErr(e instanceof Error ? e.message : String(e)); })
        .finally(() => { if (live) setLoading(false); });
    };
    void load();
    const t = setInterval(load, 60_000);
    return () => { live = false; clearInterval(t); };
  }, []);

  if (loading && rows.length === 0) return <Note>Loading…</Note>;
  if (err) return <Note tone="down">Unavailable: {err}</Note>;
  const shown = rows.filter((b) => b.status !== "disabled");
  if (shown.length === 0) return <Note>No connected brokers.</Note>;

  return (
    <div style={{ overflowX: "auto", maxWidth: "100%" }}>
      <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 12, minWidth: 600 }}>
        <thead>
          <tr style={{ color: "var(--text-dim)", borderBottom: "1px solid #1b2233" }}>
            <th style={TH}>Broker</th>
            <th style={TH}>Ccy</th>
            <th style={TH_R}>Net Liquidity</th>
            <th style={TH_R}>Available</th>
            <th style={TH_R}>Invested</th>
            <th style={TH_R}>Open P&L</th>
            <th style={TH}>Status</th>
          </tr>
        </thead>
        <tbody>
          {shown.map((b) => (
            <tr key={b.broker} style={{ borderBottom: "1px solid #141b2b" }}>
              <td style={{ ...TD, fontWeight: 700, fontFamily: undefined }}>{b.label || b.broker}</td>
              <td style={TD}>{b.currency ?? "—"}</td>
              <td style={TD_R}>{fmtMoney(b.total ?? b.balance ?? null, b.currency)}</td>
              <td style={TD_R}>{fmtMoney(b.available ?? b.free ?? null, b.currency)}</td>
              <td style={TD_R}>{fmtMoney(b.invested ?? null, b.currency)}</td>
              <td style={{ ...TD_R, color: signColour(b.openPnl ?? null) }}>{fmtMoney(b.openPnl ?? null, b.currency, true)}</td>
              <td style={TD}>{b.status}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/** Symbol cell that opens the Quote/Chart view when the symbol has an honest
 * chart symbol (equity). FX/options/futures and rows with no callback render
 * as plain, non-clickable text (don't open an empty chart). */
function SymbolCell({ raw, onOpenSymbol }: { raw: string; onOpenSymbol?: (symbol: string) => void }) {
  const chart = orderChartSymbol(raw);
  const clickable = !!onOpenSymbol && !!chart;
  if (!clickable) return <>{prettySymbol(raw)}</>;
  return (
    <button
      type="button"
      onClick={() => onOpenSymbol!(chart!)}
      title={`Open ${chart} chart`}
      style={{
        background: "transparent", border: "none", padding: 0, cursor: "pointer",
        font: "inherit", color: "var(--text)", fontWeight: 700, textAlign: "left",
        textDecoration: "underline", textDecorationColor: "rgba(79,140,255,0.35)",
        textUnderlineOffset: 2,
      }}
    >
      {prettySymbol(raw)}
    </button>
  );
}

/** Strategy id cell. OMS orders carry the originating strategy in
 * OmsOrderRow.strategyId (null for ad-hoc/manual orders). Compact: truncate
 * long ids with an ellipsis and expose the full value via the title tooltip;
 * render "—" when the row has no strategy. */
function StrategyCell({ id }: { id: string | null }) {
  if (!id) return <span style={{ color: "var(--text-dim)" }}>—</span>;
  return (
    <span
      title={id}
      style={{
        display: "inline-block", maxWidth: 140, overflow: "hidden",
        textOverflow: "ellipsis", whiteSpace: "nowrap", verticalAlign: "bottom",
      }}
    >
      {id}
    </span>
  );
}

function Note({ children, tone }: { children: React.ReactNode; tone?: "down" }) {
  return (
    <div style={{ fontSize: 12, padding: 12, color: tone === "down" ? "var(--down, #ef4444)" : "var(--text-muted)" }}>
      {children}
    </div>
  );
}

const TH: React.CSSProperties = { textAlign: "left", padding: "6px 10px", fontWeight: 600, fontSize: 11 };
const TH_R: React.CSSProperties = { ...TH, textAlign: "right" };
const TD: React.CSSProperties = { padding: "7px 10px", fontFamily: "monospace" };
const TD_R: React.CSSProperties = { ...TD, textAlign: "right" };

/**
 * DeskTabs — the tabbed work-area: Positions · Orders · Trades · Balances.
 * Underline-active, blue (IBKR style). Positions is the centerpiece (its own
 * component); the others wire to existing endpoints with simple dense tables:
 *   - Orders   → api.omsOrders() (live OMS orders, all states)
 *   - Trades   → api.omsOrders() filtered to terminal FILLED orders (the
 *                fills we actually have; no separate trades endpoint exists)
 *   - Balances → per-broker cash from api.cashSummary(), native currency
 *
 * Tabs are pills (the user dislikes dropdowns on primary surfaces), so state
 * stays visible. Tables share the same overflow-x:auto + min-width treatment
 * as PositionsTable so they scroll within the card on a phone.
 */
import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api, type OmsOrderRow } from "../../api/client";
import { prettySymbol, bareSymbol, productOf } from "../../util/brokerSymbols";
import { fmtWhenDate } from "../../util/time";
import { fmtQty } from "../../util/numbers";
import { PositionsTable } from "./PositionsTable";
import { fmtMoney, fmtNum, signColour } from "./deskFormat";

type Tab = "positions" | "orders" | "trades" | "balances";

const TABS: { key: Tab; label: string }[] = [
  { key: "positions", label: "Positions" },
  { key: "orders", label: "Orders" },
  { key: "trades", label: "Trades" },
  { key: "balances", label: "Balances" },
];

export function DeskTabs({ onOpenSymbol }: { onOpenSymbol?: (symbol: string) => void }) {
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
        {tab === "positions" && <PositionsTable onOpenSymbol={onOpenSymbol} />}
        {tab === "orders" && <OrdersTab onOpenSymbol={onOpenSymbol} />}
        {tab === "trades" && <TradesTab onOpenSymbol={onOpenSymbol} />}
        {tab === "balances" && <BalancesTab />}
      </div>
    </div>
  );
}

/** Orders — all live OMS orders. */
function OrdersTab({ onOpenSymbol }: { onOpenSymbol?: (symbol: string) => void }) {
  const orders = useOrders();
  return <OrderTable rows={orders.rows} loading={orders.loading} err={orders.err} empty="No orders." onOpenSymbol={onOpenSymbol} />;
}

/** Trades — terminal FILLED orders (the executed fills we can show). */
function TradesTab({ onOpenSymbol }: { onOpenSymbol?: (symbol: string) => void }) {
  const orders = useOrders();
  const filled = orders.rows.filter((o) => o.state === "FILLED" || o.state === "PARTIALLY_FILLED");
  return <OrderTable rows={filled} loading={orders.loading} err={orders.err} empty="No fills yet." showFill onOpenSymbol={onOpenSymbol} />;
}

/** Chart symbol for an OMS order row, or null when there's no honest Yahoo
 * candle symbol (IG FX/CFD epics, options, futures). Equity tickers reduce to
 * the bare symbol which Yahoo accepts. */
function orderChartSymbol(raw: string): string | null {
  return productOf(raw) === "Equity" ? bareSymbol(raw) : null;
}

function useOrders() {
  const [rows, setRows] = useState<OmsOrderRow[]>([]);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => {
    let live = true;
    const load = () => {
      api.omsOrders(undefined, 100)
        .then((r) => { if (live) { setRows(r.orders); setErr(null); } })
        .catch((e) => { if (live) setErr(e instanceof Error ? e.message : String(e)); })
        .finally(() => { if (live) setLoading(false); });
    };
    void load();
    const t = setInterval(load, 60_000);
    return () => { live = false; clearInterval(t); };
  }, []);
  return { rows, loading, err };
}

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
      <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 12, minWidth: 640 }}>
        <thead>
          <tr style={{ color: "var(--text-dim)", borderBottom: "1px solid #1b2233" }}>
            <th style={TH}>Date / Time</th>
            <th style={TH}>Symbol</th>
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
      api.cashSummary()
        .then((r) => { if (live) { setRows(r.brokers); setErr(null); } })
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

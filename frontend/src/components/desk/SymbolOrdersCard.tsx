/**
 * SymbolOrdersCard — orders for ONE symbol in the right rail.
 *
 * One job: filter omsOrders by symbol (bare-ticker match) and display a
 * compact table: date·time · side · qty · state · strategy.
 *
 * Receives pre-fetched orders from the parent so the rail doesn't duplicate
 * the orders fetch; just filters and renders.
 *
 * Shows "No orders" when none match. Limits display to most recent 20.
 */
import { fmtWhenDate } from "../../util/time";
import { fmtQty } from "../../util/numbers";
import { bareSymbol } from "../../util/brokerSymbols";
import type { OmsOrderRow } from "../../api/client";

const MAX_ROWS = 20;

export function SymbolOrdersCard({
  symbol,
  strategy,
  orders,
  loading,
}: {
  symbol: string;
  /** Scope to one strategy so the same ticker under another strategy (e.g. the
   * IBKR clone) doesn't show here. */
  strategy?: string | null;
  orders: OmsOrderRow[];
  loading: boolean;
}) {
  const bare = bareSymbol(symbol).toUpperCase();
  const filtered = orders
    .filter(
      (o) =>
        bareSymbol(o.symbol).toUpperCase() === bare &&
        (!strategy || o.strategyId === strategy),
    )
    .slice(0, MAX_ROWS);

  return (
    <div
      style={{
        border: "1px solid #1b2233",
        borderRadius: 6,
        background: "rgba(255,255,255,0.015)",
        padding: "10px 12px",
        minWidth: 0,
      }}
    >
      <div
        style={{
          fontSize: 10,
          color: "var(--text-muted)",
          textTransform: "uppercase",
          letterSpacing: "0.05em",
          marginBottom: 8,
          fontWeight: 600,
        }}
      >
        Orders ({loading ? "…" : filtered.length})
      </div>

      {loading ? (
        <div style={{ fontSize: 11, color: "var(--text-muted)" }}>Loading orders…</div>
      ) : filtered.length === 0 ? (
        <div style={{ fontSize: 11, color: "var(--text-muted)", fontStyle: "italic" }}>
          No OMS orders for {symbol}.
        </div>
      ) : (
        <div style={{ overflowX: "auto" }}>
          <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 11, minWidth: 320 }}>
            <thead>
              <tr style={{ color: "var(--text-dim)", borderBottom: "1px solid #1b2233" }}>
                <th style={TH}>Date / Time</th>
                <th style={TH}>Side</th>
                <th style={TH_R}>Qty</th>
                <th style={TH_R}>Price</th>
                <th style={TH}>State</th>
                <th style={TH}>Strategy</th>
              </tr>
            </thead>
            <tbody>
              {filtered.map((o) => (
                <tr key={o.id} style={{ borderBottom: "1px solid #141b2b" }}>
                  <td style={{ ...TD, color: "var(--text-muted)" }}>
                    {fmtWhenDate(o.createdAtUtc || o.lastStateChangeAtUtc)}
                  </td>
                  <td
                    style={{
                      ...TD,
                      color: o.side === "BUY" ? "#1fc16b" : "#ef4444",
                      fontWeight: 700,
                    }}
                  >
                    {o.side}
                  </td>
                  <td style={TD_R}>{fmtQty(o.filledQty || o.qty)}</td>
                  <td style={TD_R}>
                    {o.avgFillPrice != null
                      ? o.avgFillPrice.toFixed(2)
                      : o.limitPrice != null
                        ? <span style={{ color: "var(--text-muted)" }}>{o.limitPrice.toFixed(2)}</span>
                        : "—"}
                  </td>
                  <td style={{ ...TD, fontSize: 10 }}>{o.state}</td>
                  <td style={{ ...TD, color: "var(--text-muted)", maxWidth: 120, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                    {o.strategyId ?? "—"}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {filtered.length === MAX_ROWS && (
        <div style={{ fontSize: 9, color: "var(--text-dim)", marginTop: 4 }}>
          Showing most recent {MAX_ROWS} · go to OMS for full history
        </div>
      )}
    </div>
  );
}

const TH:   React.CSSProperties = { textAlign: "left",  padding: "5px 8px", fontWeight: 600 };
const TH_R: React.CSSProperties = { ...TH, textAlign: "right" };
const TD:   React.CSSProperties = { padding: "5px 8px", fontFamily: "monospace" };
const TD_R: React.CSSProperties = { ...TD, textAlign: "right" };

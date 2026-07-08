/**
 * SymbolValidationCard — per-symbol TRADE VALIDATION for a testing/validation
 * workflow. Turns the flat OMS order list into ROUND-TRIP EPISODES so you can
 * answer, for one symbol:
 *
 *   - When did we first trade it?
 *   - How many round-trips, and did we enter MULTIPLE times in one episode?
 *   - Entry avg vs exit avg, holding period, realised P&L per round-trip.
 *   - Which are still OPEN.
 *
 * Everything here is FACTUAL — derived only from FILLED OMS orders (broker
 * truth). No signal is re-computed (that would risk drifting from the strategy);
 * the "did we enter at the right point vs the cloud" check is done VISUALLY on
 * the CandleIchimokuChart above, which now draws the cloud at the strategy's own
 * 5/32/50 params with the actual fill markers.
 *
 * Receives the pre-fetched orders from the rail (no extra fetch).
 */
import { bareSymbol } from "../../util/brokerSymbols";
import { fmtWhenDate } from "../../util/time";
import type { OmsOrderRow } from "../../api/client";

type Episode = {
  entryDate: string;
  exitDate: string | null;
  buys: number;
  sells: number;
  qtyIn: number;
  costIn: number;
  qtyOut: number;
  proceedsOut: number;
  strategy: string | null;
  open: boolean;
};

/** Group a symbol's FILLED orders (chronological) into round-trip episodes:
 * an episode opens on the first BUY from flat and closes when the running
 * position returns to (or below) zero. Adds within the episode = multiple
 * entries. Pure/factual — walks fills only. */
function buildEpisodes(orders: OmsOrderRow[]): Episode[] {
  const fills = orders
    .filter((o) => (o.state === "FILLED" || o.state === "PARTIALLY_FILLED") && (o.filledQty || 0) > 0)
    .slice()
    .sort((a, b) => +new Date(a.createdAtUtc) - +new Date(b.createdAtUtc));

  const episodes: Episode[] = [];
  let pos = 0;
  let ep: Episode | null = null;

  for (const o of fills) {
    const q = o.filledQty || o.qty;
    const px = o.avgFillPrice ?? o.limitPrice ?? 0;
    if (o.side === "BUY") {
      if (!ep) {
        ep = {
          entryDate: o.createdAtUtc,
          exitDate: null,
          buys: 0,
          sells: 0,
          qtyIn: 0,
          costIn: 0,
          qtyOut: 0,
          proceedsOut: 0,
          strategy: o.strategyId ?? null,
          open: true,
        };
      }
      ep.buys++;
      ep.qtyIn += q;
      ep.costIn += q * px;
      pos += q;
    } else {
      // SELL
      if (ep) {
        ep.sells++;
        ep.qtyOut += q;
        ep.proceedsOut += q * px;
      }
      pos -= q;
      if (ep && pos <= 1e-9) {
        ep.exitDate = o.createdAtUtc;
        ep.open = false;
        episodes.push(ep);
        ep = null;
        pos = 0;
      }
    }
  }
  if (ep) episodes.push(ep); // still open
  return episodes.reverse(); // newest first
}

function daysBetween(a: string, b: string | null): number {
  const end = b ? +new Date(b) : Date.now();
  return Math.max(0, Math.round((end - +new Date(a)) / (24 * 3600 * 1000)));
}

export function SymbolValidationCard({
  symbol,
  strategy,
  orders,
  loading,
}: {
  symbol: string;
  /** Scope to one strategy so another strategy's trades on the same ticker
   * (e.g. the IBKR clone) don't pollute this strategy's round-trips. */
  strategy?: string | null;
  orders: OmsOrderRow[];
  loading: boolean;
}) {
  const bare = bareSymbol(symbol).toUpperCase();
  const mine = orders.filter(
    (o) =>
      bareSymbol(o.symbol).toUpperCase() === bare &&
      (!strategy || o.strategyId === strategy),
  );
  const episodes = buildEpisodes(mine);

  const firstTraded = episodes.length ? episodes[episodes.length - 1].entryDate : null;
  const closed = episodes.filter((e) => !e.open);
  const openCount = episodes.length - closed.length;

  return (
    <div style={CARD}>
      <div style={HEADER}>Validation — round-trips</div>

      {loading ? (
        <div style={{ fontSize: 11, color: "var(--text-muted)" }}>Loading…</div>
      ) : episodes.length === 0 ? (
        <div style={{ fontSize: 11, color: "var(--text-muted)", fontStyle: "italic" }}>
          No filled trades for {symbol} yet.
        </div>
      ) : (
        <>
          <div style={{ display: "flex", gap: 14, flexWrap: "wrap", marginBottom: 8, fontSize: 11 }}>
            <span>
              <span style={LBL}>First traded</span> {firstTraded ? fmtWhenDate(firstTraded).slice(0, 11) : "—"}
            </span>
            <span>
              <span style={LBL}>Round-trips</span> {closed.length}
              {openCount > 0 && <span style={{ color: "#e0b341" }}> · {openCount} open</span>}
            </span>
          </div>

          <div style={{ overflowX: "auto" }}>
            <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 11, minWidth: 360 }}>
              <thead>
                <tr style={{ color: "var(--text-dim)", borderBottom: "1px solid #1b2233" }}>
                  <th style={TH}>Entry</th>
                  <th style={TH_R}>Entries</th>
                  <th style={TH_R}>Avg in</th>
                  <th style={TH}>Exit</th>
                  <th style={TH_R}>Avg out</th>
                  <th style={TH_R}>Held</th>
                  <th style={TH_R}>P&amp;L</th>
                </tr>
              </thead>
              <tbody>
                {episodes.map((e, i) => {
                  const avgIn = e.qtyIn > 0 ? e.costIn / e.qtyIn : null;
                  const avgOut = e.qtyOut > 0 ? e.proceedsOut / e.qtyOut : null;
                  // Realised on the closed portion: proceeds − avg-entry × qtyOut.
                  const pnl = avgIn != null && e.qtyOut > 0 ? e.proceedsOut - avgIn * e.qtyOut : null;
                  const held = daysBetween(e.entryDate, e.exitDate);
                  return (
                    <tr key={i} style={{ borderBottom: "1px solid #141b2b" }}>
                      <td style={{ ...TD, color: "var(--text-muted)" }}>
                        {fmtWhenDate(e.entryDate).slice(0, 11)}
                      </td>
                      <td style={TD_R}>
                        {e.buys}
                        {e.buys > 1 && <span title="entered multiple times" style={{ color: "#e0b341" }}> ⤴</span>}
                      </td>
                      <td style={TD_R}>{avgIn != null ? avgIn.toFixed(2) : "—"}</td>
                      <td style={{ ...TD, color: "var(--text-muted)" }}>
                        {e.open ? <span style={{ color: "#e0b341" }}>OPEN</span> : fmtWhenDate(e.exitDate!).slice(0, 11)}
                      </td>
                      <td style={TD_R}>{avgOut != null ? avgOut.toFixed(2) : "—"}</td>
                      <td style={TD_R}>{held}d</td>
                      <td
                        style={{
                          ...TD_R,
                          color: pnl == null ? "var(--text-muted)" : pnl >= 0 ? "#1fc16b" : "#ef4444",
                          fontWeight: 700,
                        }}
                      >
                        {pnl != null ? (pnl >= 0 ? "+" : "") + pnl.toFixed(0) : "—"}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>

          <div style={{ fontSize: 9, color: "var(--text-dim)", marginTop: 6, lineHeight: 1.4 }}>
            ⤴ = multiple entries in one episode. P&amp;L is realised on the closed
            quantity (broker fills only). Entry quality vs the cloud is on the chart
            above (5·32·50, actual fill markers).
          </div>
        </>
      )}
    </div>
  );
}

const CARD: React.CSSProperties = {
  border: "1px solid #1b2233",
  borderRadius: 6,
  background: "rgba(255,255,255,0.015)",
  padding: "10px 12px",
  minWidth: 0,
};
const HEADER: React.CSSProperties = {
  fontSize: 10,
  color: "var(--text-muted)",
  textTransform: "uppercase",
  letterSpacing: "0.05em",
  marginBottom: 8,
  fontWeight: 600,
};
const LBL: React.CSSProperties = { color: "var(--text-dim)", marginRight: 4 };
const TH: React.CSSProperties = { textAlign: "left", padding: "5px 8px", fontWeight: 600 };
const TH_R: React.CSSProperties = { ...TH, textAlign: "right" };
const TD: React.CSSProperties = { padding: "5px 8px", fontFamily: "monospace" };
const TD_R: React.CSSProperties = { ...TD, textAlign: "right" };

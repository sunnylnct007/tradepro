/**
 * SymbolDetailRail — right-rail detail panel for a selected symbol.
 *
 * Composes four single-responsibility cards stacked vertically:
 *   1. SymbolChartCard   — CandleIchimokuChart + timeframe pills
 *   2. SymbolPositionCard — shares·avg·mkt value·unrealized P&L
 *   3. SymbolDecisionCard — WHY/trend from compareLatest
 *   4. SymbolOrdersCard  — OMS orders for this symbol
 *
 * Plus a header strip: symbol label + close/back button.
 *
 * Orders are fetched here (single call for all orders; the card filters).
 * Positions are passed in from the parent (already loaded by PositionsTable).
 *
 * Props:
 *   symbol     — Yahoo-resolvable chart symbol
 *   onClose    — called when the user hits the back/close button
 *   positions  — current merged broker positions (for SymbolPositionCard)
 */
import { useEffect, useRef, useState } from "react";
import { api } from "../../api/client";
import type { OmsOrderRow } from "../../api/client";
import { SymbolChartCard } from "./SymbolChartCard";
import { SymbolPositionCard, type PositionRow } from "./SymbolPositionCard";
import { SymbolDecisionCard } from "./SymbolDecisionCard";
import { SymbolOrdersCard } from "./SymbolOrdersCard";

const ORDER_LIMIT = 100;

export function SymbolDetailRail({
  symbol,
  onClose,
  positions,
}: {
  symbol: string;
  onClose: () => void;
  positions: PositionRow[];
}) {
  const [orders, setOrders]     = useState<OmsOrderRow[]>([]);
  const [ordersLoading, setOL] = useState(true);

  // Ref used to scroll the rail into view when a symbol is selected.
  const railRef = useRef<HTMLElement>(null);

  // Fetch orders once on mount (symbol change resets the component via key).
  useEffect(() => {
    let live = true;
    api
      .omsOrders(undefined, ORDER_LIMIT)
      .then((r) => { if (live) { setOrders(r.orders); setOL(false); } })
      .catch(() => { if (live) setOL(false); });
    return () => { live = false; };
  }, []);

  // Scroll the rail into view when the component mounts (i.e. when a symbol is
  // selected). This handles the case where the user clicks a row that is low on
  // the page and the rail (which is in a sticky right column) would otherwise
  // be above the current scroll position or off-screen on mobile.
  useEffect(() => {
    railRef.current?.scrollIntoView({ behavior: "smooth", block: "start" });
  }, []);

  return (
    <aside
      ref={railRef}
      style={{
        border: "1px solid #1b2233",
        borderRadius: 8,
        background: "rgba(255,255,255,0.015)",
        padding: 10,
        display: "flex",
        flexDirection: "column",
        gap: 10,
        minWidth: 280,
        // Allow the user to drag the right edge to resize the rail width.
        resize: "horizontal",
        overflow: "auto",
        maxHeight: "calc(100vh - 120px)",
        // Stick to the top of the scrolling work-area so the chart stays
        // visible even when the positions list is long. `top: 0` works because
        // DeskShell's <main> is the scroll container (overflow:auto), not the
        // browser viewport.
        position: "sticky",
        top: 0,
      }}
    >
      {/* Header: symbol + close button */}
      <div
        style={{
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          gap: 8,
        }}
      >
        <div style={{ fontFamily: "monospace", fontWeight: 800, fontSize: 15, color: "var(--text)" }}>
          {symbol}
        </div>
        <button
          onClick={onClose}
          title="Back to account overview"
          style={{
            background: "transparent",
            border: "1px solid #1b2233",
            borderRadius: 4,
            color: "var(--text-muted)",
            cursor: "pointer",
            fontSize: 11,
            padding: "2px 8px",
          }}
        >
          ← Back
        </button>
      </div>

      {/* 1. Chart — 400px tall so candles + axis labels are readable in the
              wider rail. The rail itself has resize:horizontal so the trader
              can drag the right edge for even more room. */}
      <SymbolChartCard symbol={symbol} height={400} />

      {/* 2. Position */}
      <SymbolPositionCard symbol={symbol} positions={positions} />

      {/* 3. Signal / Why held */}
      <SymbolDecisionCard symbol={symbol} />

      {/* 4. Orders */}
      <SymbolOrdersCard symbol={symbol} orders={orders} loading={ordersLoading} />
    </aside>
  );
}

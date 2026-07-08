/**
 * SymbolDetailModal — the "pop out" of the symbol detail rail into a large,
 * comfortable overlay. Same content as SymbolDetailRail but laid out in TWO
 * columns (big chart on the left, cards on the right) so you can validate a
 * symbol without the cramped, scroll-heavy narrow rail.
 *
 * Reuses the rail's already-fetched orders/positions (passed in) — no extra
 * fetch. Backdrop click or Esc closes. Strategy-scoped like the rail.
 */
import { useEffect } from "react";
import { SymbolChartCard } from "./SymbolChartCard";
import { SymbolPositionCard, type PositionRow } from "./SymbolPositionCard";
import { SymbolDecisionCard } from "./SymbolDecisionCard";
import { SymbolRangeRiskCard } from "./SymbolRangeRiskCard";
import { SymbolValidationCard } from "./SymbolValidationCard";
import { SymbolOrdersCard } from "./SymbolOrdersCard";
import type { OmsOrderRow } from "../../api/client";

type Fill = { side: "BUY" | "SELL"; price: number | null; atUtc: string };

export function SymbolDetailModal({
  symbol,
  strategy,
  positions,
  orders,
  ordersLoading,
  entryPrice,
  entryDate,
  fills,
  onClose,
}: {
  symbol: string;
  strategy?: string | null;
  positions: PositionRow[];
  orders: OmsOrderRow[];
  ordersLoading: boolean;
  entryPrice?: number | null;
  entryDate?: string | null;
  fills: Fill[];
  onClose: () => void;
}) {
  // Esc to close.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  return (
    <div
      onClick={onClose}
      style={{
        position: "fixed",
        inset: 0,
        background: "rgba(3,6,12,0.72)",
        zIndex: 1000,
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        padding: "2.5vh 2vw",
      }}
    >
      <div
        onClick={(e) => e.stopPropagation()}
        style={{
          width: "92vw",
          height: "90vh",
          background: "#0d1117",
          border: "1px solid #1b2233",
          borderRadius: 10,
          display: "flex",
          flexDirection: "column",
          overflow: "hidden",
          boxShadow: "0 20px 60px rgba(0,0,0,0.5)",
        }}
      >
        {/* Header */}
        <div
          style={{
            display: "flex",
            alignItems: "center",
            justifyContent: "space-between",
            padding: "10px 14px",
            borderBottom: "1px solid #1b2233",
          }}
        >
          <div style={{ display: "flex", alignItems: "baseline", gap: 10 }}>
            <span style={{ fontFamily: "monospace", fontWeight: 800, fontSize: 17, color: "var(--text)" }}>{symbol}</span>
            {strategy && (
              <span
                style={{ fontSize: 11, fontWeight: 600, color: "var(--accent, #4f8cff)", background: "rgba(79,140,255,0.12)", padding: "1px 7px", borderRadius: 4 }}
              >
                {strategy}
              </span>
            )}
            <span style={{ fontSize: 11, color: "var(--text-dim)" }}>· validation</span>
          </div>
          <button
            onClick={onClose}
            title="Close (Esc)"
            style={{ background: "transparent", border: "1px solid #1b2233", borderRadius: 4, color: "var(--text-muted)", cursor: "pointer", fontSize: 12, padding: "3px 10px" }}
          >
            ✕ Close
          </button>
        </div>

        {/* Two-column body: chart (wide) | cards (scrollable). */}
        <div
          style={{
            display: "grid",
            gridTemplateColumns: "minmax(0, 1.7fr) minmax(340px, 1fr)",
            gap: 12,
            padding: 12,
            flex: 1,
            minHeight: 0,
          }}
        >
          {/* Left: big chart. */}
          <div style={{ minWidth: 0, overflow: "auto" }}>
            <SymbolChartCard symbol={symbol} height={620} entryPrice={entryPrice} entryDate={entryDate} fills={fills} />
          </div>

          {/* Right: cards, scroll independently. */}
          <div style={{ display: "flex", flexDirection: "column", gap: 10, overflow: "auto", minHeight: 0 }}>
            <SymbolPositionCard symbol={symbol} positions={positions} />
            <SymbolDecisionCard symbol={symbol} positions={positions} />
            <SymbolRangeRiskCard symbol={symbol} />
            <SymbolValidationCard symbol={symbol} strategy={strategy} orders={orders} loading={ordersLoading} />
            <SymbolOrdersCard symbol={symbol} strategy={strategy} orders={orders} loading={ordersLoading} />
          </div>
        </div>
      </div>
    </div>
  );
}

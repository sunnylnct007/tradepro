/**
 * Desk — IBKR-Desktop-style northstar cockpit, mounted at /desk.
 *
 * Portfolio layout is a master-detail two-column grid on wide screens:
 *
 *   [work-area]  |  [right rail]
 *
 *   Work-area: AccountSummaryGrid (compact per-broker table) +
 *              DeskTabs (Positions/Orders/Trades/Balances — Positions tab
 *              now renders PositionsByStrategy grouped by strategy desk)
 *
 *   Right rail:
 *     - Nothing selected → DeskRightRail (account-value % chart)
 *     - Symbol selected  → SymbolDetailRail (chart + position + WHY + orders)
 *       The selection is lifted here as `selectedSymbol: string|null`.
 *       Position rows and order rows call `onSelectSymbol(chartSymbol)` which
 *       sets selectedSymbol WITHOUT navigating — /desk is a single composite
 *       cockpit, never a new page.
 *
 * The `DeskTabs` component's Positions tab has been replaced by
 * `PositionsByStrategy` (strategy-grouped positions with per-strategy subtotals).
 * The existing Orders/Trades/Balances tabs are unchanged.
 *
 * Mobile: grid collapses to single column; the rail stacks below the work-area.
 * Detail rail caps at 100vh - 120px with its own overflow:auto.
 *
 * Other views (Quote/Screeners/News/Watchlist) use full width.
 */
import { useCallback, useEffect, useState } from "react";
import { DeskShell, type DeskView } from "../components/desk/DeskShell";
import { AccountSummaryGrid } from "../components/desk/AccountSummaryGrid";
import { DeskTabs } from "../components/desk/DeskTabs";
import { DeskRightRail } from "../components/desk/DeskRightRail";
import { SymbolDetailRail } from "../components/desk/SymbolDetailRail";
import { ScreenersView } from "../components/desk/ScreenersView";
import { NewsView } from "../components/desk/NewsView";
import { WatchlistView } from "../components/desk/WatchlistView";
import { QuoteView } from "../components/desk/QuoteView";
import { SimulationView } from "../components/desk/SimulationView";
import { OmsOrders } from "./OmsOrders";
import { RiskPage } from "./RiskPage";
import type { PositionRow } from "../components/desk/PositionsByStrategy";

const WIDE_BREAKPOINT = 1024;

function useWide(): boolean {
  const [wide, setWide] = useState(
    typeof window !== "undefined" ? window.innerWidth > WIDE_BREAKPOINT : true,
  );
  useEffect(() => {
    const onResize = () => setWide(window.innerWidth > WIDE_BREAKPOINT);
    window.addEventListener("resize", onResize);
    return () => window.removeEventListener("resize", onResize);
  }, []);
  return wide;
}

export function Desk() {
  const wide = useWide();
  const [view, setView] = useState<DeskView>("portfolio");

  // Master-detail: the symbol currently "drilled into" in the right rail.
  // null → account-value chart (DeskRightRail).
  // string → SymbolDetailRail for that Yahoo symbol.
  const [selectedSymbol, setSelectedSymbol] = useState<string | null>(null);

  // Positions from PositionsByStrategy — fed into SymbolPositionCard via the
  // rail so the rail doesn't need its own fetch.
  const [positions, setPositions] = useState<PositionRow[]>([]);

  // When the user clicks a symbol in positions or orders:
  //   - stay on "portfolio" (no view change)
  //   - open the right rail for that symbol
  const onSelectSymbol = useCallback((sym: string) => {
    setSelectedSymbol(sym);
  }, []);

  // Navigating to another view clears the symbol selection so the rail
  // resets to the account-value chart on return.
  const onSelectView = useCallback((v: DeskView) => {
    setView(v);
    if (v !== "portfolio") setSelectedSymbol(null);
  }, []);

  const handleRowsChange = useCallback((rows: PositionRow[]) => {
    setPositions(rows);
  }, []);

  return (
    <DeskShell active={view} onSelect={onSelectView}>
      {view === "portfolio" && (
        <>
          {/* Compact per-broker account summary table */}
          <AccountSummaryGrid />

          {/* Two-column: work-area | right-rail.
              When a symbol is selected the rail widens significantly (min 640px
              or 46vw) so candlestick bars + axis labels are readable. The rail
              also has CSS resize:horizontal so the trader can drag it wider.
              On narrow viewports it stacks below the work-area. */}
          <div
            style={{
              display: "grid",
              gridTemplateColumns: wide
                ? selectedSymbol
                  ? "minmax(0, 1fr) min(640px, 46vw)"
                  : "minmax(0, 1fr) 320px"
                : "1fr",
              gap: 14,
              alignItems: "start",
            }}
          >
            {/* Work area: tabbed Positions/Orders/Trades/Balances */}
            <DeskTabs
              onOpenSymbol={onSelectSymbol}
              onPositionsRowsChange={handleRowsChange}
            />

            {/* Right rail: account chart or symbol detail */}
            {selectedSymbol ? (
              <SymbolDetailRail
                key={selectedSymbol}
                symbol={selectedSymbol}
                onClose={() => setSelectedSymbol(null)}
                positions={positions}
              />
            ) : (
              <DeskRightRail />
            )}
          </div>
        </>
      )}

      {view === "quote" && (
        <QuoteView initialSymbol={selectedSymbol} />
      )}
      {view === "screeners"  && <ScreenersView />}
      {view === "news"       && <NewsView wide={wide} />}
      {view === "watchlist"  && <WatchlistView />}
      {view === "simulation" && <SimulationView />}
      {view === "oms"        && <OmsOrders />}
      {view === "risk"       && <RiskPage />}
    </DeskShell>
  );
}

// Keep legacy export for any import that uses the named export.
export default Desk;

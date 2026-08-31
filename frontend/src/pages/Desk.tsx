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
import { useSearchParams } from "react-router-dom";
import { DeskShell, type DeskView } from "../components/desk/DeskShell";
import { ResearchView } from "../components/desk/ResearchView";
import { SwingView } from "../components/desk/SwingView";
import { PostEarningsPutsView } from "../components/desk/PostEarningsPutsView";
import { MomentumView } from "../components/desk/MomentumView";
import { ScannerView } from "../components/desk/ScannerView";
import { AccountSummaryGrid } from "../components/desk/AccountSummaryGrid";
import { StrategyHealthPanel } from "../components/desk/StrategyHealthPanel";
import { EquityTrackingCard } from "../components/desk/EquityTrackingCard";
import { PnlTruthCard } from "../components/desk/PnlTruthCard";
import { BrokerBookCard } from "../components/desk/BrokerBookCard";
import { SignalAuditCard } from "../components/desk/SignalAuditCard";
import { DeskKpiStrip } from "../components/desk/DeskKpiStrip";
import { RunLogCard } from "../components/desk/RunLogCard";
import { FillReplayCard } from "../components/desk/FillReplayCard";
import { TodaySetupsCard } from "../components/desk/TodaySetupsCard";
import { DeskTabs } from "../components/desk/DeskTabs";
import { DeskRightRail } from "../components/desk/DeskRightRail";
import { SymbolDetailRail } from "../components/desk/SymbolDetailRail";
import { ScreenersView } from "../components/desk/ScreenersView";
import { NewsView } from "../components/desk/NewsView";
import { HarvestView } from "../components/desk/HarvestView";
import { OptionsDesk } from "../components/desk/OptionsDesk";
import { StrangleDecisionsView } from "../components/desk/StrangleDecisionsView";
import { WatchlistView } from "../components/desk/WatchlistView";
import { QuoteView } from "../components/desk/QuoteView";
import { SimulationView } from "../components/desk/SimulationView";
import { OmsOrders } from "./OmsOrders";
import { RiskPage } from "./RiskPage";
import { Compare } from "./Compare";
import { UniverseScan } from "./UniverseScan";
import { DailyScreener } from "./DailyScreener";
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

const DESK_VIEWS: DeskView[] = ["portfolio", "decide", "scan", "screeners", "news", "watchlist", "quote", "simulation", "oms", "risk", "harvest", "options", "daily-screener", "research", "swing", "post-earnings-puts", "strangle-decisions"];

export function Desk() {
  const wide = useWide();
  // Deep-link support: /desk?view=oms lands directly on that cockpit view, so the
  // legacy routes (/oms, /risk, /scan, /compare) can redirect INTO the cockpit
  // instead of rendering the old Layout shell (consistent admin theme everywhere).
  const [searchParams] = useSearchParams();
  const paramView = searchParams.get("view") as DeskView | null;
  const initialView: DeskView = paramView && DESK_VIEWS.includes(paramView) ? paramView : "portfolio";
  const [view, setView] = useState<DeskView>(initialView);

  // Master-detail: the symbol currently "drilled into" in the right rail.
  // null → account-value chart (DeskRightRail).
  // string → SymbolDetailRail for that Yahoo symbol.
  const [selectedSymbol, setSelectedSymbol] = useState<string | null>(null);
  // The STRATEGY the symbol was clicked under (from the strategy-grouped row) so
  // the detail rail shows only THAT strategy's orders/fills — not another
  // strategy that happens to hold the same ticker (e.g. the IBKR clone bleeding
  // into ichimoku_equity's validation). null = show all strategies.
  const [selectedStrategy, setSelectedStrategy] = useState<string | null>(null);

  // Positions from PositionsByStrategy — fed into SymbolPositionCard via the
  // rail so the rail doesn't need its own fetch.
  const [positions, setPositions] = useState<PositionRow[]>([]);

  // When the user clicks a symbol in positions or orders:
  //   - stay on "portfolio" (no view change)
  //   - open the right rail for that symbol, scoped to the clicked strategy
  const onSelectSymbol = useCallback((sym: string, strategy?: string | null) => {
    setSelectedSymbol(sym);
    setSelectedStrategy(strategy ?? null);
  }, []);

  // Navigating to another view clears the symbol selection so the rail
  // resets to the account-value chart on return.
  const onSelectView = useCallback((v: DeskView) => {
    setView(v);
    if (v !== "portfolio") setSelectedSymbol(null);
  }, []);

  // Global top-bar search (SymbolSearch, ⌘K) picking a result. Search can
  // fire from any view, so this switches to "portfolio" (the only view with
  // a mounted SymbolDetailRail) THEN opens the rail — the reverse order of
  // onSelectView's clear-on-leave, so it doesn't get wiped. This is the fix
  // for search dropping the user into the old legacy <Layout> shell via
  // /symbol/:ticker: it never navigates away from /desk at all now.
  const onSearchSelectSymbol = useCallback((sym: string) => {
    setView("portfolio");
    setSelectedSymbol(sym);
    setSelectedStrategy(null);
  }, []);

  const handleRowsChange = useCallback((rows: PositionRow[]) => {
    setPositions(rows);
  }, []);

  return (
    <DeskShell active={view} onSelect={onSelectView} onSelectSymbol={onSearchSelectSymbol}>
      {view === "portfolio" && (
        <>
          {/* Compact-UX pass: always-visible KPI strip + a per-strategy health bar
              full-width, then the detail panels in a MASONRY (CSS multi-column)
              layout. Masonry (not a grid) because the panels have very different
              heights — a grid aligns rows to the tallest card and leaves big gaps
              under the short ones; columns pack cards top-to-bottom with no gaps. */}
          <DeskKpiStrip />
          <StrategyHealthPanel />

          <div style={{ columnCount: wide ? 3 : 1, columnGap: 12 }}>
            {[
              <PnlTruthCard key="pnl" />,
              <SignalAuditCard key="sig" />,
              <BrokerBookCard key="brk" />,
              <EquityTrackingCard key="eq" />,
              <FillReplayCard key="fill" />,
              // Compact indicator only — the full run-log stream lives on the
              // Data tab (owner, 22 Aug 2026: "dashboard should be an
              // indicator; logs should be a separate tab").
              <RunLogCard key="runlog" compact />,
              <TodaySetupsCard key="setups" />,
            ].map((card) => (
              <div key={card.key} style={{ breakInside: "avoid", marginBottom: 12 }}>
                {card}
              </div>
            ))}
          </div>

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
                  ? "minmax(0, 1fr) min(820px, 52vw)"
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
                strategy={selectedStrategy}
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
      {view === "harvest"    && <HarvestView />}
      {view === "research"   && <ResearchView />}
      {view === "swing"      && <SwingView />}
      {view === "post-earnings-puts" && <PostEarningsPutsView />}
      {view === "momentum"   && <MomentumView />}
      {view === "scanner"    && <ScannerView />}
      {view === "options"    && <OptionsDesk />}
      {view === "strangle-decisions" && <StrangleDecisionsView />}
      {view === "decide"         && <Compare />}
      {view === "scan"           && <UniverseScan />}
      {view === "daily-screener" && <DailyScreener />}
    </DeskShell>
  );
}

// Keep legacy export for any import that uses the named export.
export default Desk;

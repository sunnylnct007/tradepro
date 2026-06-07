/**
 * Desk — the new IBKR-Desktop-style northstar cockpit, mounted at /desk.
 *
 * Standalone from the legacy /trader cockpit and the app-wide <Layout> nav:
 * it brings its own three-zone shell (DeskShell) so we can switch the default
 * to it later without disturbing anything that exists today.
 *
 * The left icon-nav switches the work-area between IN-PAGE views (state here,
 * not routes — /desk is one composite cockpit):
 *   - Portfolio  → DeskKpiRow + DeskTabs (Positions/Orders/Trades/Balances)
 *                  with DeskRightRail (account-value chart) on wide screens
 *   - Screeners  → ScreenersView (compareLatest, sortable IBKR-style table)
 *   - News       → NewsView (market context · earnings · headlines · catalysts)
 *   - Watchlist  → WatchlistView (curated UK list)
 *
 * Layout (Portfolio): a 2-column grid [work-area | right-rail] on wide screens
 * that collapses to a single column (rail stacked BELOW) under WIDE_BREAKPOINT
 * — a `minmax(0, …)` first column lets the dense tables shrink/scroll instead
 * of forcing page-level horizontal overflow. The other views use the full
 * width (no right rail).
 */
import { useEffect, useState } from "react";
import { DeskShell, type DeskView } from "../components/desk/DeskShell";
import { DeskKpiRow } from "../components/desk/DeskKpiRow";
import { DeskTabs } from "../components/desk/DeskTabs";
import { DeskRightRail } from "../components/desk/DeskRightRail";
import { ScreenersView } from "../components/desk/ScreenersView";
import { NewsView } from "../components/desk/NewsView";
import { WatchlistView } from "../components/desk/WatchlistView";

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
  return (
    <DeskShell active={view} onSelect={setView}>
      {view === "portfolio" && (
        <>
          <DeskKpiRow />
          <div
            style={{
              display: "grid",
              gridTemplateColumns: wide ? "minmax(0, 1fr) 320px" : "1fr",
              gap: 14,
              alignItems: "start",
            }}
          >
            <DeskTabs />
            <DeskRightRail />
          </div>
        </>
      )}
      {view === "screeners" && <ScreenersView />}
      {view === "news" && <NewsView wide={wide} />}
      {view === "watchlist" && <WatchlistView />}
    </DeskShell>
  );
}

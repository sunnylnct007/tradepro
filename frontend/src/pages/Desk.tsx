/**
 * Desk — IBKR-Desktop-style northstar cockpit, mounted at /desk.
 *
 * Portfolio layout is a master-detail two-column grid on wide screens:
 *
 *   [work-area]  |  [right rail]
 *
 *   Work-area: paper ledger + broker book (money and holdings, both
 *              broker-confirmed) + DeskTabs (Orders/Trades/Balances — records,
 *              not money; the OMS-computed Positions tab was cut 10 Nov 2026)
 *
 *   Right rail:
 *     - Nothing selected → DeskRightRail (account-value % chart)
 *     - Symbol selected  → SymbolDetailRail (chart + position + WHY + orders)
 *       The selection is lifted here as `selectedSymbol: string|null`.
 *       Position rows and order rows call `onSelectSymbol(chartSymbol)` which
 *       sets selectedSymbol WITHOUT navigating — /desk is a single composite
 *       cockpit, never a new page.
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
import { CandidatesView } from "../components/desk/CandidatesView";
import { DeskHealthBanner } from "../components/desk/DeskHealthBanner";
import { SwingView } from "../components/desk/SwingView";
import { PostEarningsPutsView } from "../components/desk/PostEarningsPutsView";
import { MomentumView } from "../components/desk/MomentumView";
import { PaperLedgerCard } from "../components/desk/PaperLedgerCard";
import { BrokerBookCard } from "../components/desk/BrokerBookCard";
import { ActionBoard } from "../components/desk/ActionBoard";
import { LiveView } from "../components/desk/LiveView";
import { DeskKpiStrip } from "../components/desk/DeskKpiStrip";
import { SignalBoard } from "../components/desk/SignalBoard";
import { RunLogCard } from "../components/desk/RunLogCard";
import { DeskTabs } from "../components/desk/DeskTabs";
import { DeskRightRail } from "../components/desk/DeskRightRail";
import { SymbolDetailRail } from "../components/desk/SymbolDetailRail";
import { HarvestView } from "../components/desk/HarvestView";
import { OptionsDesk } from "../components/desk/OptionsDesk";
import { StrangleDecisionsView } from "../components/desk/StrangleDecisionsView";
import { OmsOrders } from "./OmsOrders";
import { Universes } from "./Universes";
import { Settings } from "./Settings";
import type { PositionRow } from "../components/desk/SymbolPositionCard";
import { api } from "../api/client";
import { accountMode } from "../components/desk/deskFormat";
import { chartSymbolFor } from "../util/brokerSymbols";

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

// ONLY VIEWS WITH A RAIL ENTRY, 9 Nov 2026. Ten views were cut from the rail
// on 1 Sep and left routed by URL "in case" — decide, scan, risk, screeners,
// news, watchlist, quote, simulation, scanner, daily-screener. The owner found
// one by its bookmark: "why do we still have this screen that shows no value".
// A screen with no rail entry is not parked, it is a trap: it still renders
// stale verdicts, and in decide's case its lane (tradepro-compare, 54 symbols
// x 7 strategies) had been running for 23 hours to feed it. Deleted, with the
// lane. Nothing that is not on the rail is routable.
const DESK_VIEWS: DeskView[] = ["live", "candidates", "portfolio", "oms", "harvest", "options", "research", "swing", "momentum", "post-earnings-puts", "strangle-decisions", "universes", "settings"];

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

  // Master-detail: the symbol currently "drilled into" in the right rail.
  // null → account-value chart (DeskRightRail).
  // string → SymbolDetailRail for that Yahoo symbol.
  // The STRATEGY the symbol was clicked under (from the strategy-grouped row) so
  // the detail rail shows only THAT strategy's orders/fills — not another
  // strategy that happens to hold the same ticker (e.g. the IBKR clone bleeding
  // into ichimoku_equity's validation). null = show all strategies.
  const [selectedStrategy, setSelectedStrategy] = useState<string | null>(null);

  // Positions for the symbol rail come from the BROKER (golden source), not
  // the OMS — the OMS-computed positions table was cut on 10 Nov 2026.
  const [positions, setPositions] = useState<PositionRow[]>([]);
  useEffect(() => {
    let live = true;
    api.accountState().then((st) => {
      if (!live) return;
      const out: PositionRow[] = [];
      for (const a of st?.accounts ?? []) {
        const mode = accountMode(a.broker);
        const broker = a.broker.split("_")[0];
        for (const p of a.positions ?? []) {
          if (!p.qty) continue;
          out.push({
            broker, ticker: p.symbol, chartSymbol: chartSymbolFor(p.symbol, broker),
            qty: p.qty, last: p.mark, pnl: p.unrealisedPnl,
            pnlPct: p.avgCost && p.mark != null ? ((p.mark - p.avgCost) / p.avgCost) * 100 : null,
            ccy: p.currency, mode, avgPrice: p.avgCost,
          });
        }
      }
      setPositions(out);
    }).catch(() => { /* rail says "not held" and the broker card says why */ });
    return () => { live = false; };
  }, []);

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

  return (
    <DeskShell active={view} onSelect={onSelectView} onSelectSymbol={onSearchSelectSymbol}>
      {view === "portfolio" && (
        <>
          {/* Compact-UX pass: always-visible KPI strip + a per-strategy health bar
              full-width, then the detail panels in a MASONRY (CSS multi-column)
              layout. Masonry (not a grid) because the panels have very different
              heights — a grid aligns rows to the tallest card and leaves big gaps
              under the short ones; columns pack cards top-to-bottom with no gaps. */}
          {/* THE FIRST THING ON THE PAGE IS WHAT TO DO (owner, 24 Sep 2026:
              "i do not want to see noise. I want to see what I am supposed to
              do. Buy, sell, etc."). Previously the first card naming an action
              was the SEVENTH down, behind KPIs, health, P&L, audit, broker
              book, equity tracking and fill replay — all true, none of them an
              instruction. Those all still render; they render BELOW. */}
          {/* THE ACTION BOARD LIVES ON CANDIDATES, NOT HERE (28 Sep 2026).
              Owner: "portfolio screen showing seems verbose as we already have
              candidate screen".

              It was put here on 24 Sep because the portfolio page buried the
              action seven cards down. That was true then and there was no
              Candidates screen competing for the job. There is now, and showing
              20 buys and 6 puts above the holdings made Portfolio answer the
              question Candidates already answers — while pushing what you
              actually OWN below the fold.

              One screen, one question: Candidates = what to do. Portfolio =
              what you hold and how it is doing. */}
          <SignalBoard />
          <DeskKpiStrip />


          <div style={{ columnCount: wide ? 3 : 1, columnGap: 12 }}>
            {[
              <PaperLedgerCard key="pnl" />,
              <BrokerBookCard key="brk" />,
              // Compact indicator only — the full run-log stream lives on the
              // Data tab (owner, 22 Aug 2026: "dashboard should be an
              // indicator; logs should be a separate tab").
              <RunLogCard key="runlog" compact />,
            ].map((card) => (
              <div key={card.key} style={{ breakInside: "avoid", marginBottom: 12 }}>
                {card}
              </div>
            ))}
          </div>


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
            {/* Work area: tabbed Orders/Trades/Balances — records, not money */}
            <DeskTabs onOpenSymbol={onSelectSymbol} />

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

      {/* ON EVERY VIEW, above everything. A broken execution lane is not a
          candidates-screen problem; it invalidates whatever board you are
          looking at. Silent when healthy — see the component. */}
      {/* NOT ON LIVE. The banner is paper plumbing — orphan paper legs, paper
          data lanes. The owner opened the Live screen and the first thing on
          it was "41 supporting lanes are broken" about an account that is not
          his. Live's own card says when IT is stale; that is the only health
          line that belongs there. */}
      {view !== "live" && <DeskHealthBanner />}

      {view === "oms"        && <OmsOrders />}
      {view === "harvest"    && <HarvestView />}
      {view === "universes"  && <Universes />}
      {view === "settings"   && <Settings />}
      {view === "research"   && <ResearchView />}
      {view === "candidates" && (
        <>
          {/* Distilled first — verb, symbol, level — then the full table with
              every strategy, its freshness and its gate trace below it. */}
          {/* WHAT YOU ALREADY CARRY COMES FIRST. A board of new trades above
              a position expiring in thirteen days gets the priority exactly
              backwards — the owner read the latter in a chat message on
              1 Nov and had nowhere to see it for the week that followed. */}
          <ActionBoard />
          <CandidatesView onOpenSymbol={onSearchSelectSymbol} />
        </>
      )}
      {view === "swing"      && <SwingView onOpenSymbol={onSearchSelectSymbol} />}
      {view === "post-earnings-puts" && <PostEarningsPutsView />}
      {view === "momentum"   && <MomentumView />}
      {view === "live"       && <LiveView />}
      {view === "options"    && <OptionsDesk />}
      {view === "strangle-decisions" && <StrangleDecisionsView />}
    </DeskShell>
  );
}

// Keep legacy export for any import that uses the named export.
export default Desk;

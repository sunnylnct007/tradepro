import { createBrowserRouter, Navigate, RouterProvider } from "react-router-dom";
import { AuthProvider } from "./auth/AuthProvider";
import { Layout } from "./components/Layout";
import { TradingModeProvider } from "./contexts/TradingMode";
import { Backtests } from "./pages/Backtests";
import { PaperBacktest } from "./pages/PaperBacktest";
import { PaperLive } from "./pages/PaperLive";
import { SessionDetail } from "./pages/SessionDetail";
import { Dashboard } from "./pages/Dashboard";
import { DocumentDetail } from "./pages/DocumentDetail";
import { Documents } from "./pages/Documents";
import { HealthPage } from "./pages/HealthPage";
import { Help } from "./pages/Help";
import { HelpTopic } from "./pages/HelpTopic";
import { IntradayLeaderboard } from "./pages/IntradayLeaderboard";
import { Portfolio } from "./pages/Portfolio";
import { Scanner } from "./pages/Scanner";
import { Settings } from "./pages/Settings";
import { Universes } from "./pages/Universes";
import { Signals } from "./pages/Signals";
import { Simulations } from "./pages/Simulations";
import { Strategies } from "./pages/Strategies";
import { SymbolDeepDive } from "./pages/SymbolDeepDive";
import { IchimokuEquity } from "./pages/IchimokuEquity";
import { IchimokuFx } from "./pages/IchimokuFx";
import { AdminDataBrowser } from "./pages/AdminDataBrowser";
import { Desk } from "./pages/Desk";
import { DailyScreener } from "./pages/DailyScreener";

const router = createBrowserRouter([
  // /desk — the new IBKR-Desktop-style northstar cockpit. Mounted as a
  // SIBLING of "/" (outside the legacy <Layout> nav) because it brings its
  // own three-zone app shell. The existing /trader cockpit is untouched; we
  // flip the default to /desk later.
  { path: "/desk", element: <Desk /> },
  {
    path: "/",
    element: <Layout />,
    children: [
      // Index now lands on the /desk cockpit (the IBKR-style northstar
      // surface) — that's the default home. Legacy pages stay reachable at
      // their paths and get migrated INTO the cockpit one-by-one. The old
      // Decide page is still at /compare.
      { index: true, element: <Navigate to="/desk" replace /> },
      // Migrated views now live IN the /desk cockpit — redirect the legacy
      // standalone routes there so in-site nav never drops back to the old
      // Layout shell (consistent admin theme). Components still render inside
      // DeskShell via ?view=.
      { path: "compare", element: <Navigate to="/desk?view=decide" replace /> },
      { path: "scanner", element: <Scanner /> },
      { path: "screener", element: <DailyScreener /> },
      { path: "portfolio", element: <Portfolio /> },
      { path: "documents", element: <Documents /> },
      { path: "documents/:docId", element: <DocumentDetail /> },
      { path: "signals", element: <Signals /> },
      // Symbol Deep Dive — single page that answers "Should I buy {ticker}?"
      // by stitching every relevant data source into one linear scroll.
      // Spec: strategies/docs/tradepro_claude.pdf (v0.1). 10 sections —
      // header lands today, sections 2-10 follow incrementally.
      //
      // NOTE: still under the legacy <Layout>, not /desk. A direct URL
      // still shows the old menu — considered redirecting into /desk's
      // SymbolDetailRail instead (matching the other legacy-route
      // redirects), but SymbolDetailRail (233 lines: chart+position+WHY+
      // orders) is far thinner than this page (1525 lines: hit-rate panel,
      // decision trace, analyst consensus, full rationale) — that redirect
      // would have silently downgraded every direct link/bookmark. Left
      // as-is; the real fix is migrating THIS page's content into /desk,
      // not routing around it. Search-from-within-/desk already opens
      // SymbolDetailRail in-place without navigating here at all.
      { path: "symbol/:ticker", element: <SymbolDeepDive /> },
      { path: "simulations", element: <Simulations /> },
      { path: "strategies", element: <Strategies /> },
      { path: "strategies/ichimoku-equity", element: <IchimokuEquity /> },
      { path: "strategies/ichimoku-fx", element: <IchimokuFx /> },
      { path: "paper-backtest", element: <PaperBacktest /> },
      { path: "paper-live", element: <PaperLive /> },
      { path: "paper-live/session/:id", element: <SessionDetail /> },
      // /backtests — UI-triggered quant-engine backtests. Trigger
      // form + queue list; the existing Session Detail page (linked
      // from each row) renders the Plotly charts produced by the
      // worker once the run completes.
      { path: "backtests", element: <Backtests /> },
      { path: "oms", element: <Navigate to="/desk?view=oms" replace /> },
      // Cockpit promotion: /trader is the IBKR-style /desk cockpit. The old
      // TraderCockpit was deleted 21 Aug 2026 after the transition settled
      // (recoverable from git history); /trader-legacy redirects with it.
      { path: "trader", element: <Navigate to="/desk" replace /> },
      { path: "trader-legacy", element: <Navigate to="/desk" replace /> },
      // Intraday strategy leaderboard — per-(symbol, strategy)
      // cumulative P&L rolled up from completed session_requests.
      // Powers the "did this strategy actually make money on this
      // symbol" question. See Task #69 phase 2.
      { path: "intraday/leaderboard", element: <IntradayLeaderboard /> },
      { path: "charts", element: <Dashboard /> },
      { path: "health", element: <HealthPage /> },
      { path: "settings", element: <Settings /> },
      { path: "universes", element: <Universes /> },
      { path: "scan", element: <Navigate to="/desk?view=scan" replace /> },
      { path: "admin/data", element: <AdminDataBrowser /> },
      { path: "risk", element: <Navigate to="/desk?view=risk" replace /> },
      { path: "help", element: <Help /> },
      { path: "help/:topic", element: <HelpTopic /> },
    ],
  },
]);

export default function App() {
  return (
    <AuthProvider>
      <TradingModeProvider>
        <RouterProvider router={router} />
      </TradingModeProvider>
    </AuthProvider>
  );
}

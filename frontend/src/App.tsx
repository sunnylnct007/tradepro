import { createBrowserRouter, RouterProvider } from "react-router-dom";
import { AuthProvider } from "./auth/AuthProvider";
import { Layout } from "./components/Layout";
import { TradingModeProvider } from "./contexts/TradingMode";
import { Backtests } from "./pages/Backtests";
import { Compare } from "./pages/Compare";
import { PaperBacktest } from "./pages/PaperBacktest";
import { PaperLive } from "./pages/PaperLive";
import { OmsOrders } from "./pages/OmsOrders";
import { SessionDetail } from "./pages/SessionDetail";
import { TraderCockpit } from "./pages/TraderCockpit";
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
import { Signals } from "./pages/Signals";
import { Simulations } from "./pages/Simulations";
import { Strategies } from "./pages/Strategies";
import { SymbolDeepDive } from "./pages/SymbolDeepDive";

const router = createBrowserRouter([
  {
    path: "/",
    element: <Layout />,
    children: [
      // Index lands on the Decide page (Compare). Running strategies
      // one-by-one via /scanner isn't realistic for the daily workflow
      // — Compare already aggregates the 5-strategy vote per symbol
      // and the worker refreshes it on a schedule. /scanner stays
      // available for single-strategy exploration but isn't the entry.
      { index: true, element: <Compare /> },
      { path: "compare", element: <Compare /> },
      { path: "scanner", element: <Scanner /> },
      { path: "portfolio", element: <Portfolio /> },
      { path: "documents", element: <Documents /> },
      { path: "documents/:docId", element: <DocumentDetail /> },
      { path: "signals", element: <Signals /> },
      // Symbol Deep Dive — single page that answers "Should I buy {ticker}?"
      // by stitching every relevant data source into one linear scroll.
      // Spec: strategies/docs/tradepro_claude.pdf (v0.1). 10 sections —
      // header lands today, sections 2-10 follow incrementally.
      { path: "symbol/:ticker", element: <SymbolDeepDive /> },
      { path: "simulations", element: <Simulations /> },
      { path: "strategies", element: <Strategies /> },
      { path: "paper-backtest", element: <PaperBacktest /> },
      { path: "paper-live", element: <PaperLive /> },
      { path: "paper-live/session/:id", element: <SessionDetail /> },
      // /backtests — UI-triggered quant-engine backtests. Trigger
      // form + queue list; the existing Session Detail page (linked
      // from each row) renders the Plotly charts produced by the
      // worker once the run completes.
      { path: "backtests", element: <Backtests /> },
      { path: "oms", element: <OmsOrders /> },
      { path: "trader", element: <TraderCockpit /> },
      // Intraday strategy leaderboard — per-(symbol, strategy)
      // cumulative P&L rolled up from completed session_requests.
      // Powers the "did this strategy actually make money on this
      // symbol" question. See Task #69 phase 2.
      { path: "intraday/leaderboard", element: <IntradayLeaderboard /> },
      { path: "charts", element: <Dashboard /> },
      { path: "health", element: <HealthPage /> },
      { path: "settings", element: <Settings /> },
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

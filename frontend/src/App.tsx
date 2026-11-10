import { createBrowserRouter, Navigate, RouterProvider } from "react-router-dom";
import { AuthProvider } from "./auth/AuthProvider";
import { Layout } from "./components/Layout";
import { TradingModeProvider } from "./contexts/TradingMode";
import { Settings } from "./pages/Settings";
import { Universes } from "./pages/Universes";
import { Desk } from "./pages/Desk";

// ── THE LEGACY APP IS GONE (10 Nov 2026) ─────────────────────────────────
//
// Owner ruling: "Delete the legacy app under / — scanner, signals,
// simulations, strategies, paper-live, backtests, charts, health, admin,
// help. I'd keep /universes and /settings. Yes."
//
// Twenty pages (~13k lines) that rendered inside the old <Layout> shell were
// deleted, not hidden: they described strategies that had failed their gates,
// read stores that were a session stale, or duplicated a /desk view under a
// different name. Everything is recoverable from git history; nothing is
// reachable by URL. The only surfaces now are:
//
//   /desk        — the cockpit (Live, Candidates, Portfolio, Orders, Data, …)
//   /universes   — which symbols are tracked / harvested (the `harvest` flag)
//   /settings    — configuration
//
// Old bookmarks land on the cockpit rather than a 404: a redirect carries the
// reader to the view that replaced the page where one exists, and to the
// cockpit index otherwise. The redirects name the old paths deliberately so a
// grep for a deleted route finds this list, not silence.
const router = createBrowserRouter([
  { path: "/desk", element: <Desk /> },
  {
    path: "/",
    element: <Layout />,
    children: [
      { index: true, element: <Navigate to="/desk" replace /> },
      { path: "settings", element: <Settings /> },
      { path: "universes", element: <Universes /> },
      // Replaced by a /desk view.
      { path: "portfolio", element: <Navigate to="/desk?view=portfolio" replace /> },
      { path: "oms", element: <Navigate to="/desk?view=oms" replace /> },
      { path: "trader", element: <Navigate to="/desk" replace /> },
      { path: "trader-legacy", element: <Navigate to="/desk" replace /> },
      // Deleted outright — scanner, signals, simulations, strategies/*,
      // paper-live/*, paper-backtest, backtests, charts, health, admin/*,
      // help/*, documents/*, symbol/*, intraday/*.
      { path: "*", element: <Navigate to="/desk" replace /> },
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

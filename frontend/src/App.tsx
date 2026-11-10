import { createBrowserRouter, Navigate, RouterProvider, useLocation } from "react-router-dom";
import { AuthProvider } from "./auth/AuthProvider";
import { TradingModeProvider } from "./contexts/TradingMode";
import { Desk } from "./pages/Desk";

// ── ONE SHELL (10 Nov 2026) ───────────────────────────────────────────────
//
// The legacy app under `/` was deleted earlier today (#361) but Universes and
// Settings were still framed by its sidebar. Owner: "if i go to universe link
// i see old navigation link". They are now VIEWS inside the desk shell, so
// there is exactly one navigation in the product. Old paths redirect into the
// desk and keep their query (so /universes?name=harvest still lands on the
// harvest toggle list). Everything else falls through to the cockpit.

/** Redirect a legacy path into a desk view, carrying the original query string. */
function ToDeskView({ view }: { view: string }) {
  const { search } = useLocation();
  const qs = new URLSearchParams(search);
  qs.set("view", view);
  return <Navigate to={`/desk?${qs.toString()}`} replace />;
}

const router = createBrowserRouter([
  { path: "/desk", element: <Desk /> },
  { path: "/", element: <Navigate to="/desk" replace /> },
  { path: "/universes", element: <ToDeskView view="universes" /> },
  { path: "/settings", element: <ToDeskView view="settings" /> },
  { path: "/portfolio", element: <Navigate to="/desk?view=portfolio" replace /> },
  { path: "/oms", element: <Navigate to="/desk?view=oms" replace /> },
  { path: "/trader", element: <Navigate to="/desk" replace /> },
  { path: "/trader-legacy", element: <Navigate to="/desk" replace /> },
  // Deleted outright — scanner, signals, simulations, strategies/*, paper-live/*,
  // backtests, charts, health, admin/*, help/*, documents/*, symbol/*, intraday/*.
  { path: "*", element: <Navigate to="/desk" replace /> },
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

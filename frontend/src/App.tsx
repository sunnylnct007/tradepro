import { createBrowserRouter, RouterProvider } from "react-router-dom";
import { AuthProvider } from "./auth/AuthProvider";
import { Layout } from "./components/Layout";
import { Compare } from "./pages/Compare";
import { Dashboard } from "./pages/Dashboard";
import { DocumentDetail } from "./pages/DocumentDetail";
import { Documents } from "./pages/Documents";
import { HealthPage } from "./pages/HealthPage";
import { Help } from "./pages/Help";
import { HelpTopic } from "./pages/HelpTopic";
import { Portfolio } from "./pages/Portfolio";
import { Scanner } from "./pages/Scanner";
import { Settings } from "./pages/Settings";
import { Signals } from "./pages/Signals";
import { Simulations } from "./pages/Simulations";

const router = createBrowserRouter([
  {
    path: "/",
    element: <Layout />,
    children: [
      { index: true, element: <Scanner /> },
      { path: "compare", element: <Compare /> },
      { path: "portfolio", element: <Portfolio /> },
      { path: "documents", element: <Documents /> },
      { path: "documents/:docId", element: <DocumentDetail /> },
      { path: "signals", element: <Signals /> },
      { path: "simulations", element: <Simulations /> },
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
      <RouterProvider router={router} />
    </AuthProvider>
  );
}

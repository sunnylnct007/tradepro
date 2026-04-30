import { createBrowserRouter, RouterProvider } from "react-router-dom";
import { AuthProvider } from "./auth/AuthProvider";
import { Layout } from "./components/Layout";
import { Compare } from "./pages/Compare";
import { Dashboard } from "./pages/Dashboard";
import { Help } from "./pages/Help";
import { HelpTopic } from "./pages/HelpTopic";
import { Scanner } from "./pages/Scanner";
import { Signals } from "./pages/Signals";
import { Simulations } from "./pages/Simulations";

const router = createBrowserRouter([
  {
    path: "/",
    element: <Layout />,
    children: [
      { index: true, element: <Scanner /> },
      { path: "compare", element: <Compare /> },
      { path: "signals", element: <Signals /> },
      { path: "simulations", element: <Simulations /> },
      { path: "charts", element: <Dashboard /> },
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

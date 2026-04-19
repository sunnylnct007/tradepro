import { createBrowserRouter, RouterProvider } from "react-router-dom";
import { Layout } from "./components/Layout";
import { Dashboard } from "./pages/Dashboard";
import { Scanner } from "./pages/Scanner";
import { Signals } from "./pages/Signals";
import { Simulations } from "./pages/Simulations";

const router = createBrowserRouter([
  {
    path: "/",
    element: <Layout />,
    children: [
      { index: true, element: <Scanner /> },
      { path: "signals", element: <Signals /> },
      { path: "simulations", element: <Simulations /> },
      { path: "charts", element: <Dashboard /> },
    ],
  },
]);

export default function App() {
  return <RouterProvider router={router} />;
}

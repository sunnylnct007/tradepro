import { createBrowserRouter, RouterProvider } from "react-router-dom";
import { Layout } from "./components/Layout";
import { Dashboard } from "./pages/Dashboard";
import { Simulations } from "./pages/Simulations";

const router = createBrowserRouter([
  {
    path: "/",
    element: <Layout />,
    children: [
      { index: true, element: <Dashboard /> },
      { path: "simulations", element: <Simulations /> },
    ],
  },
]);

export default function App() {
  return <RouterProvider router={router} />;
}

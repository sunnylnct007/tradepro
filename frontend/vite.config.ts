import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  // Allow ?raw imports from the repo root so the Help page can embed
  // docs/ARCHITECTURE.md without duplicating it inside frontend/.
  server: { port: 5173, fs: { allow: [".."] } },
  build: { outDir: "dist", sourcemap: true },
});

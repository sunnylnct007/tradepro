import React from "react";
import ReactDOM from "react-dom/client";
import "./styles.css";
import App from "./App";
import { initFirebase } from "./firebase";

// Stale-chunk recovery. A new deploy changes hashed chunk names, so an
// already-open tab that lazily imports a route (e.g. /oms's plotly chart)
// fetches a chunk the latest build replaced → "Failed to fetch dynamically
// imported module" → the page crashes. Auto-reload ONCE to pick up the
// current build; guard against a reload loop if the chunk is genuinely
// gone. Covers both Vite's preloadError event and a raw rejected import.
function recoverFromStaleChunk(reason: unknown) {
  const msg = String((reason as { message?: string })?.message ?? reason ?? "");
  if (!/dynamically imported module|importing a module|ChunkLoadError|Loading chunk/i.test(msg)) return;
  const KEY = "tradepro:chunk-reloaded-at";
  const last = Number(sessionStorage.getItem(KEY) || 0);
  if (Date.now() - last < 10_000) return; // already tried very recently — don't loop
  sessionStorage.setItem(KEY, String(Date.now()));
  window.location.reload();
}
window.addEventListener("vite:preloadError", (e) => {
  e.preventDefault();
  recoverFromStaleChunk((e as unknown as { payload?: unknown }).payload);
});
window.addEventListener("unhandledrejection", (e) => recoverFromStaleChunk(e.reason));

initFirebase();

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
);

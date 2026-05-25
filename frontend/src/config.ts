// Same-origin fallback so a static bundle deployed behind nginx (which
// proxies /api/* to the API container) doesn't need to know its own
// host at build time. Explicit VITE_API_BASE_URL still wins when set —
// useful for local dev pointing at a remote API.
const fromEnv = (import.meta.env.VITE_API_BASE_URL ?? "").trim();
const sameOrigin =
  typeof window !== "undefined" ? window.location.origin : "http://localhost:5080";

// Python sidecar that exposes build_symbol_analysis_card over HTTP.
// Defaults to localhost:8002 for local dev; production deployments
// proxy through the .NET API at /api/symbol-analysis/ so the sidecar
// isn't reachable directly.
const analysisFromEnv = (import.meta.env.VITE_ANALYSIS_BASE_URL ?? "").trim();

export const config = {
  apiBaseUrl: fromEnv !== "" ? fromEnv : sameOrigin,
  analysisBaseUrl: analysisFromEnv !== "" ? analysisFromEnv : "http://localhost:8002",
  defaultCurrency: import.meta.env.VITE_DEFAULT_CURRENCY ?? "GBP",
  defaultProvider: import.meta.env.VITE_DEFAULT_PROVIDER ?? "yahoo",
};

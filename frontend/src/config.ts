// Same-origin fallback so a static bundle deployed behind nginx (which
// proxies /api/* to the API container) doesn't need to know its own
// host at build time. Explicit VITE_API_BASE_URL still wins when set —
// useful for local dev pointing at a remote API.
const fromEnv = (import.meta.env.VITE_API_BASE_URL ?? "").trim();
const sameOrigin =
  typeof window !== "undefined" ? window.location.origin : "http://localhost:5080";

// Python sidecar that exposes build_symbol_analysis_card.
// Default routes through the .NET API at /api/symbol-analysis/{ticker}
// (auth-enforced + production-safe). VITE_ANALYSIS_BASE_URL overrides
// to talk to the sidecar directly — useful for local dev when running
// only `uv run tradepro-analysis-server` without the .NET API.
const analysisFromEnv = (import.meta.env.VITE_ANALYSIS_BASE_URL ?? "").trim();
const analysisBaseUrl =
  analysisFromEnv !== "" ? analysisFromEnv : (fromEnv !== "" ? fromEnv : sameOrigin);
const analysisDirect = analysisFromEnv !== "";

export const config = {
  apiBaseUrl: fromEnv !== "" ? fromEnv : sameOrigin,
  analysisBaseUrl,
  // True when calling the sidecar directly (override set); false when
  // routing through the .NET API /api/symbol-analysis/ proxy.
  analysisDirect,
  defaultCurrency: import.meta.env.VITE_DEFAULT_CURRENCY ?? "GBP",
  defaultProvider: import.meta.env.VITE_DEFAULT_PROVIDER ?? "yahoo",
};

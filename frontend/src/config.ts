// Same-origin fallback so a static bundle deployed behind nginx (which
// proxies /api/* to the API container) doesn't need to know its own
// host at build time. Explicit VITE_API_BASE_URL still wins when set —
// useful for local dev pointing at a remote API.
const fromEnv = (import.meta.env.VITE_API_BASE_URL ?? "").trim();
const sameOrigin =
  typeof window !== "undefined" ? window.location.origin : "http://localhost:5080";

export const config = {
  apiBaseUrl: fromEnv !== "" ? fromEnv : sameOrigin,
  defaultCurrency: import.meta.env.VITE_DEFAULT_CURRENCY ?? "GBP",
  defaultProvider: import.meta.env.VITE_DEFAULT_PROVIDER ?? "yahoo",
};

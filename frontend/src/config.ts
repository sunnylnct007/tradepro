export const config = {
  apiBaseUrl: import.meta.env.VITE_API_BASE_URL ?? "http://localhost:5080",
  defaultCurrency: import.meta.env.VITE_DEFAULT_CURRENCY ?? "GBP",
  defaultProvider: import.meta.env.VITE_DEFAULT_PROVIDER ?? "yahoo",
};

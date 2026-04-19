/// <reference types="vite/client" />

interface ImportMetaEnv {
  readonly VITE_API_BASE_URL?: string;
  readonly VITE_DEFAULT_CURRENCY?: string;
  readonly VITE_DEFAULT_PROVIDER?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}

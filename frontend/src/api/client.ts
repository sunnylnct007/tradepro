import { config } from "../config";
import { getIdToken } from "../firebase";
import type {
  CanonicalVerdict,
  CandleSeries,
  CompareLatestResponse,
  CompareUniverseSummary,
  CorporateActionsResponse,
  DocumentEnvelope,
  DocumentSummary,
  EarningsMarkersResponse,
  HitRateRequest,
  HitRateResult,
  InsiderTradesResponse,
  InstrumentSearchResponse,
  ScanRequest,
  StrategyCatalogResponse,
  ScanResult,
  SignalDecision,
  SignalRequest,
  SimulationRequest,
  SimulationResult,
  Watchlist,
  WorkerHealth,
} from "./types";

async function authHeaders(): Promise<Record<string, string>> {
  const token = await getIdToken();
  return token ? { Authorization: `Bearer ${token}` } : {};
}

async function get<T>(path: string, params?: Record<string, string | number | undefined>): Promise<T> {
  const url = new URL(path, config.apiBaseUrl);
  if (params) {
    for (const [k, v] of Object.entries(params)) {
      if (v !== undefined && v !== null) url.searchParams.set(k, String(v));
    }
  }
  const resp = await fetch(url, { headers: await authHeaders() });
  if (!resp.ok) throw new Error(`${resp.status} ${resp.statusText}: ${await resp.text()}`);
  return resp.json() as Promise<T>;
}

async function post<T, B>(path: string, body: B): Promise<T> {
  const url = new URL(path, config.apiBaseUrl);
  const resp = await fetch(url, {
    method: "POST",
    headers: { "content-type": "application/json", ...(await authHeaders()) },
    body: JSON.stringify(body),
  });
  if (!resp.ok) throw new Error(`${resp.status} ${resp.statusText}: ${await resp.text()}`);
  return resp.json() as Promise<T>;
}

async function del<T>(path: string): Promise<T> {
  const url = new URL(path, config.apiBaseUrl);
  const resp = await fetch(url, { method: "DELETE", headers: { ...(await authHeaders()) } });
  if (!resp.ok) throw new Error(`${resp.status} ${resp.statusText}: ${await resp.text()}`);
  return resp.json() as Promise<T>;
}

export interface OptionsPaperPosition {
  id: number;
  symbol: string;
  structure: string;          // CASH_SECURED_PUT | COVERED_CALL
  state: string;              // SHORT_PUT_OPEN | ASSIGNED | COVERED_CALL_OPEN | CLOSED
  strike: number | null;
  expiry: string | null;
  dte: number | null;
  delta: number | null;
  iv_rank: number | null;
  premium: number | null;
  contracts: number;
  cash_secured_gbp: number | null;
  regime: string | null;
  opened_at_utc: string;
  closed_at_utc: string | null;
  realised_pnl_gbp: number | null;
  notes: string | null;
  risk_decision_json: string | null;
  updated_at_utc: string;
}
export interface RecordOptionsPositionBody {
  symbol: string;
  structure?: string;
  state?: string;
  strike?: number | null;
  expiry?: string | null;
  dte?: number | null;
  delta?: number | null;
  ivRank?: number | null;
  premium?: number | null;
  contracts?: number | null;
  cashSecuredGbp?: number | null;
  regime?: string | null;
  notes?: string | null;
  riskDecisionJson?: string | null;
}
export interface OptionsPositionEventBody {
  state?: string | null;
  realisedPnlGbp?: number | null;
  notes?: string | null;
}

export const api = {
  health: () => get<{ status: string }>("/health"),
  // Integration/provider readiness — broker connectivity + cash, LLM, Finnhub.
  // Public, no auth. Feeds the Health page + the cockpit caveats banner.
  integrationsHealth: () =>
    get<{
      verdict: "ok" | "warn" | "needs_attention";
      utc: string;
      providers: Array<{
        provider: string;
        label: string;
        status: "ok" | "degraded" | "down" | "disabled";
        detail: string;
        latencyMs: number | null;
        lastCheckedUtc: string;
        mode: string | null;
      }>;
    }>("/health/integrations"),
  providers: () => get<{ providers: string[] }>("/api/marketdata/providers"),
  strategies: () => get<StrategyCatalogResponse>("/api/simulations/strategies"),
  candles: (params: { symbol: string; provider?: string; interval?: string; from?: string; to?: string }) =>
    get<CandleSeries>("/api/marketdata/candles", params),
  /** LIVE intraday price via IBKR (any symbol, on-demand) — the Yahoo candles
   * endpoint serves stale/prior-close data in this environment, so the freshness
   * gate uses this. Returns the latest 1-min bar's close, or null on miss. */
  /** Live status of the C# IBKRBarHarvester (the NEW IBKR-primary intraday
   * harvester → ibkr_price_bars) — separate from the legacy yfinance bar_cache.
   * Surfaces enabled/ticks/IBKR-vs-Yahoo split/backfill so IBKR harvesting is
   * visible on the Data Health screen. */
  /** Live market-data probe. IBKR grants ONE market-data session per account,
   *  so opening the IBKR portal or TWS silently takes it from the desk: auth
   *  stays valid, contracts still resolve, prices go dark. This lets the UI
   *  say so and offer a retry once the portal is closed.
   *
   *  NOTE the "N/A" handling — IBKR returns that literal STRING for a field it
   *  cannot serve, and treating it as a value is exactly how the health probe
   *  certified a full-day outage as healthy (18 Aug 2026). */
  ibkrMarketDataCheck: async (symbol = "SPY") => {
    const snap = await get<{ snapshot?: Record<string, unknown> }>(
      "/api/integrations/ibkr/quote", { symbol, fields: "31,7283,6509" });
    const s = snap?.snapshot ?? {};
    const real = (v: unknown) => {
      if (v === null || v === undefined) return false;
      const t = String(v).trim().toUpperCase();
      return t !== "" && t !== "N/A" && t !== "NA" && t !== "-" && t !== "NONE";
    };
    return {
      live: real(s["31"]) || real(s["7283"]),
      last: real(s["31"]) ? String(s["31"]) : null,
      availability: real(s["6509"]) ? String(s["6509"]) : null,
      symbol,
    };
  },

  ibkrHarvesterStatus: () =>
    get<{
      enabled: boolean;
      intervalSeconds: number;
      resolution: string;
      configuredSymbolCount: number;
      backfilledSymbols: number;
      lastTickAtUtc: string | null;
      nextTickEtaUtc: string | null;
      lastTickIbkr: number;
      lastTickYahoo: number;
      lastTickFailed: number;
      lastTickBarsWritten: number;
      lastError: string | null;
      paused: boolean;
      pausedAtUtc: string | null;
      pauseReason: string | null;
    }>("/api/integrations/ibkr/harvester-status"),
  /** How far the central ibkr_price_bars store has been harvested: per-resolution
   * totals (IBKR vs Yahoo split) + per-symbol first→last timestamp and bar count.
   * Answers "how much 1m/1d data do we actually have, and how far back?" */
  ibkrBarCoverage: () =>
    get<{
      generatedAtUtc: string;
      byResolution: Array<{
        resolution: string;
        symbols: number;
        totalBars: number;
        ibkrBars: number;
        yahooBars: number;
      }>;
      coverage: Array<{
        symbol: string;
        resolution: string;
        firstTs: string | null;
        lastTs: string | null;
        bars: number;
        ibkrBars: number;
        yahooBars: number;
        lastCapturedUtc: string | null;
      }>;
    }>("/api/integrations/ibkr/bar-coverage"),
  /** Harvested candles straight from the central ibkr_price_bars store, at any
   * resolution (1m/5m/1d) — this is the DEEP IBKR intraday data, not Yahoo's
   * shallow 7-day 1m. Powers the flexible chart's intraday view. */
  ibkrBars: (params: { symbol: string; resolution?: string; limit?: number }) => {
    const qp: Record<string, string> = { symbol: params.symbol };
    if (params.resolution) qp.resolution = params.resolution;
    if (params.limit != null) qp.limit = String(params.limit);
    return get<{
      symbol: string;
      resolution: string;
      count: number;
      bars: Array<{ ts: string; open: number; high: number; low: number; close: number; volume: number | null; source: string | null }>;
    }>("/api/integrations/ibkr/bars", qp);
  },
  /** Pause ALL TradePro IBKR usage + log the session out, so the user can log
   * into the IBKR Client Portal (only one Web-API session per account). */
  ibkrPause: (reason?: string) =>
    post<{ paused: boolean; note: string }, Record<string, never>>(
      `/api/integrations/ibkr/pause${reason ? `?reason=${encodeURIComponent(reason)}` : ""}`, {}),
  ibkrResume: () =>
    post<{ paused: boolean; note: string }, Record<string, never>>("/api/integrations/ibkr/resume", {}),
  ibkrLivePrice: async (symbol: string): Promise<number | null> => {
    try {
      const r = await get<{ bars?: Array<{ c: number }>; error?: string }>(
        "/api/integrations/ibkr/price-history",
        { symbol, period: "1d", bar: "1min" },
      );
      const bars = r.bars ?? [];
      return bars.length ? bars[bars.length - 1].c : null;
    } catch {
      return null;
    }
  },
  runSimulation: (req: SimulationRequest) =>
    post<SimulationResult, SimulationRequest>("/api/simulations/run", req),
  evaluateSignal: (req: SignalRequest) =>
    post<SignalDecision, SignalRequest>("/api/signals/evaluate", req),
  /** Canonical verdict from the CACHED compare payloads the Decide page
   *  serves — same data + pipeline, so the two never disagree. Fast,
   *  prod-available. 404s when the symbol isn't in any compared universe. */
  symbolVerdictCached: (symbol: string) =>
    get<CanonicalVerdict>("/api/compare/verdict",
      { symbol: symbol.trim().toUpperCase() }),
  /** Canonical multi-strategy verdict computed LIVE for ANY symbol via the
   *  Python sidecar (same compare() pipeline). Slow (~15-30s) and needs the
   *  analysis sidecar running — used as a fallback for symbols not in a
   *  cached universe. */
  symbolVerdict: (symbol: string, lookbackYears?: number) =>
    get<CanonicalVerdict>(
      `/api/symbol-analysis/${encodeURIComponent(symbol.trim().toUpperCase())}/verdict`,
      lookbackYears ? { lookbackYears } : undefined),
  scanSignals: (req: ScanRequest) =>
    post<ScanResult, ScanRequest>("/api/signals/scan", req),
  hitRate: (req: HitRateRequest) =>
    post<HitRateResult, HitRateRequest>("/api/signals/hitrate", req),
  ukWatchlist: () => get<Watchlist>("/api/watchlists/uk"),
  watchlists: () => get<{ names: string[] }>("/api/watchlists/"),
  compareUniverses: () =>
    get<{ universes: CompareUniverseSummary[] }>("/api/compare/universes"),
  compareLatest: (universe: string) =>
    get<CompareLatestResponse>("/api/compare/latest", { universe }),
  workerHealth: () => get<WorkerHealth>("/api/health/worker"),
  searchInstruments: (q: string, limit = 10) =>
    get<InstrumentSearchResponse>("/api/instruments/search", { q, limit }),
  documents: (symbol?: string) =>
    get<{ documents: DocumentSummary[] }>("/api/documents",
      symbol ? { symbol } : undefined),
  document: (docId: string) =>
    get<DocumentEnvelope>(`/api/documents/${encodeURIComponent(docId)}`),
  documentText: async (docId: string): Promise<string> => {
    const url = new URL(
      `/api/documents/${encodeURIComponent(docId)}/text`,
      config.apiBaseUrl,
    );
    const token = await getIdToken();
    const headers: Record<string, string> = {};
    if (token) headers.authorization = `Bearer ${token}`;
    const resp = await fetch(url, { headers });
    if (!resp.ok) {
      throw new Error(`${resp.status} ${resp.statusText}: ${await resp.text()}`);
    }
    return resp.text();
  },
  // Paper-trading backtest reports — list newest-first + drill into one.
  // Phase D-2: every summary row now carries the optional data_state_hash.
  // Null when the report was produced before Phase D-1 or by a non-BarStore
  // code path (e.g. legacy paper_session callers reading via cache.py).
  paperBacktestReports: () =>
    get<Array<{
      reportId: string;
      kind: string;
      symbol: string;
      start?: string;
      end?: string;
      entryCount: number;
      receivedAtUtc: string;
      dataStateHash: string | null;
    }>>("/api/paper/backtest/reports"),
  paperBacktestReport: (reportId: string) =>
    get<unknown>(`/api/paper/backtest/reports/${encodeURIComponent(reportId)}`),
  // Phase D-2: find every report that ran on the same data state.
  // The cockpit result viewer hits this when the user clicks the hash
  // chip on a backtest row.
  paperBacktestReportsByHash: (dataStateHash: string) =>
    get<{
      dataStateHash: string;
      count: number;
      reports: Array<{
        reportId: string;
        kind: string;
        symbol: string;
        start?: string;
        end?: string;
        entryCount: number;
        receivedAtUtc: string;
        dataStateHash: string | null;
      }>;
    }>(
      `/api/paper/backtest/reports/by-hash/${encodeURIComponent(dataStateHash)}`,
    ),
  paperStrategies: () =>
    get<{
      count: number;
      strategies: Array<{
        name: string;
        class: string;
        summary: string;
        source?: string;                 // "trader-quant" | "alpha-engine" | "scaffold"
        status?: string;                 // code default; overridden via strategyStatusOverrides
        default_lookback_days?: number;  // pre-fill for the Lookback (days) input
        caveats?: string[];              // operator-facing design limitations
        default_params: Record<string, unknown>;
      }>;
    }>("/api/paper/strategies/"),

  // Promotion-lifecycle overrides keyed by strategy_id. Merge client-
  // side with paperStrategies — the override wins when present, otherwise
  // catalog status is the source of truth.
  strategyStatusOverrides: () =>
    get<{
      overrides: Array<{
        StrategyId: string;
        Status: string;
        UpdatedAtUtc: string;
        UpdatedBy: string;
      }>;
    }>("/api/paper/strategy-status/"),

  setStrategyStatus: (strategyId: string, status: string) =>
    post<unknown, { Status: string }>(
      `/api/paper/strategy-status/${encodeURIComponent(strategyId)}`,
      { Status: status },
    ),

  clearStrategyStatus: async (strategyId: string) => {
    // .NET MapDelete; no `del` helper today, use raw fetch.
    const headers = await authHeaders();
    const resp = await fetch(
      new URL(
        `/api/paper/strategy-status/${encodeURIComponent(strategyId)}`,
        config.apiBaseUrl,
      ),
      { method: "DELETE", headers },
    );
    if (!resp.ok && resp.status !== 404) {
      throw new Error(`${resp.status} ${resp.statusText}`);
    }
  },

  // Ops queue — UI-driven strategy runs (task #68 / #69). User
  // enqueues; Mac claims; status flows back to /api/ops/sessions.
  opsSessions: (kind?: string, limit = 100) =>
    get<{
      // Backend ops Envelope() emits snake_case to keep wire format
      // consistent with the rest of the ops surface. The old type
      // here used camelCase, which silently meant every consumer was
      // reading `undefined` from .status / .requestId / .resultSummary
      // — the cockpit signals panel "always empty" symptom traced
      // back to this. Wire is what's real; types now match it.
      sessions: Array<{
        request_id: string;
        kind: string;
        state: string;
        params: Record<string, unknown>;
        claimed_by: string | null;
        requested_at_utc: string;
        claimed_at_utc: string | null;
        completed_at_utc: string | null;
        result_summary: Record<string, unknown> | null;
        error: string | null;
      }>;
    }>("/api/ops/sessions", { kind, limit }),
  runIntraday: (payload: Record<string, unknown>) =>
    post<{
      // Backend ops Envelope() emits snake_case so the response
      // matches the rest of the ops surface (getOpsSession, etc.).
      // Was previously typed as `requestId` etc. but the wire is
      // `request_id` — callers reading res.requestId got undefined
      // and crashed on .slice().
      request_id: string;
      kind: string;
      state: string;
      params: Record<string, unknown>;
      requested_at_utc: string;
      claimed_at_utc: string | null;
      claimed_by: string | null;
      completed_at_utc: string | null;
      result_summary: unknown;
      error: string | null;
    }, Record<string, unknown>>("/api/ops/run-intraday", payload),

  // Phase C-Validate: enqueue a data_validate op for a
  // (canonical, asset_class) tuple. The Mac-side tradepro-data-worker
  // claims it, walks manifests, posts a gap report back via
  // /api/ops/complete-data/{id}. Status polled via opsSessions(kind="data_validate").
  runDataValidate: (payload: {
    canonical: string;
    asset_class: string;
    resolution?: string;
  }) =>
    post<{
      request_id: string;
      kind: string;
      state: string;
      params: Record<string, unknown>;
      requested_at_utc: string;
      result_summary: unknown;
      error: string | null;
    }, typeof payload>("/api/ops/run-data-validate", payload),

  // Phase C-Backfill: enqueue a data_backfill op for a
  // (canonical, asset_class, resolution, from, to) tuple. The Mac-
  // side tradepro-data-worker claims it, calls BarStore.get through
  // the configured provider chain, posts a coverage report back via
  // /api/ops/complete-data/{id}.
  runDataBackfill: (payload: {
    canonical: string;
    asset_class: string;
    resolution: string;
    from: string;            // YYYY-MM-DD or "today"
    to?: string;             // YYYY-MM-DD or "today"; defaults to today
    allow_partial?: boolean; // defaults to true; opt out to fail-loud on gaps
  }) =>
    post<{
      request_id: string;
      kind: string;
      state: string;
      params: Record<string, unknown>;
      requested_at_utc: string;
      result_summary: unknown;
      error: string | null;
    }, typeof payload>("/api/ops/run-data-backfill", payload),

  // Phase C-Reload: enqueue a destructive data_reload op. Force-
  // refreshes the requested partitions through the configured
  // provider chain, OVERWRITING existing parquet files. The `reason`
  // field is mandatory (audit trail); backend rejects payloads
  // without it. The worker's ReloadHandler also requires it as
  // defence in depth.
  runDataReload: (payload: {
    canonical: string;
    asset_class: string;
    resolution: string;
    from: string;
    to?: string;
    reason: string;          // mandatory; min 10 chars enforced server-side
    allow_partial?: boolean;
  }) =>
    post<{
      request_id: string;
      kind: string;
      state: string;
      params: Record<string, unknown>;
      requested_at_utc: string;
      result_summary: unknown;
      error: string | null;
    }, typeof payload>("/api/ops/run-data-reload", payload),
  cancelOpsSession: (requestId: string) =>
    post<unknown, {}>(
      `/api/ops/sessions/${encodeURIComponent(requestId)}/cancel`, {}),
  // Single-session lookup for the Session Detail page. Returns the
  // full snake_case envelope (request_id, params, result_summary, ...).
  getOpsSession: (requestId: string) =>
    get<{
      request_id: string;
      kind: string;
      params: unknown;
      state: string;
      requested_at_utc: string;
      claimed_at_utc: string | null;
      claimed_by: string | null;
      completed_at_utc: string | null;
      result_summary: unknown;
      error: string | null;
    }>(`/api/ops/sessions/${encodeURIComponent(requestId)}`),
  paperSnapshots: () =>
    get<Array<{
      sessionLabel: string;
      broker: string;
      asOfUtc: string;
      strategyCount: number;
      totalFills: number;
      receivedAtUtc: string;
    }>>("/api/paper/snapshots/"),
  paperSnapshot: (sessionLabel: string) =>
    get<unknown>(`/api/paper/snapshots/${encodeURIComponent(sessionLabel)}`),
  // Per-strategy P&L time series for the cockpit "P&L at a glance" graph.
  // scope=daily → one point/strategy/day (all-time); intraday → today.
  pnlSeries: (scope: "daily" | "intraday") =>
    get<{
      scope: string;
      series: Array<{
        strategyId: string;
        points: Array<{ ts: string; realised: number; unrealised: number; equity: number; total: number }>;
      }>;
    }>("/api/paper/pnl/series", { scope }),
  // Broker-reported REALISED P&L per day (golden source; nets spread +
  // financing). The honest "what did we actually make/lose each day" the OMS
  // can't give (pre-2026-06-02 IG fills were booked at price 0).
  accountValueHistory: (days = 30) =>
    get<{
      from: number;
      error?: string | null;
      points: Array<{ broker: string; date: string; currency: string | null; value: number }>;
    }>("/api/account-value/history", { days }),
  igHistory: (days = 7) =>
    get<{
      enabled: boolean;
      from?: string; to?: string;
      totalRealised?: number;
      byDay?: Array<{ date: string; realised: number; trades: number }>;
      byStrategy?: Array<{
        strategyId: string;       // OMS strategy_id, or "unattributed"
        assetClass: string | null; // "FX" | "EQUITY" | null
        realised: number;
        trades: number;
      }>;
      byStrategySymbol?: Array<{
        strategyId: string;
        symbol: string;           // clean instrument label (FX pair / company)
        gross: number;            // DEAL trade P&L
        cost: number;             // financing + admin + commission (WITH)
        net: number;              // gross + cost
        trades: number;
      }>;
      attributionBasis?: string;
      error?: string | null;
    }>("/api/integrations/ig/history", { days }),
  // Per-strategy P&L comparison — one row per strategy, per native currency.
  // Any field null = genuinely not available (reason in `notes`), never a guess.
  pnlByStrategy: (days = 3650) =>
    get<{
      utc: string;
      error?: string | null;
      coverageError?: string | null;
      window?: { days: number; basis: string };
      rows: Array<{
        strategyId: string;
        broker: string;
        currency: string;
        openPnl: number | null;
        realisedToday: number | null;
        realisedLtd: number | null;
        realisedPnl: number | null;
        totalPnl: number | null;
        trades: number;
        winRatePct: number | null;
        // Broker-confirmation truth: of this strategy's FILLED orders, how many
        // carry a broker_order_id. unconfirmed=true means ZERO do — the whole
        // book is OMS-simulated, NOT broker-verified (the IBKR paper clones).
        filledOrders: number;
        brokerConfirmedFills: number;
        unconfirmed: boolean;
        confirmation: string;
        notes: string;
      }>;
    }>("/api/pnl/by-strategy", { days }),
  // Fill-replay artifact (tradepro-fill-replay): per-universe entry-extension +
  // one-shot-vs-staggered, computed from ACTUAL OMS fills on the Mac and pushed.
  fillReplay: (strategy: string, label?: string) =>
    get<{
      strategy: string;
      label: string;
      asOfUtc: string;
      uploadedBy?: string | null;
      artifact: {
        kind: string;
        strategy: string;
        as_of_utc: string;
        stagger_days: number;
        universes: Record<string, {
          n: number;
          entry_dates: string[];
          one_shot_return_pct: number;
          staggered_return_pct: number;
          win_rate_pct: number;
          ext_return_corr: number;
          ext_losers_avg: number | null;
          ext_winners_avg: number | null;
        }>;
        rows: Array<{
          symbol: string;
          universe: string;
          entry_date: string;
          ext_pct: number;
          atr_over_kijun: number;
          return_pct: number;
          staggered_return_pct: number;
        }>;
        missing: string[];
      };
    }>(`/api/fill-replay/${encodeURIComponent(strategy)}/latest`, label ? { label } : undefined),
  // Today's Setups scanner (tradepro-today-setups): universe ranked by entry quality.
  /** Swing candidates — the mean-reversion bracket-order list.
   *  Reuses the today_setups_results store (universe="swing"); the artifact
   *  shape is different from the older setups scanner, hence its own method. */
  swingCandidates: () =>
    get<{
      universe: string; label: string; asOfUtc: string;
      artifact: {
        kind: string; as_of_utc: string; count: number; signal_bar: string;
        rule: { entry: string; target: string; stop: string; timeout: string };
        evidence: {
          gates_file: string; gates_commit: string; trades: number;
          win_rate_pct: number; mean_per_trade_pct: number;
          worst_trade_pct: number; median_hold_sessions: number; note: string;
        };
        limits: string[];
        quarantined?: Array<{ symbol: string; reason: string; detail: string }>;
        candidates: Array<{
          symbol: string; tier: string; bar: string; close: number;
          entry_hint: number; target: number; stop: number; target_pct: number;
          reward_risk: number | null; sigma_below: number; atr_pct: number;
          pct_above_200sma: number; off_52w_high_pct: number | null;
          volume_vs_20d: number | null; max_hold_sessions: number;
        }>;
      };
    }>("/api/today-setups/swing/latest"),

  momentumCandidates: () =>
    get<{
      universe: string; label: string; asOfUtc: string;
      artifact: {
        kind: string; as_of_utc: string; count: number; signal_bar: string;
        rule: { entry: string; stop: string; trailing: string; timeout: string };
        evidence: {
          gates_file: string; gates_commit: string; trades: number;
          win_rate_pct: number; mean_per_trade_pct: number;
          worst_trade_pct: number; median_hold_sessions: number; note: string;
          median_per_trade_pct?: number;
        };
        limits: string[];
        quarantined?: Array<{ symbol: string; reason: string; detail: string }>;
        candidates: Array<{
          symbol: string; bar: string; close: number; entry_hint: number;
          volume_vs_20d?: number | null; chg_5d_pct?: number | null;
          stop: number; trailing_pct: number; pct_above_200sma: number;
          pct_above_20sma: number; atr_pct: number | null;
          off_52w_high_pct: number | null;
          expected_hold_sessions: number; max_hold_sessions: number;
          checks: Array<{ label: string; detail: string; value: string; ok: boolean }>;
          levels: { sma10: number; sma20: number; sma50: number; sma200: number };
          history: {
            trades: number; win_rate_pct: number; mean_pct: number;
            median_pct: number; best_pct: number; worst_pct: number;
            median_bars: number; sample_warning: string | null;
            last_5: Array<{ entry_date: string; exit_date: string; pct: number; bars: number; exit: string }>;
          } | null;
        }>;
      };
    }>("/api/today-setups/momentum/latest"),

  todaySetups: (universe: string) =>
    get<{
      universe: string;
      label: string;
      asOfUtc: string;
      artifact: {
        kind: string;
        universe: string;
        as_of_utc: string;
        counts: { consider: number; extended: number; excluded: number; scanned: number; hold?: number; weak?: number; suspect?: number };
        setups: Array<{
          symbol: string;
          rank: number;
          classification: "consider" | "extended" | "hold";
          close: number;
          range_pctile: number;
          pct_off_high: number;
          pct_over_200sma: number;
          atr_pct: number | null;
          kijun: number;
          dist_to_kijun_pct: number | null;
          dist_atr: number | null;
          stop8: number;
          why: string;
        }>;
        excluded_symbols: string[];
        data_suspect?: string[];
        missing: string[];
        note: string;
      };
    }>(`/api/today-setups/${encodeURIComponent(universe)}/latest`),

  // Signal vs Position audit (tradepro-signal-audit): exits-not-firing + honest NLV P&L.
  signalAudit: (strategy: string) =>
    get<{
      strategy: string;
      asOfUtc: string;
      artifact: {
        kind: string;
        strategy: string;
        broker: string;
        currency: string;
        as_of_utc: string;
        pnl: {
          start_capital: number | null;
          nlv: number | null;
          cash: number | null;
          invested_cost: number | null;
          unrealised_on_held: number | null;
          total_pnl: number | null;
          realized_and_costs: number | null;
          total_pnl_pct: number | null;
        };
        counts: { held: number; hold: number; exit_overdue: number; blind: number; missed_buys?: number };
        missed_buys?: Array<{ symbol: string; universe: string }>;
        exit_overdue: Array<{
          symbol: string | null;
          qty: number;
          entry: number | null;
          price: number | null;
          pnl_pct: number | null;
          classification: string;
          exit_fired: string | null;
          days_overdue: number | null;
        }>;
        blind: Array<string | null>;
        note: string;
      };
    }>(`/api/signal-audit/${encodeURIComponent(strategy)}/latest`),

  // Central run-log — every process/machine's runs + failures, one place.
  runLog: (limit = 60, status?: string) =>
    get<{
      rows: Array<{
        id: number;
        process: string;
        machine: string | null;
        kind: string;
        broker: string | null;
        symbol: string | null;
        status: string; // ok | fail | partial | stale | warn
        error: string | null;
        summary: string | null;
        started_at_utc: string | null;
        finished_at_utc: string | null;
        created_at_utc: string;
      }>;
      health24h: Record<string, number>;
      processes: Array<{
        process: string;
        lastRunUtc: string | null;
        ageHours: number | null;
        maxAgeHours: number;
        stale: boolean;
      }>;
    }>(`/api/run-log/recent?limit=${limit}${status ? `&status=${encodeURIComponent(status)}` : ""}`),
  paperPendingOrders: () =>
    get<Array<{
      orderId: string;
      broker: string;
      brokerMode: string;
      strategyId: string;
      symbol: string;
      t212Ticker: string;
      side: string;
      quantity: number;
      orderType: string;
      tag?: string | null;
      suggestedAtUtc: string;
      barAtEmitClose?: number | null;
      barAtEmitTime?: string | null;
      state: string;
      receivedAtUtc: string;
      decidedAtUtc?: string | null;
      brokerOrderId?: number | null;
      brokerStatus?: string | null;
      rejectionReason?: string | null;
      error?: string | null;
      responseBody?: string | null;
    }>>("/api/paper/pending-orders/"),
  approvePendingOrder: (orderId: string) =>
    post<unknown, {}>(`/api/paper/pending-orders/${encodeURIComponent(orderId)}/approve`, {}),
  rejectPendingOrder: (orderId: string, reason?: string) => {
    const qs = reason ? `?reason=${encodeURIComponent(reason)}` : "";
    return post<unknown, {}>(
      `/api/paper/pending-orders/${encodeURIComponent(orderId)}/reject${qs}`, {});
  },
  // Bulk-reject Pending rows. tickerLike is a SQL LIKE pattern;
  // pass undefined to reject ALL Pending. Returns { rejected: N }.
  bulkRejectPending: (tickerLike?: string, reason?: string) =>
    post<{ rejected: number }, { TickerLike?: string; Reason?: string }>(
      "/api/paper/pending-orders/reject-all",
      { TickerLike: tickerLike, Reason: reason }),

  // Paper-session trigger queue
  runPaperSession: (params: {
    strategy: string;
    symbols: string[];
    capital_usd: number;
    broker?: string;
    placement_mode?: string;
    interval?: string | null;
  }) =>
    post<{ request_id: string; state: string; params: unknown }, typeof params>(
      "/api/ops/run-paper", params
    ),

  paperSessions: (limit = 50) =>
    get<{ sessions: Array<{
      request_id: string;
      kind: string;
      params: unknown;
      state: string;
      requested_at_utc: string;
      claimed_at_utc: string | null;
      claimed_by: string | null;
      completed_at_utc: string | null;
      result_summary: unknown;
      error: string | null;
    }> }>("/api/ops/paper-sessions", { limit }),

  cancelPaperSession: (requestId: string) =>
    post<unknown, {}>(`/api/ops/paper-sessions/${encodeURIComponent(requestId)}/cancel`, {}),

  // Quant backtest queue — /api/quant/backtest/*. The .NET endpoint
  // enqueues a session_request with kind="backtest"; the Mac daemon
  // claims it via /api/ops/poll-backtest and writes back the full
  // result_summary (charts + ensemble summary + monte-carlo summary).
  // Status flows back via getBacktest(); the existing Session Detail
  // page (/paper-live/session/:id) renders the charts.
  runBacktest: (req: {
    Strategy: string;
    Symbols: string[];
    Start?: string | null;
    End?: string | null;
    InitialCapital?: number | null;
    NSims?: number | null;
    Years?: number | null;
    Seed?: number | null;
    Label?: string | null;
  }) =>
    post<{ requestId: string; state: string }, typeof req>(
      "/api/quant/backtest/run", req,
    ),

  getBacktest: (requestId: string) =>
    get<{
      request_id: string;
      kind: string;
      params: unknown;
      state: string;
      requested_at_utc: string;
      claimed_at_utc: string | null;
      claimed_by: string | null;
      completed_at_utc: string | null;
      result_summary: unknown;
      error: string | null;
    }>(`/api/quant/backtest/${encodeURIComponent(requestId)}`),

  listBacktests: (limit = 50) =>
    get<{
      backtests: Array<{
        request_id: string;
        kind: string;
        params: unknown;
        state: string;
        requested_at_utc: string;
        claimed_at_utc: string | null;
        claimed_by: string | null;
        completed_at_utc: string | null;
        result_summary: unknown;
        error: string | null;
      }>;
    }>("/api/quant/backtest/", { limit }),

  cancelBacktest: (requestId: string) =>
    post<unknown, {}>(
      `/api/quant/backtest/${encodeURIComponent(requestId)}/cancel`, {},
    ),

  uploadDocument: async (
    file: File,
    title: string,
    symbols: string,
    sourceUrl?: string,
  ): Promise<{
    docId: string;
    title: string;
    fileKind: string;
    extractor: string;
    charCount: number;
    pageCount: number | null;
    linkedSymbols: string[];
  }> => {
    const url = new URL("/api/documents/upload", config.apiBaseUrl);
    const fd = new FormData();
    fd.append("file", file);
    fd.append("title", title);
    fd.append("symbols", symbols);
    if (sourceUrl) fd.append("sourceUrl", sourceUrl);
    const token = await getIdToken();
    const headers: Record<string, string> = {};
    if (token) headers.authorization = `Bearer ${token}`;
    const resp = await fetch(url, { method: "POST", headers, body: fd });
    if (!resp.ok) {
      throw new Error(`${resp.status} ${resp.statusText}: ${await resp.text()}`);
    }
    return resp.json();
  },

  // OMS — order lifecycle. /api/oms/* lives in OmsEndpoints.cs; backs
  // the OmsOrders page. Field names are PascalCase because .NET
  // serializes records that way (no [JsonPropertyName] overrides).
  omsOrders: (states?: string[], limit = 100, symbol?: string, includeDeleted?: boolean) => {
    const q: Record<string, string | number> = { limit };
    if (states && states.length) q.states = states.join(",");
    // ?symbol=MS pulls that bare ticker's FULL history (SQL-filtered), so the
    // symbol detail view isn't capped to the recent LIMIT window.
    if (symbol) q.symbol = symbol;
    // includeDeleted=true = the "show archived" view (soft-deleted rows kept for
    // execution-stats analytics).
    if (includeDeleted) q.includeDeleted = "true";
    return get<{ orders: OmsOrderRow[] }>("/api/oms/orders", q);
  },
  omsOrderEvents: (orderId: string) =>
    get<{ events: OmsOrderEventRow[] }>(
      `/api/oms/orders/${encodeURIComponent(orderId)}/events`,
    ),
  // Full decision-chain audit: OMS state transitions + RiskGate
  // events + LLM evaluations. Answers "on what basis was this
  // approved/rejected" in one round trip.
  omsOrderAudit: (orderId: string) =>
    get<{
      order: OmsOrderRow;
      events: OmsOrderEventRow[];
      riskEvents: Array<{
        id: string;
        occurred_at_utc: string;
        gate: string;
        decision: string;
        reason: string | null;
        detail_json: string | null;
      }>;
      llmEvals: Array<{
        id: string;
        occurred_at_utc: string;
        purpose: string;
        llm_url: string;
        llm_model: string;
        source_tag: string | null;
        latency_ms: number | null;
        decision: string;
        confidence: number | null;
        reasoning: string | null;
        detail_json: string | null;
      }>;
      summary: {
        nStateTransitions: number;
        nRiskEvents: number;
        nLlmEvals: number;
        riskBlocks: number;
        llmApprovals: number;
        llmRejections: number;
      };
    }>(`/api/oms/orders/${encodeURIComponent(orderId)}/audit`),
  omsApprove: (orderId: string) =>
    post<OmsOrderRow, {}>(
      `/api/oms/orders/${encodeURIComponent(orderId)}/approve`,
      {},
    ),
  // Manual OrderIntent enqueue — used by the cockpit "Test placement"
  // panel to smoke-test the OMS → T212 demo chain end-to-end without
  // a real strategy session. Strategy code calls the same endpoint
  // from the Mac daemon (paper/brokers/t212.py).
  // Free-form key-value settings (migration 011_settings_kv.sql).
  // Each row has metadata (label, description, value_type, min/max,
  // allowed_values) so the UI renders the right input automatically.
  settingsKv: () =>
    get<{
      settings: Array<{
        key: string;
        value: unknown;
        valueType: string;
        label: string | null;
        description: string | null;
        category: string;
        minValue: number | null;
        maxValue: number | null;
        allowedValues: unknown;
        updatedAtUtc: string;
        updatedBy: string;
      }>;
    }>("/api/settings-kv/"),
  updateSettingKv: async (key: string, value: unknown) => {
    const url = new URL(`/api/settings-kv/${encodeURIComponent(key)}`, config.apiBaseUrl);
    const resp = await fetch(url, {
      method: "PUT",
      headers: { "content-type": "application/json", ...(await authHeaders()) },
      body: JSON.stringify(value),
    });
    if (!resp.ok) throw new Error(`${resp.status} ${resp.statusText}: ${await resp.text()}`);
    return resp.json();
  },

  // Strategy → broker mapping (migration 021 / 024 / 025).
  // GET returns the current table + valid brokers + the global default
  // so the UI doesn't have to know the list of brokers from elsewhere.
  // PUT upserts; DELETE removes the row (strategy falls back to the
  // global default).
  strategyBrokerMap: () =>
    get<{
      validBrokers: string[];
      defaultBroker: string | null;
      mappings: Array<{
        strategy_id: string;
        broker: string;
        account_id: string | null;
        note: string | null;
        updated_at_utc: string;
        updated_by: string;
      }>;
    }>("/api/admin/strategy-broker-map"),
  updateStrategyBrokerMap: async (
    strategyId: string,
    body: { broker: string; accountId?: string | null; note?: string | null },
  ) => {
    const url = new URL(
      `/api/admin/strategy-broker-map/${encodeURIComponent(strategyId)}`,
      config.apiBaseUrl,
    );
    const resp = await fetch(url, {
      method: "PUT",
      headers: { "content-type": "application/json", ...(await authHeaders()) },
      body: JSON.stringify(body),
    });
    if (!resp.ok) throw new Error(`${resp.status} ${resp.statusText}: ${await resp.text()}`);
    return resp.json();
  },
  deleteStrategyBrokerMap: async (strategyId: string) => {
    const url = new URL(
      `/api/admin/strategy-broker-map/${encodeURIComponent(strategyId)}`,
      config.apiBaseUrl,
    );
    const resp = await fetch(url, {
      method: "DELETE",
      headers: await authHeaders(),
    });
    if (!resp.ok) throw new Error(`${resp.status} ${resp.statusText}: ${await resp.text()}`);
    return resp.json();
  },

  // Paper-strategy catalog pushed by the Mac worker. Lets the UI
  // enumerate every registered strategy (so the broker-mapping
  // editor can show unmapped strategies alongside mapped ones).
  paperStrategyCatalog: () =>
    get<{
      count: number;
      strategies: Array<{
        name: string;
        class?: string;
        default_params?: Record<string, unknown>;
        caveats?: string[];
        source?: string;
      }>;
    }>("/api/paper/strategies/"),

  // Symbol universes (Wikipedia-scraped, curated via overrides).
  // Worker pushes via tradepro-refresh-universes; trader picks from
  // these on the Trigger forms.
  universes: () =>
    get<{
      universes: Array<{
        name: string;
        sourceUrl: string;
        symbolCount: number;
        fetchedAtUtc: string;
        source: string;
        includedOverrides: number;
        excludedOverrides: number;
      }>;
    }>("/api/universes/"),
  universe: (name: string) =>
    get<{
      header: {
        name: string;
        sourceUrl: string;
        symbolCount: number;
        fetchedAtUtc: string;
        source: string;
      };
      symbols: Array<{
        ticker: string;
        name: string | null;
        sector: string | null;
        industry: string | null;
        overrideAction: "INCLUDE" | "EXCLUDE" | null;
        effective: boolean;
      }>;
    }>(`/api/universes/${encodeURIComponent(name)}`),
  setUniverseOverride: (name: string, body: { Ticker: string; Action: "INCLUDE" | "EXCLUDE"; Note?: string }) =>
    post<{ ok: boolean }, typeof body>(
      `/api/universes/${encodeURIComponent(name)}/overrides`, body),
  clearUniverseOverride: async (name: string, ticker: string) => {
    const headers = await authHeaders();
    const resp = await fetch(
      new URL(
        `/api/universes/${encodeURIComponent(name)}/overrides/${encodeURIComponent(ticker)}`,
        config.apiBaseUrl,
      ),
      { method: "DELETE", headers },
    );
    if (!resp.ok && resp.status !== 404) {
      throw new Error(`${resp.status} ${resp.statusText}`);
    }
  },

  omsEnqueue: (intent: {
    ClientOrderId: string;
    Broker: string;
    Symbol: string;
    Side: "BUY" | "SELL";
    Qty: number;
    OrderType: "MKT" | "LMT" | string;
    StrategyId: string;
    PlacedBy: string;
    TimeInForce?: string;
    LimitPrice?: number | null;
    StopPrice?: number | null;
  }) =>
    post<OmsOrderRow, typeof intent>(`/api/oms/orders`, intent),
  omsReject: (orderId: string, reason: string) =>
    post<OmsOrderRow, { Reason: string }>(
      `/api/oms/orders/${encodeURIComponent(orderId)}/reject`,
      { Reason: reason },
    ),
  omsCancel: (orderId: string, reason: string) =>
    post<OmsOrderRow, { Reason: string }>(
      `/api/oms/orders/${encodeURIComponent(orderId)}/cancel`,
      { Reason: reason },
    ),
  // T212 cash balance — Invest product (stocks/ETFs). CFD cash is
  // a separate T212 endpoint not yet wired (follow-up).
  t212Cash: (account: "demo" | "live" = "demo") =>
    get<{
      enabled: boolean;
      mode: string;
      message?: string;
      free?: number | null;
      invested?: number | null;
      total?: number | null;
      blocked?: number | null;
      ppl?: number | null;
      currency?: string | null;
      error?: string | null;
      fetchedAtUtc?: string;
    }>("/api/integrations/trading212/cash", { account }),

  // Multi-broker cash summary — every connected broker's cash in one
  // hit so the cockpit can show a strip rather than fetching each
  // broker separately. Each row has `status` so the UI can render
  // disabled / down without throwing.
  cashSummary: () =>
    get<{
      utc: string;
      brokers: Array<{
        broker: string;
        label: string;
        status: "ok" | "degraded" | "down" | "disabled";
        currency?: string | null;
        free?: number | null;
        invested?: number | null;
        total?: number | null;
        openPnl?: number | null;
        available?: number | null;
        balance?: number | null;
        error?: string | null;
        note?: string | null;
        mode?: string | null;
      }>;
    }>("/api/integrations/cash-summary"),

  // Per-strategy run-timing + outcome health, so a silently-stopped strategy is
  // visible on the cockpit instead of buried in logs. status: healthy | idle |
  // blocked | stale | unknown.
  strategiesHealth: () =>
    get<{
      generatedAtUtc: string;
      strategies: Array<{
        strategy: string;
        label: string;
        status: "healthy" | "idle" | "blocked" | "stale" | "unknown";
        reason: string;
        lastOrderUtc: string | null;
        minutesSinceOrder: number | null;
        today: { fills: number; cancels: number; pending: number; total: number };
      }>;
    }>("/api/strategies/health"),

  bulkCancelPending: (body: { strategyPrefix?: string; broker?: string; reason?: string }) =>
    post<{ cancelled: number; ids: string[]; strategyPrefix: string | null; broker: string | null; actor: string; reason: string }, typeof body>(
      "/api/admin/oms/bulk-cancel-pending", body),

  // Operational alerts surfaced in the cockpit banner. The first
  // producer is the paper-session fail-closed guard (a strategy that
  // aborted because it couldn't confirm its position from the broker).
  alerts: (limit = 50) =>
    get<{
      count: number;
      critical: number;
      alerts: Array<{
        id: string;
        source: string;
        severity: "info" | "warn" | "critical";
        code: string;
        title: string;
        detail: string;
        strategyId: string | null;
        broker: string | null;
        symbols: string[];
        dedupKey: string | null;
        occurrences: number;
        firstSeenUtc: string;
        lastSeenUtc: string;
      }>;
    }>("/api/alerts", { limit }),
  resolveAlert: (id: string) =>
    post<{ resolved: boolean; id: string }, Record<string, never>>(
      `/api/alerts/${id}/resolve`, {},
    ),

  // Sync OMS ← broker: adopt the broker's actual net positions into the
  // OMS (synthetic, audited RECONCILE adjustments). broker = OMS label
  // e.g. "T212_DEMO" | "IG_DEMO". Returns the adjustments made.
  // force=true confirms the broker is genuinely flat (e.g. after a demo
  // reset) so the "broker empty + OMS has positions" fail-safe flattens
  // the OMS instead of refusing with 409.
  syncOmsFromBroker: (broker: string, force = false) =>
    post<{
      broker: string;
      adjusted: number;
      adjustments: Array<{ symbol: string; side: string; delta: number; targetQty: number; fromOmsQty: number }>;
    }, { broker: string; force: boolean }>("/api/oms/positions/sync-from-broker", { broker, force }),

  // T212 open positions (equity). account = demo | live.
  t212Positions: (account: "demo" | "live" = "demo") =>
    get<import("../types/cockpit").T212PosResp>(
      "/api/integrations/trading212/positions", { account }),

  // Flatten (net to flat) open IG deals. Pass { dealId } to close one
  // deal, { symbol } (bare pair e.g. "EURUSD") to close all deals for a
  // pair, or {} to flatten everything. Each close is confirmed at IG, so
  // `closed`/`failed` reflect actual execution (a weekend-closed market
  // returns failed with reason MARKET_CLOSED). Mutating → UI confirms.
  flattenIg: (opts?: { symbol?: string; dealId?: string }) =>
    post<{
      symbol: string;
      requested: number;
      closed: number;
      failed: number;
      details: Array<{ epic: string; dealId?: string; direction?: string; size?: number; ok: boolean; error?: string | null }>;
    }, { symbol?: string; dealId?: string }>(
      "/api/integrations/ig/positions/flatten", opts ?? {}),

  // IG open positions (FX / CFD). The cockpit position panel is
  // otherwise T212-equity-only; this surfaces the FX book that the
  // ichimoku_fx_mr strategy trades via IG. Not account-scoped — IG
  // demo/live is a single backend config.
  igPositions: () =>
    get<{
      enabled: boolean;
      mode: string;
      count?: number;
      error?: string | null;
      positions: Array<{
        ticker: string;
        quantity: number;
        averagePricePaid: number | null;
        currentPrice: number | null;
        unrealisedAbs: number | null;
        unrealisedPct: number | null;
        lotSize: number | null;
        instrumentName: string | null;
        dealId: string | null;
      }>;
    }>("/api/integrations/ig/positions"),

  // IBKR open positions (equities). READ-ONLY. Surfaced in the desk +
  // cockpit position tables with an IBKR broker tag, the same way T212
  // live positions render. When the tradepro/ibkr secret is absent the
  // backend returns { enabled:false, note } and the UI renders nothing.
  ibkrPositions: () =>
    get<{
      enabled: boolean;
      broker?: string;
      mode?: string;
      count?: number;
      note?: string;
      error?: string | null;
      positions: Array<{
        ticker: string | null;
        instrumentName: string | null;
        quantity: number;
        averagePricePaid: number | null;
        currentPrice: number | null;
        unrealisedAbs: number | null;
        unrealisedPct: number | null;
        currency: string | null;
      }>;
    }>("/api/integrations/ibkr/positions"),

  // Broker account snapshots the Mac daemons pushed (broker_account_state).
  // Surfaces an algo clone's OWN account — e.g. the IBKR PAPER clone DUP656969,
  // which the live IBKRClient (ibkrPositions) can't see.
  accountState: () =>
    get<{
      accounts: Array<{
        broker: string;
        accountId: string | null;
        currency: string | null;
        netLiquidation: number | null;
        totalCash: number | null;
        unrealisedPnl: number | null;
        dailyPnl: number | null;
        positions: Array<{
          symbol: string;
          secType: string | null; // STK / OPT / FUT — broker-golden asset class
          right: string | null; // 'P' / 'C' for options
          strike: number | null;
          expiry: string | null;
          qty: number;
          mark: number | null;
          marketValue: number | null;
          avgCost: number | null;
          unrealisedPnl: number | null;
          currency: string | null;
        }>;
        updatedAtUtc: string;
      }>;
    }>("/api/integrations/account-state"),

  omsPositions: (strategyId?: string) =>
    get<{ positions: Array<{ strategyId: string; symbol: string; broker: string; quantity: number; avgPrice: number | null; lastFillAtUtc: string }> }>(
      "/api/oms/positions", strategyId ? { strategyId } : undefined,
    ),
  omsPositionsDiff: (account: "demo" | "live" = "demo", strategyId?: string) =>
    get<{
      account: string;
      strategyId: string | null;
      brokerEnabled: boolean;
      t212Error: string | null;
      fetchedAtUtc: string;
      totalSymbols: number;
      drifted: number;
      rows: Array<{ symbol: string; omsQty: number; t212Qty: number; diff: number }>;
    }>("/api/oms/positions/diff", strategyId ? { account, strategyId } : { account }),
  omsMode: () => get<{ mode: string }>("/api/oms/mode"),
  setOmsMode: (mode: "auto" | "manual") =>
    post<{ mode: string; prior: string }, { Mode: string }>(
      "/api/oms/mode",
      { Mode: mode },
    ),

  // ── IT admin: raw-table browsers ─────────────────────────────
  adminEvents: (p?: { event_type?: string; since_seq?: number; before_seq?: number; limit?: number }) =>
    get<{ rows: AdminEventRow[] }>("/api/admin/events", p as Record<string, string | number | undefined>),
  adminOrders: (p?: { symbol?: string; strategy?: string; mode?: string; limit?: number }) =>
    get<{ rows: AdminOrderRow[] }>("/api/admin/orders", p as Record<string, string | number | undefined>),
  adminFills: (p?: { order_id?: string; limit?: number }) =>
    get<{ rows: AdminFillRow[] }>("/api/admin/fills", p as Record<string, string | number | undefined>),
  adminOmsEvents: (p?: { order_id?: string; limit?: number }) =>
    get<{ rows: AdminOmsEventRow[] }>("/api/admin/oms-events", p as Record<string, string | number | undefined>),
  adminStrategyVersions: () =>
    get<{ rows: AdminStrategyVersionRow[] }>("/api/admin/strategy-versions"),

  // ── Equity pipeline validation artifact ──────────────────────
  // Backed by EquityPipelineEndpoints.cs — Mac CLI
  // tradepro-equity-pipeline --push emits the JSON; this read
  // surfaces it on the strategy validation page.
  equityPipelineLatest: (strategy: string, label = "latest") =>
    get<EquityPipelineEnvelope>(
      `/api/equity-pipeline/${encodeURIComponent(strategy)}/latest`,
      { label },
    ),
  equityPipelineRuns: (strategy: string) =>
    get<{
      strategy: string;
      runs: Array<{
        label: string;
        as_of_utc: string;
        uploaded_at_utc: string;
        uploaded_by: string | null;
        note: string | null;
      }>;
    }>(`/api/equity-pipeline/${encodeURIComponent(strategy)}`),

  // Earnings-marker overlay for PriceHistoryChart. Returns reported earnings
  // events for `symbol` within the last `lookbackDays` (default 1825 = 5y).
  // Empty on fetch failure so the chart degrades to "no markers" cleanly.
  earningsMarkers: (symbol: string, lookbackDays?: number) =>
    get<EarningsMarkersResponse>("/api/marketdata/earnings", {
      symbol,
      ...(lookbackDays !== undefined ? { lookbackDays } : {}),
    }),

  // Corporate-action overlay (dividends "D" + splits "S") for PriceHistoryChart.
  // Defaults to 5y lookback. Silent failure — chart renders with no chips.
  corporateActions: (symbol: string, lookbackDays?: number) =>
    get<CorporateActionsResponse>("/api/marketdata/corporate-actions", {
      symbol,
      ...(lookbackDays !== undefined ? { lookbackDays } : {}),
    }),

  // Insider buy overlay — discretionary purchase transactions only.
  // Default 365d lookback. Silent failure → no "I" chips on chart.
  insiderBuys: (symbol: string, lookbackDays?: number) =>
    get<InsiderTradesResponse>("/api/marketdata/insiders", {
      symbol,
      ...(lookbackDays !== undefined ? { lookbackDays } : {}),
    }),

  // Trustworthy data layer — Phase A (migrations 029 + 030 +
  // DataTrustEndpoints.cs). Three concerns:
  //   * /assumptions   — auditable list of data assumptions
  //   * /preferences   — provider chain per (asset_class, resolution)
  //   * /backfill      — Phase-A placeholder; functional in Phase C
  // See CURRENT_BACKTEST_LIMITATIONS.md + ROADMAP for the framing.
  dataAssumptions: () =>
    get<{
      assumptions: Array<{
        id: string;
        description: string;
        severity: "CRITICAL" | "HIGH" | "MEDIUM" | "LOW" | "INFORMATIONAL";
        status: "HONEST" | "PARTIAL" | "OPTIMISTIC" | "FICTIONAL";
        affects: string[];
        consequence: string;
        remedy: string;
        mitigation: string | null;
        last_reviewed_at_utc: string;
        last_reviewed_by: string;
      }>;
    }>("/api/admin/data-trust/assumptions"),
  dataSourcePreferences: () =>
    get<{
      validProviders: string[];
      preferences: Array<{
        asset_class: string;
        resolution: string;
        provider_chain: string[];
        notes: string | null;
        updated_at_utc: string;
        updated_by: string;
      }>;
    }>("/api/admin/data-trust/preferences"),
  updateDataSourcePreference: async (
    assetClass: string,
    resolution: string,
    body: { providerChain: string[]; notes?: string | null },
  ) => {
    const url = new URL(
      `/api/admin/data-trust/preferences/${encodeURIComponent(assetClass)}/${encodeURIComponent(resolution)}`,
      config.apiBaseUrl,
    );
    const resp = await fetch(url, {
      method: "PUT",
      headers: { "content-type": "application/json", ...(await authHeaders()) },
      body: JSON.stringify(body),
    });
    if (!resp.ok) throw new Error(`${resp.status} ${resp.statusText}: ${await resp.text()}`);
    return resp.json();
  },
  deleteDataSourcePreference: async (assetClass: string, resolution: string) => {
    const url = new URL(
      `/api/admin/data-trust/preferences/${encodeURIComponent(assetClass)}/${encodeURIComponent(resolution)}`,
      config.apiBaseUrl,
    );
    const resp = await fetch(url, {
      method: "DELETE",
      headers: await authHeaders(),
    });
    if (!resp.ok) throw new Error(`${resp.status} ${resp.statusText}: ${await resp.text()}`);
    return resp.json();
  },
  // Phase A: backfill endpoint returns 501 with a structured roadmap
  // pointer. The UI calls this only to surface the not-yet-implemented
  // message in a tooltip — never as part of a normal operator flow.
  triggerDataBackfill: async (body: {
    assetClass: string;
    symbol: string;
    resolution: string;
    fromDate: string;
    toDate: string;
  }): Promise<{ error: string; detail: string } | { jobId: string }> => {
    const url = new URL("/api/admin/data-trust/backfill", config.apiBaseUrl);
    const resp = await fetch(url, {
      method: "POST",
      headers: { "content-type": "application/json", ...(await authHeaders()) },
      body: JSON.stringify(body),
    });
    // 501 carries a structured body explaining the roadmap status —
    // don't throw, return it so the UI can render the message.
    return resp.json();
  },

  // Phase B-2: bar-cache observability. The Python BarStore POSTs
  // a telemetry event per fetch to /events; per-symbol health is
  // an incrementally-updated snapshot on /health. The cockpit panel
  // calls these to surface "what is the cache doing right now".
  barCacheEvents: (params?: {
    canonical?: string;
    assetClass?: string;
    result?: string;
    limit?: number;
  }) => {
    const qp: Record<string, string | number | undefined> = {};
    if (params?.canonical) qp.canonical = params.canonical;
    if (params?.assetClass) qp.asset_class = params.assetClass;
    if (params?.result) qp.result = params.result;
    if (params?.limit !== undefined) qp.limit = params.limit;
    return get<{
      events: Array<{
        id: number;
        occurred_at_utc: string;
        canonical: string;
        asset_class: string;
        resolution: string;
        range_start_utc: string;
        range_end_utc: string;
        result: string;
        source_chain: string[];
        provider_used: string | null;
        provider_versions_text: string;
        rows_expected: number | null;
        rows_returned: number | null;
        gaps_detected_count: number;
        schema_version: string;
        latency_ms: number;
        error_class: string | null;
        error_provider: string | null;
        error_message: string | null;
        retry_strategy: string | null;
      }>;
    }>("/api/admin/data-trust/bar-cache/events", qp);
  },
  fillAttribution: (params: { date?: string; days?: number; strategy?: string }) => {
    const qp: Record<string, string | undefined> = {};
    if (params.date) qp.date = params.date;
    if (params.days) qp.days = String(params.days);
    if (params.strategy) qp.strategy = params.strategy;
    return get<{
      kind: string;
      from_date: string;
      to_date: string;
      total_realised_pnl: number;
      total_fills: number;
      symbols_traded: number;
      worst_symbol: string | null;
      worst_pnl: number | null;
      best_symbol: string | null;
      best_pnl: number | null;
      per_symbol: Array<{
        symbol: string;
        realised_pnl: number;
        fills: number;
        closed_trades: number;
        winning_trades: number;
        win_rate_pct: number | null;
        buys: number;
        sells: number;
      }>;
      note: string;
    }>("/api/fills/attribution", qp);
  },

  /** One row per DATASET: is the data there, and since when. */
  dataReadiness: () =>
    get<{
      generatedAtUtc: string;
      verdict: string;
      usable: number;
      total: number;
      datasets: Array<{
        key: string;
        label: string;
        purpose: string;
        usable: boolean;
        asOfUtc: string | null;
        ageHours: number | null;
        brokenSince: string | null;
        detail: string;
        extra?: { consecutiveDegraded?: number; depthDays?: number; neededDays?: number } | null;
      }>;
      note: string;
    }>("/api/data-readiness"),

  // `resolution` defaults to 1d server-side (migration 064 gave each harvest
  // lane its own row). Pass "all" only when you intend to render a mixed table.
  barCacheHealth: (params?: { canonical?: string; assetClass?: string; resolution?: string }) => {
    const qp: Record<string, string | undefined> = {};
    if (params?.canonical) qp.canonical = params.canonical;
    if (params?.assetClass) qp.asset_class = params.assetClass;
    if (params?.resolution) qp.resolution = params.resolution;
    return get<{
      resolution: string;
      available: string[];
      health: Array<{
        canonical: string;
        asset_class: string;
        resolution: string;
        last_fetched_at_utc: string | null;
        last_fetched_result: string | null;
        last_fetched_provider: string | null;
        last_fetched_resolution: string | null;
        coverage_start_date: string | null;
        coverage_end_date: string | null;
        coverage_partitions: number;
        missing_days_count: number;
        schema_version: string | null;
        manifest_violations_last_30d: number;
        last_corp_action_at_utc: string | null;
        last_corp_action_type: string | null;
        updated_at_utc: string;
      }>;
    }>("/api/admin/data-trust/bar-cache/health", qp);
  },
  // Phase G-1 — coverage matrix. Returns per-(canonical × month)
  // status derived from bar_cache_events. `months` is the rolling
  // window (default 12, clamped server-side to [1, 36]).
  barCacheCoverageMatrix: (params?: {
    assetClass?: string;
    resolution?: string;
    months?: number;
  }) => {
    const qp: Record<string, string | undefined> = {};
    if (params?.assetClass) qp.asset_class = params.assetClass;
    if (params?.resolution) qp.resolution = params.resolution;
    if (params?.months !== undefined) qp.months = String(params.months);
    return get<{
      months: string[]; // "YYYY-MM", ascending
      asset_class: string | null;
      resolution: string | null;
      rows: Array<{
        canonical: string;
        asset_class: string;
        cells: Record<string, {
          status:
            | "full" | "partial" | "error"
            | "rate_limited" | "no_provider" | "unknown";
          last_result: string;
          last_provider: string | null;
          rows_returned: number | null;
          rows_expected: number | null;
          occurred_at_utc: string;
        }>;
      }>;
    }>("/api/admin/data-trust/bar-cache/coverage-matrix", qp);
  },

  // Per-symbol data-QUALITY score: "is this symbol's data good enough to
  // decide on TODAY?" Rolls bar_cache_health into one honest verdict so the
  // Data-Health dashboard shows good-for-today / pending-N-days at a glance.
  barCacheQuality: (params?: { assetClass?: string; staleAfterDays?: number }) => {
    const qp: Record<string, string | undefined> = {};
    if (params?.assetClass) qp.asset_class = params.assetClass;
    if (params?.staleAfterDays !== undefined)
      qp.stale_after_days = String(params.staleAfterDays);
    return get<{
      as_of: string;
      last_completed_session?: string;
      stale_after_days: number;
      summary: {
        total: number; good: number; bronze: number; partial: number;
        stale: number; missing: number; good_for_today: number;
      };
      symbols: Array<{
        canonical: string;
        asset_class: string;
        score: "GOOD" | "BRONZE" | "PARTIAL" | "STALE" | "MISSING";
        good_for_today: boolean;
        days_behind: number | null;
        provider: string;
        missing_days: number;
        coverage_end: string | null;
        reason: string;
      }>;
    }>("/api/admin/data-trust/bar-cache/quality", qp);
  },
  // Options Desk — wheel candidate screen. Produced by the Mac
  // tradepro-options-screen job (IV-Rank + regime + risk engine) and
  // pushed to /api/options/candidates. Empty until the first screen runs.
  optionsCandidates: () =>
    get<{
      generated_at_utc: string | null;
      market_open: boolean;
      candidates: Array<{
        symbol: string;
        regime: string | null;
        iv_rank: number | null;
        iv: number | null;
        open_interest: number | null;
        spread_usd: number | null;
        eligible: boolean;
        blocks: string[];
        warnings: string[];
        suggested_strike: number | null;
        suggested_delta: number | null;
        suggested_premium: number | null;
      }>;
    }>("/api/options/candidates"),
  runScreener: () =>
    post<{ ok: boolean; skipped?: boolean; reason?: string; result?: { run_date: string; tickers_screened: number; wheel_top: string[]; swing_top: string[]; dual_candidates: string[] }; stderr?: string }, Record<string, never>>("/api/screener/run", {}),
  screenerLive: () =>
    get<{ fetched_at_utc: string; rows: Array<{ ticker: string; price: number | null; change_pct: number | null; change_abs: number | null; ivp_52w: number | null; iv_annual: number | null; hv30: number | null; div_yield: number | null; put_yield_pct: number | null; high_52w: number | null; low_52w: number | null; dist_low_pct: number | null; avg_vol_90d: number | null }> }>("/api/screener/live"),
  // Options Desk — paper wheel positions (BRD §11 ledger). Record a paper
  // CSP entry + the risk-engine verdict, list/track them, transition state.
  optionsPositions: (state?: string) =>
    get<{ positions: OptionsPaperPosition[] }>("/api/options/positions", state ? { state } : undefined),
  recordOptionsPosition: (body: RecordOptionsPositionBody) =>
    post<{ ok: boolean; id: number }, RecordOptionsPositionBody>("/api/options/positions", body),
  optionsPositionEvent: (id: number, body: OptionsPositionEventBody) =>
    post<{ ok: boolean }, OptionsPositionEventBody>(`/api/options/positions/${id}/event`, body),
  deleteOptionsPosition: (id: number) => del<{ ok: boolean }>(`/api/options/positions/${id}`),
  // Position watchdog (v1 F0.1 + BABA addendum) — expiry clock + assignment-
  // risk (moneyness) + a dead-collateral flag per open paper position.
  optionsWatchdog: () =>
    get<{
      generatedAtUtc: string;
      count: number;
      needsAttention: number;
      positions: Array<{
        id: number;
        symbol: string;
        structure: string;
        state: string;
        strike: number | null;
        expiry: string | null;
        daysToExpiry: number | null;
        expiryUrgency: "unknown" | "expired" | "urgent" | "warn" | "ok";
        spot: number | null;
        spotError: string | null;
        distancePct: number | null;
        moneyness: string | null;
        deadCollateral: boolean;
        contracts: number;
        cashSecuredGbp: number | null;
        premium: number | null;
      }>;
    }>("/api/options/watchdog"),
  // Phase F-3 — fill-quality analytics. Empty payload until F-2
  // capture starts landing in production. Sign convention: positive
  // realised_bps = worse than mid (BUY above mid, SELL below mid),
  // negative = price improvement.
  fillQuality: (params?: {
    limit?: number;
    broker?: string;
    sinceDays?: number;
  }) => {
    const qp: Record<string, string | undefined> = {};
    if (params?.limit !== undefined) qp.limit = String(params.limit);
    if (params?.broker) qp.broker = params.broker;
    if (params?.sinceDays !== undefined) qp.sinceDays = String(params.sinceDays);
    return get<{
      window_days: number;
      broker: string | null;
      empty_state: boolean;
      recent_fills: Array<{
        id: number;
        order_id: string;
        broker: string;
        strategy_id: string;
        symbol: string;
        side: string;
        qty: number;
        price: number;
        bid_at_fill: number | null;
        ask_at_fill: number | null;
        mid_at_fill: number | null;
        snapshot_source: string | null;
        fill_at_utc: string;
        snapshot_at_utc: string | null;
        realised_bps: number | null;
      }>;
      per_symbol_aggregates: Array<{
        broker: string;
        symbol: string;
        n_fills: number;
        avg_bps: number | null;
        median_bps: number | null;
        p95_bps: number | null;
        min_bps: number | null;
        max_bps: number | null;
      }>;
    }>("/api/admin/data-trust/fill-quality", qp);
  },
  // Phase P2.1 — IG snapshot harvester status. In-process singleton
  // read so the cockpit's "is the lake filling up" panel renders
  // sub-ms latency. Verdict + ETA fields are what support eyeballs.
  igHarvesterStatus: () =>
    get<{
      verdict: "healthy" | "stale" | "error" | "disabled" | "never_ticked";
      enabled: boolean;
      interval_seconds: number;
      configured_epic_count: number;
      last_tick_at_utc: string | null;
      next_tick_eta_utc: string | null;
      seconds_since_last_tick: number | null;
      seconds_until_next_tick: number | null;
      last_tick_captured: number;
      last_tick_failed: number;
      started_at_utc: string;
      last_error: string | null;
      server_time_utc: string;
    }>("/api/admin/data-trust/ig-snapshots/status"),
  // Phase P2 — recent snapshots + per-symbol aggregates from the
  // ig_l1_snapshots lake. Reads the time-window-filtered view the
  // cockpit panel renders. Empty until the harvester runs at least once.
  igSnapshotsRecent: (params?: {
    symbol?: string;
    limit?: number;
    sinceHours?: number;
  }) => {
    const qp: Record<string, string | undefined> = {};
    if (params?.symbol) qp.symbol = params.symbol;
    if (params?.limit !== undefined) qp.limit = String(params.limit);
    if (params?.sinceHours !== undefined) qp.sinceHours = String(params.sinceHours);
    return get<{
      window_hours: number;
      symbol_filter: string | null;
      empty_state: boolean;
      recent_snapshots: Array<{
        id: number;
        symbol: string;
        epic: string;
        bid: number | null;
        ask: number | null;
        mid: number | null;
        spread_bps: number | null;
        market_status: string | null;
        update_time: string | null;
        captured_at_utc: string;
        source: string;
        error: string | null;
      }>;
      per_symbol_aggregates: Array<{
        symbol: string;
        n_polls: number;
        n_quotes: number;
        avg_spread_bps: number | null;
        min_spread_bps: number | null;
        max_spread_bps: number | null;
        last_seen_utc: string | null;
      }>;
    }>("/api/admin/data-trust/ig-snapshots/recent", qp);
  },
  // IBKR bar-cache harvester status + trigger.
  // GET  /ibkr/status — recent bar_cache_events where provider=ibkr,
  //   connection hint, depth summary. Feeds the IBKR panel in Settings.
  // POST /ibkr/fetch-bars — enqueue an immediate IBKR backfill op for a
  //   specific (canonical, asset_class, resolution, from, to) tuple.
  ibkrBarStatus: (params?: { limit?: number }) => {
    const qp: Record<string, string | undefined> = {};
    if (params?.limit !== undefined) qp.limit = String(params.limit);
    return get<{
      connection_status: string;
      connection_hint: string;
      last_fetch_at_utc: string | null;
      success_count_last_n: number;
      event_count: number;
      valid_resolutions: string[];
      depth_summary: Record<string, string>;
      events: Array<{
        id: number;
        occurred_at_utc: string;
        canonical: string;
        asset_class: string;
        resolution: string;
        range_start_utc: string;
        range_end_utc: string;
        result: string;
        provider_used: string | null;
        rows_returned: number | null;
        rows_expected: number | null;
        latency_ms: number;
        error_class: string | null;
        error_message: string | null;
      }>;
    }>("/api/admin/data-trust/ibkr/status", qp);
  },

  ibkrFetchBars: (payload: {
    canonical: string;
    asset_class: string;
    resolution: string;
    from: string;    // YYYY-MM-DD
    to?: string;     // YYYY-MM-DD; defaults to today server-side
    allow_partial?: boolean;
  }) =>
    post<{
      request_id: string;
      kind: string;
      state: string;
      params: Record<string, unknown>;
      requested_at_utc: string;
      result_summary: unknown;
      error: string | null;
    }, typeof payload>("/api/admin/data-trust/ibkr/fetch-bars", payload),

  // Catalyst registry (Phase C-1). North-star item from project memory
  // — pure-technical signals miss event-driven trades. C-1 is the
  // persistence + visibility surface; the news extractor sink + signal
  // overlay are C-2 / C-3.
  catalysts: (params?: {
    symbol?: string;
    kind?: string;
    lookbackDays?: number;
    lookaheadDays?: number;
    status?: string;
  }) => {
    const qp: Record<string, string | undefined> = {};
    if (params?.symbol) qp.symbol = params.symbol;
    if (params?.kind) qp.kind = params.kind;
    if (params?.lookbackDays !== undefined) qp.lookbackDays = String(params.lookbackDays);
    if (params?.lookaheadDays !== undefined) qp.lookaheadDays = String(params.lookaheadDays);
    if (params?.status) qp.status = params.status;
    return get<{
      count: number;
      window: { back_days: number; ahead_days: number };
      status_filter: string;
      catalysts: Array<{
        id: number;
        symbol: string;
        kind: string;
        occurs_on: string | null;
        title: string;
        source: string;
        severity: "low" | "medium" | "high";
        status: "active" | "expired" | "dismissed";
        surfaced_at_utc: string;
        payload_text: string;
        note: string | null;
        created_at_utc: string;
        updated_at_utc: string;
      }>;
    }>("/api/catalysts/", qp);
  },
  upsertCatalyst: (body: {
    symbol: string;
    kind: string;
    occursOn: string | null;     // YYYY-MM-DD or null when undated
    title: string;
    source: string;
    severity?: "low" | "medium" | "high";
    status?: "active" | "expired" | "dismissed";
    payload?: unknown;
    note?: string | null;
  }) =>
    post<{ id: number; status: string }, unknown>("/api/catalysts/", {
      Symbol: body.symbol,
      Kind: body.kind,
      OccursOn: body.occursOn,
      Title: body.title,
      Source: body.source,
      Severity: body.severity ?? "medium",
      Status: body.status ?? "active",
      Payload: body.payload ?? {},
      Note: body.note ?? null,
    }),
  dismissCatalyst: async (id: number) => {
    const headers = await authHeaders();
    const resp = await fetch(
      new URL(`/api/catalysts/${id}/dismiss`, config.apiBaseUrl),
      { method: "PATCH", headers },
    );
    if (!resp.ok) {
      throw new Error(`${resp.status} ${resp.statusText}: ${await resp.text()}`);
    }
    return resp.json() as Promise<{ id: number; status: string }>;
  },

};

// Shape of the artifact emitted by strategies/cli/equity_pipeline.py
// — kept loose (most charts are arrays of {date, value}) because the
// CLI evolves it and we don't want a schema lockstep. The strategy
// validation page consumes specific paths; everything else is opaque.
export type EquityPipelineEnvelope = {
  strategy: string;
  label: string;
  asOfUtc: string;
  uploadedAtUtc: string;
  uploadedBy: string | null;
  note: string | null;
  artifact: {
    as_of_utc: string;
    config: Record<string, unknown>;
    in_sample: Record<string, number | string>;
    walk_forward: {
      summary: Record<string, number | string>;
      per_window: Array<{
        test_year: string;
        vol_scalar: number;
        sharpe: number;
        cagr_pct: number;
        n_days: number;
      }>;
    };
    spy_benchmark: Record<string, number | string>;
    monte_carlo: {
      n_sims: number;
      years: number;
      initial: number;
      summary: Record<string, unknown>;
      fan_chart: {
        years_axis: number[];
        q05: number[];
        q25: number[];
        q50: number[];
        q75: number[];
        q95: number[];
      };
    } | null;
    charts: {
      equity: Array<{ date: string; value: number }>;
      oos_equity: Array<{ date: string; value: number }>;
      spy_equity: Array<{ date: string; value: number }>;
      drawdown: Array<{ date: string; value: number }>;
      spy_drawdown: Array<{ date: string; value: number }>;
      sleeve_cumulative: Record<string, Array<{ date: string; value: number }>>;
      gross_exposure: Array<{ date: string; value: number }>;
    };
    sleeves_meta: Array<{
      name: string;
      n_tickers: number;
      source: string;
      note?: string;
    }>;
    timings_sec: Record<string, number>;
  };
};

export type OmsOrderRow = {
  id: string;
  clientOrderId: string;
  broker: string;
  brokerOrderId: string | null;
  strategyId: string | null;
  symbol: string;
  side: "BUY" | "SELL";
  qty: number;
  orderType: "MKT" | "LMT" | "STP" | "STP_LMT";
  limitPrice: number | null;
  stopPrice: number | null;
  timeInForce: string;
  state:
    | "PENDING_APPROVAL"
    | "SUBMITTED"
    | "WORKING"
    | "PARTIALLY_FILLED"
    | "FILLED"
    | "CANCELLED"
    | "REJECTED"
    | "EXPIRED";
  placedBy: "HUMAN" | "STRATEGY_AUTO";
  filledQty: number;
  avgFillPrice: number | null;
  cancelledReason: string | null;
  createdAtUtc: string;
  lastStateChangeAtUtc: string;
};

export type OmsOrderEventRow = {
  id: number;
  orderId: string;
  eventType: string;
  priorState: string | null;
  newState: string;
  actor: string;
  detailJson: string | null;
  occurredAtUtc: string;
};

// ── IT admin raw-table row types ───────────────────────────────────
export type AdminEventRow = {
  seq: number;
  event_type: string;
  aggregate_id: string | null;
  payload_text: string;
  occurred_at: string;
};

export type AdminOrderRow = {
  order_id: string;
  correlation_id: string | null;
  strategy_name: string;
  strategy_version: string;
  mode: string;
  broker: string;
  symbol: string;
  side: string;
  quantity: number;
  order_type: string;
  limit_price: number | null;
  bar_at_emit_close: number | null;
  bar_at_emit_time: string | null;
  tag: string | null;
  emitted_at_utc: string;
  risk_decision: string | null;
  risk_reason: string | null;
  risk_decided_at: string | null;
};

export type AdminFillRow = {
  fill_id: number;
  order_id: string;
  broker_order_id: string | null;
  fill_qty: number;
  fill_price: number;
  commission: number;
  filled_at_utc: string;
  bar_at_fill_close: number | null;
  bar_at_fill_time: string | null;
};

export type AdminOmsEventRow = {
  id: number;
  order_id: string;
  event_type: string;
  prior_state: string | null;
  new_state: string;
  actor: string;
  detail_json: string | null;
  occurred_at_utc: string;
};

export type AdminStrategyVersionRow = {
  name: string;
  version: string;
  code_hash: string;
  layer: string;
  description: string;
  registered_at: string;
  deprecated_at: string | null;
};

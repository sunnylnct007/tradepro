import { config } from "../config";
import { getIdToken } from "../firebase";
import type {
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
  runSimulation: (req: SimulationRequest) =>
    post<SimulationResult, SimulationRequest>("/api/simulations/run", req),
  evaluateSignal: (req: SignalRequest) =>
    post<SignalDecision, SignalRequest>("/api/signals/evaluate", req),
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
  // Paper-trading backtest reports — list newest-first + drill into one
  paperBacktestReports: () =>
    get<Array<{
      reportId: string;
      kind: string;
      symbol: string;
      start?: string;
      end?: string;
      entryCount: number;
      receivedAtUtc: string;
    }>>("/api/paper/backtest/reports"),
  paperBacktestReport: (reportId: string) =>
    get<unknown>(`/api/paper/backtest/reports/${encodeURIComponent(reportId)}`),
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
  omsOrders: (states?: string[], limit = 100) => {
    const q: Record<string, string | number> = { limit };
    if (states && states.length) q.states = states.join(",");
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
  syncOmsFromBroker: (broker: string) =>
    post<{
      broker: string;
      adjusted: number;
      adjustments: Array<{ symbol: string; side: string; delta: number; targetQty: number; fromOmsQty: number }>;
    }, { broker: string }>("/api/oms/positions/sync-from-broker", { broker }),

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
        instrumentName: string | null;
        dealId: string | null;
      }>;
    }>("/api/integrations/ig/positions"),

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
  barCacheHealth: (params?: { canonical?: string; assetClass?: string }) => {
    const qp: Record<string, string | undefined> = {};
    if (params?.canonical) qp.canonical = params.canonical;
    if (params?.assetClass) qp.asset_class = params.assetClass;
    return get<{
      health: Array<{
        canonical: string;
        asset_class: string;
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

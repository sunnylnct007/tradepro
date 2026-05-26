import { config } from "../config";
import { getIdToken } from "../firebase";
import type {
  CandleSeries,
  CompareLatestResponse,
  CompareUniverseSummary,
  DocumentEnvelope,
  DocumentSummary,
  HitRateRequest,
  HitRateResult,
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
      sessions: Array<{
        requestId: string;
        kind: string;
        status: string;
        payload: Record<string, unknown>;
        claimedBy: string | null;
        enqueuedAtUtc: string;
        claimedAtUtc: string | null;
        completedAtUtc: string | null;
        resultSummary: Record<string, unknown> | null;
      }>;
    }>("/api/ops/sessions", { kind, limit }),
  runIntraday: (payload: Record<string, unknown>) =>
    post<{
      requestId: string;
      kind: string;
      status: string;
      payload: Record<string, unknown>;
      enqueuedAtUtc: string;
    }, Record<string, unknown>>("/api/ops/run-intraday", payload),
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
  omsApprove: (orderId: string) =>
    post<OmsOrderRow, {}>(
      `/api/oms/orders/${encodeURIComponent(orderId)}/approve`,
      {},
    ),
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

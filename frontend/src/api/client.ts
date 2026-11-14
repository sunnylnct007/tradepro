import { config } from "../config";
import { getIdToken } from "../firebase";
import type {
  CandleSeries,
  DocumentEnvelope,
  DocumentSummary,
  InstrumentSearchResponse,
  StrategyCatalogResponse,
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

// Lifted from types/cockpit.ts when the legacy cockpit types module was deleted
// with the rest of the old app (10 Nov 2026).
export type T212PosResp = {
  enabled: boolean;
  mode: string;
  positionCount: number;
  positions: Array<{
    ticker: string;
    yahooSymbol: string | null;
    quantity: number;
    averagePricePaid: number | null;
    currentPrice: number | null;
    unrealisedPct: number | null;
    unrealisedAbs: number | null;
    currency: string | null;
    // Position open date (broker-reported) — when the entry was first taken.
    createdAt: string | null;
  }>;
  error?: string | null;
  fromCache?: boolean;
  ageSeconds?: number;
};

export const api = {
  health: () => get<{ status: string }>("/health"),
  // Strangle decision history — every evaluation INCLUDING stand-asides, which
  // are the rows that show whether the volatility gate is set correctly.
  strangleDecisions: (days = 30, market = "") =>
    get<{ rows: Record<string, unknown>[] }>(
      `/api/strangle-decisions?days=${days}${market ? `&market=${market}` : ""}`),
  // Desk statistics. `caveats` is not decoration — it carries what the sample
  // cannot support, and at small n it is the most important field here.
  // Live desk P&L. Carries its OWN mark timestamp — a P&L without one is a
  // number of unknown age.
  strangleLivePnl: (days = 1) =>
    get<Record<string, unknown>>(`/api/strangle-decisions/pnl?days=${days}`),
  strangleStats: (days = 90) =>
    get<Record<string, unknown>>(`/api/strangle-decisions/stats?days=${days}`),
  providers: () => get<{ providers: string[] }>("/api/marketdata/providers"),
  strategies: () => get<StrategyCatalogResponse>("/api/simulations/strategies"),
  candles: (params: { symbol: string; provider?: string; interval?: string; from?: string; to?: string }) =>
    get<CandleSeries>("/api/marketdata/candles", params),
  /** Live market-data probe. IBKR grants ONE market-data session per account,
   *  so opening the IBKR portal or TWS silently takes it from the desk: auth
   *  stays valid, contracts still resolve, prices go dark. This lets the UI
   *  say so and offer a retry once the portal is closed.
   *
   *  NOTE the "N/A" handling — IBKR returns that literal STRING for a field it
   *  cannot serve, and treating it as a value is exactly how the health probe
   *  certified a full-day outage as healthy (18 Aug 2026). */
  ibkrMarketDataCheck: async (symbol = "SPY") => {
    const snap = await get<{
      live?: boolean;
      asOf?: string;
      source?: string;
      marketData?: { state?: string; reason?: string | null };
      quote?: { last?: number | null } | null;
      snapshot?: Record<string, unknown>;
      previousClose?: number | null;
    }>("/api/integrations/ibkr/quote", { symbol, fields: "31,7283,6509" });

    // `live` and the reason come FROM THE SERVER now. This used to re-implement
    // the "N/A" predicate here in TypeScript, which made three copies of the
    // same rule — the Python health probe, this, and (as of today) the C#
    // endpoint. Three copies of a rule is three chances for one of them to be
    // updated alone, which is the failure mode that has cost this codebase more
    // than any other. The server decides; the UI renders what it is told.
    const s = snap?.snapshot ?? {};
    const availabilityRaw = s["6509"];
    const availability =
      availabilityRaw === null || availabilityRaw === undefined
        ? null
        : String(availabilityRaw).trim() || null;

    return {
      live: snap?.live === true,
      // live | closed | halted | dark. "closed" and "dark" both mean no live
      // price and mean entirely different things — the market being shut is
      // normal and must not be rendered as a fault.
      state: snap?.marketData?.state ?? (snap?.live === true ? "live" : "dark"),
      previousClose:
        snap?.previousClose != null ? String(snap.previousClose) : null,
      last: snap?.quote?.last != null ? String(snap.quote.last) : null,
      // Plain-English cause when dark — names the one-market-data-session
      // contention rather than leaving the reader to chase an entitlement
      // problem that does not exist.
      reason: snap?.marketData?.reason ?? null,
      asOf: snap?.asOf ?? null,
      source: snap?.source ?? null,
      availability,
      symbol,
    };
  },

  /** Whether TradePro has stood down from the IBKR session (portal login). */
  ibkrPauseState: () =>
    get<{ paused: boolean; pausedAtUtc: string | null; reason: string | null }>("/api/integrations/ibkr/pause"),
  /** Pause ALL TradePro IBKR usage + log the session out, so the user can log
   * into the IBKR Client Portal (only one Web-API session per account). */
  ibkrPause: (reason?: string) =>
    post<{ paused: boolean; note: string }, Record<string, never>>(
      `/api/integrations/ibkr/pause${reason ? `?reason=${encodeURIComponent(reason)}` : ""}`, {}),
  ibkrResume: () =>
    post<{ paused: boolean; note: string }, Record<string, never>>("/api/integrations/ibkr/resume", {}),
  watchlists: () => get<{ names: string[] }>("/api/watchlists/"),
  searchInstruments: (q: string, limit = 10) =>
    get<InstrumentSearchResponse>("/api/instruments/search", { q, limit }),
  documents: (symbol?: string) =>
    get<{ documents: DocumentSummary[] }>("/api/documents",
      symbol ? { symbol } : undefined),
  document: (docId: string) =>
    get<DocumentEnvelope>(`/api/documents/${encodeURIComponent(docId)}`),
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
  // Broker-reported REALISED P&L per day (golden source; nets spread +
  // financing). The honest "what did we actually make/lose each day" the OMS
  // can't give (pre-2026-06-02 IG fills were booked at price 0).
  accountValueHistory: (days = 30) =>
    get<{
      from: number;
      error?: string | null;
      points: Array<{ broker: string; date: string; currency: string | null; value: number }>;
    }>("/api/account-value/history", { days }),
  // Per-strategy P&L comparison — one row per strategy, per native currency.
  // Any field null = genuinely not available (reason in `notes`), never a guess.
  // The paper P&L LEDGER — broker-confirmed fills by placement record;
  // unattributed as its own line. PaperLedger.cs / GET /api/pnl/ledger.
  paperLedger: () =>
    get<{
      utc: string;
      account: string;
      error?: string | null;
      basis: string;
      fillsSeen: number;
      confirmedFills: number;
      assumedFills: number;
      unexplainedBrokerPositions: number | null;
      unexplainedBrokerPositionsError?: string | null;
      strategies: Array<{
        key: string; strategyId: string | null; broker: string; currency: string;
        moneyComputable: boolean;
        realisedLtd: number | null; realisedToday: number | null;
        closedTrades: number; winningTrades: number; winRatePct: number | null;
        unmatchedSellQty: number; confirmedFills: number; assumedFills: number; assumedQty: number;
        symbols: number; firstFillAtUtc: string | null; lastFillAtUtc: string | null;
        notes: string[];
      }>;
      unattributed: Array<{
        key: string; strategyId: string | null; broker: string; currency: string;
        moneyComputable: boolean;
        realisedLtd: number | null; realisedToday: number | null;
        closedTrades: number; winningTrades: number; winRatePct: number | null;
        unmatchedSellQty: number; confirmedFills: number; assumedFills: number; assumedQty: number;
        symbols: number; firstFillAtUtc: string | null; lastFillAtUtc: string | null;
        notes: string[];
      }>;
    }>("/api/pnl/ledger"),
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
  /** The post-earnings put screen's published artifact. Raw JSONB from the
   *  ingest endpoint, so new fields flow through without a backend change. */
  postEarningsPuts: () =>
    get<{ universe: string; label: string; asOfUtc: string; artifact: any }>(
      "/api/today-setups/post_earnings_puts/latest"),

  /** Trigger the pre-earnings engine on demand (runs in Lambda, ~seconds). */
  runPreEarningsWatch: () =>
    post<{ ok?: boolean; job?: string; error?: string }, Record<string, never>>(
      "/api/jobs/preearnings_watch/run", {}),

  /** A published board by name (momentum_rotation, live-book-risk, …). */
  todaySetupsArtifact: (universe: string) =>
    get<{ universe: string; label: string; asOfUtc: string; artifact: any }>(
      `/api/today-setups/${universe}/latest`),

  /** Pre-earnings watch (per-symbol spec engine, Phase 1 alerts-only). */
  preEarningsCandidates: () =>
    get<{ universe: string; label: string; asOfUtc: string; artifact: any }>(
      "/api/today-setups/preearnings/latest"),

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

  // T212 open positions (equity). account = demo | live.
  t212Positions: (account: "demo" | "live" = "demo") =>
    get<T212PosResp>(
      "/api/integrations/trading212/positions", { account }),

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

  omsMode: () => get<{ mode: string }>("/api/oms/mode"),
  setOmsMode: (mode: "auto" | "manual") =>
    post<{ mode: string; prior: string }, { Mode: string }>(
      "/api/oms/mode",
      { Mode: mode },
    ),

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
  /** CURRENT fundamentals per symbol (migration 067).
   *
   * NOTE the "/api/" prefix. `get()` does `new URL(path, apiBaseUrl)` and a
   * LEADING SLASH makes the path absolute, discarding any base path — so
   * "/fundamentals" resolved to https://host/fundamentals, 404'd, was swallowed
   * by the caller's catch, and every P/E and ROE cell rendered "—" against a
   * database that had all 244 rows. Every other call here is "/api/...";
   * this one was not, and the failure looked exactly like "no data".
   *
   * Owner, 29 Aug 2026: "this figure shd be visible on our data harvesting
   * screen as well". A snapshot for a HUMAN deciding today — never a backtest
   * input, because today's P/E on a past date is look-ahead.
   *
   * One call for the whole store rather than per-symbol lookups behind a table
   * of 244 rows. `lastHarvestUtc` lets the screen distinguish "not harvested
   * yet" from "this company has no P/E", which for an ETF is the truth. */
  fundamentals: () =>
    get<{
      count: number;
      lastHarvestUtc: string | null;
      note: string;
      fundamentals: {
        symbol: string;
        as_of: string;
        source: string | null;
        trailingpe: number | null;
        forwardpe: number | null;
        pricetobook: number | null;
        returnonequity: number | null;
        profitmargin: number | null;
        debttoequity: number | null;
        trailingeps: number | null;
        forwardeps: number | null;
        marketcap: number | null;
        annualeps: string | null;
      }[];
    }>("/api/fundamentals"),

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
  /** The live option chain for one symbol — real contracts, real quotes.
   *  Used by the spread builder, which has to price strikes the screen did
   *  not pick. Quote fields are null outside market hours; the caller must
   *  say so rather than render a spread it cannot price. */
  optionChain: (symbol: string, opts?: { expiry?: string; targetDte?: number }) =>
    get<{
      symbol: string;
      spot: number | null;
      month: string | null;
      availableExpiries: string[] | null;
      error?: string | null;
      legs: Array<{
        conId: number;
        strike: number;
        right: string;
        bid: number | null;
        ask: number | null;
        last: number | null;
        delta: number | null;
        theta: number | null;
        impliedVolPct: number | null;
        openInterest: number | null;
        priorClose: number | null;
        maturityDate: string | null;
      }>;
    }>(`/api/ibkr/chain/${encodeURIComponent(symbol)}`, {
      right: "P",
      ...(opts?.expiry ? { expiry: opts.expiry } : {}),
      ...(opts?.targetDte ? { targetDte: opts.targetDte } : {}),
    }),

  optionsCandidates: () =>
    get<{
      generated_at_utc: string | null;
      market_open: boolean;
      candidates: Array<{
        symbol: string;
        /** The structure the engine actually decided on. Until 21 Oct 2026
         *  every row was implicitly a cash-secured put and the screen never
         *  said so, which is why a capped trade and an uncapped one were
         *  indistinguishable on the board. */
        structure?: string | null;
        /** Why it is not the structure we first wanted — regime, an earnings
         *  print, or both. Empty on the plain path. */
        structure_notes?: string[] | null;
        /** Max gain / max loss / breakeven in dollars. NULL when a figure
         *  could not be derived (see `economics_note`) — never a placeholder,
         *  because a guessed max loss on a defined-risk structure defeats the
         *  point of the structure. */
        economics?: {
          structure: string;
          short_strike: number;
          long_strike: number | null;
          long_debit?: number;
          long_abs_delta?: number;
          width_usd?: number;
          max_gain_usd: number;
          max_loss_usd: number;
          breakeven: number;
          capital_usd: number;
          return_on_risk_pct: number;
          unbounded: boolean;
          why: string;
        } | null;
        economics_note?: string | null;
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
        /** Known dated events between today and this expiry, NAMED
         *  (OWNER BASICS 3). Absent on artifacts produced before 10 Nov 2026. */
        events?: {
          events: Array<{ kind: string; label: string; on: string; title: string;
                          scope: string; source: string; severity: string; short: string }>;
          count: number; high: number; verified: boolean; gaps: string[]; names: string;
        } | null;
      }>;
    }>("/api/options/candidates"),
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

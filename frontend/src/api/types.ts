export interface Candle {
  timestamp: string;
  open: number;
  high: number;
  low: number;
  close: number;
  adjustedClose: number | null;
  volume: number;
}

export interface CandleSeries {
  symbol: string;
  interval: string;
  provider: string;
  candles: Candle[];
}

export interface Trade {
  timestamp: string;
  side: "BUY" | "SELL";
  price: number;
  quantity: number;
  fees: number;
  reason: string;
}

export interface EquityPoint {
  timestamp: string;
  equity: number;
  cash: number;
  position: number;
}

export interface SimulationResult {
  symbol: string;
  strategy: string;
  currency: string;
  initialCapital: number;
  finalEquity: number;
  totalReturnPct: number;
  cagrPct: number;
  maxDrawdownPct: number;
  sharpeRatio: number;
  tradeCount: number;
  trades: Trade[];
  equityCurve: EquityPoint[];
}

export interface FeeModel {
  commissionPerTrade: number;
  stampDutyRate: number;
  fxSpread: number;
}

export interface SimulationRequest {
  symbol: string;
  provider?: string;
  strategy: string;
  from: string;
  to: string;
  initialCapital: number;
  currency: string;
  fees?: FeeModel | null;
  params?: Record<string, number> | null;
}

export interface WatchlistItem {
  symbol: string;
  label: string;
  kind: string;
}

export interface Watchlist {
  name: string;
  currency: string;
  region: string;
  items: WatchlistItem[];
}

export interface IndicatorSnapshot {
  sma20: number | null;
  sma50: number | null;
  sma200: number | null;
  rsi14: number | null;
  lastClose: number | null;
  priceVs52wHighPct: number | null;
  priceVs52wLowPct: number | null;
}

export interface SignalDecision {
  symbol: string;
  strategy: string;
  asOf: string;
  action: "BUY" | "SELL" | "HOLD";
  confidence: number;
  reasons: string[];
  indicators: IndicatorSnapshot;
  suggestedStopLossPct: number | null;
  suggestedTargetPct: number | null;
}

export interface SignalRequest {
  symbol: string;
  provider?: string;
  strategy: string;
  lookbackDays: number;
  params?: Record<string, number> | null;
}

export interface ScanRequest {
  watchlist?: string | null;
  symbols?: string[] | null;
  provider?: string | null;
  strategy: string;
  params?: Record<string, number> | null;
}

export interface ScanResultItem {
  symbol: string;
  label: string;
  decision: SignalDecision;
}

export interface ScanResult {
  watchlist: string;
  strategy: string;
  generatedAt: string;
  buys: ScanResultItem[];
  sells: ScanResultItem[];
  holds: ScanResultItem[];
  errors: string[];
}

export type TimeHorizon = "Intraday" | "Short" | "Mid" | "Long" | "Any";

export interface StrategyMetadata {
  name: string;
  displayName: string;
  oneLiner: string;
  bestIn: string;
  worstIn: string;
  horizon: TimeHorizon;
  horizonText: string;
  defaultParams: Record<string, number> | null;
  paramKeys: string[] | null;
}

export interface StrategyCatalogResponse {
  strategies: string[];
  catalog: StrategyMetadata[];
}

export interface HitRateRequest {
  symbol: string;
  provider?: string | null;
  strategy: string;
  lookbackYears: number;
  params?: Record<string, number> | null;
}

export interface HitRateTrade {
  entryDate: string;
  exitDate: string | null;
  entryPrice: number;
  exitPrice: number | null;
  returnPct: number | null;
  holdingDays: number | null;
  isOpen: boolean;
}

// ---- Compare (ETF ranking pushed in from the local Mac) ----------------
// The .NET API wraps the raw Python comparator JSON in an envelope; the
// frontend reads both: envelope fields are camelCase (.NET), the inner
// payload keeps the snake_case it was generated with on the Mac.

export type EntrySignal = "BUY" | "HOLD" | "WAIT" | "AVOID";

export interface CompareMarketState {
  symbol: string;
  as_of: string | null;
  last_price: number | null;
  sma_200: number | null;
  above_sma_200: boolean | null;
  pct_off_52w_high_pct: number | null;
  drawdown_from_peak_pct: number | null;
  rsi_14: number | null;
  momentum_3m_pct: number | null;
  momentum_12m_pct: number | null;
  vol_30d_annual_pct: number | null;
  entry_signal: EntrySignal;
  entry_reason: string;
  decision_trace?: DecisionCheck[];
}

export interface DecisionCheck {
  name: string;
  status: "pass" | "warn" | "fail";
  detail: string;
}

export interface CompareMarketContext {
  as_of: string | null;
  vix: number | null;
  vix_regime: "calm" | "normal" | "stressed" | null;
  tnx: number | null;
  tnx_change_30d: number | null;
  tnx_trend: "rising" | "falling" | "flat" | null;
  spy_drawdown_pct: number | null;
  active_stress_regimes: string[];
  summary: string;
}

export interface CompareRowRegime {
  key: string;
  name: string;
  kind: string;
  bars: number;
  return_pct: number | null;
  max_drawdown_pct: number | null;
}

export interface CompareExternalConsensus {
  symbol: string;
  fetched_at: string;
  /** Yahoo recommendationKey: strong_buy / buy / hold / underperform / sell / strong_sell — null for unrated tickers (typically ETFs). */
  rating_key: string | null;
  rating_label: string | null;
  /** 1.0 (strong buy) to 5.0 (strong sell). */
  rating_mean: number | null;
  n_analysts: number | null;
  target_mean: number | null;
  target_median: number | null;
  target_high: number | null;
  target_low: number | null;
  current_price: number | null;
  /** +12% means the analyst mean target is 12% above current. Null for unrated. */
  target_vs_current_pct: number | null;
  source: string;
}

export interface CompareTopHolding {
  symbol: string | null;
  name: string;
  weight_pct: number | null;
}

export interface CompareFundamentals {
  symbol: string;
  fetched_at: string;
  fund_family: string | null;
  category: string | null;
  legal_type: string | null;
  inception_date: string | null;
  expense_ratio_pct: number | null;
  aum_usd: number | null;
  dividend_yield_pct: number | null;
  distribution_yield_pct: number | null;
  ytd_return_pct: number | null;
  three_year_return_pct: number | null;
  five_year_return_pct: number | null;
  yield_to_maturity_pct: number | null;
  duration_years: number | null;
  top_holdings: CompareTopHolding[];
  sector_weights: Record<string, number>;
  summary: string | null;
  source: string;
}

export interface CompareNewsItem {
  title: string;
  publisher: string | null;
  link: string | null;
  published_at: string | null;
  thumbnail: string | null;
}

export interface CompareRow {
  symbol: string;
  strategy: string;
  strategy_label: string;
  params: Record<string, number>;
  bars: number;
  stats: Record<string, number | null>;
  regimes: CompareRowRegime[];
  current_action: "BUY" | "SELL" | "HOLD";
  latest_signal: number;
  latest_bar: string | null;
  /** True if this strategy's most recent fired signal was BUY (i.e. it would
   * currently be holding the asset). Buy-and-hold is always true after the
   * first bar. Used for >50% strategy consensus voting. */
  in_position: boolean;
  position_since: string | null;
  market_state: CompareMarketState;
  external_consensus?: CompareExternalConsensus;
  fundamentals?: CompareFundamentals;
  news?: CompareNewsItem[];
  rank: number;
  error: string | null;
}

export interface ComparePayload {
  kind: string;
  generated_at: string;
  from: string;
  to: string;
  provider: string;
  currency: string;
  rank_metric: string;
  universe?: string;
  run_id?: string;
  symbols: string[];
  strategies: { name: string; params: Record<string, number>; label: string }[];
  regimes: {
    key: string;
    name: string;
    kind: string;
    start: string;
    end: string;
    description: string;
  }[];
  rows: CompareRow[];
  best_per_strategy: Record<string, { symbol: string; rank: number }>;
  best_overall: {
    symbol: string;
    strategy: string;
    rank_metric: string;
    value: number | null;
  } | null;
  market_context?: CompareMarketContext;
}

export interface CompareUniverseSummary {
  universe: string;
  runId: string | null;
  generatedAtUtc: string;
  receivedAtUtc: string;
  rankMetric: string | null;
  rowCount: number;
}

export interface CompareLatestResponse {
  universe: string;
  runId: string | null;
  generatedAtUtc: string;
  receivedAtUtc: string;
  rankMetric: string | null;
  rowCount: number;
  payload: ComparePayload;
}

// ---- Mac liveness signal --------------------------------------------------

export type WorkerLiveness = "alive" | "late" | "down";

export interface WorkerCurrentTask {
  task: string;
  detail: string | null;
  phase: string | null;
  startedAtUtc: string | null;
  elapsedSeconds: number | null;
}

export interface WorkerHealth {
  liveness: WorkerLiveness;
  sinceLastPingSeconds: number | null;
  isProcessing: boolean;
  summary: string;
  host?: string;
  gitSha?: string;
  sentAtUtc?: string;
  receivedAtUtc?: string;
  uptimeSeconds?: number | null;
  currentTask?: WorkerCurrentTask | null;
  payload?: unknown;
}

export interface HitRateResult {
  symbol: string;
  strategy: string;
  from: string;
  to: string;
  totalTrades: number;
  winners: number;
  losers: number;
  winRatePct: number;
  avgWinnerPct: number;
  avgLoserPct: number;
  medianHoldingDays: number;
  bestPct: number;
  worstPct: number;
  expectancyPct: number;
  totalReturnPct: number;
  trades: HitRateTrade[];
}

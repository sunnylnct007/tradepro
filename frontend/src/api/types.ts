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
  /** Where the current price sits as a percentile within the 52w
   * (low → high) range. 100 = at high, 0 = at low. Used as a guard
   * on the BUY signal: ≥70th pctile downgrades BUY → HOLD even when
   * the technical gates pass. Captures the "near the highs after a
   * strong run, not a dip" case. */
  range_position_pct?: number | null;
  low_52w_price?: number | null;
  low_52w_date?: string | null;
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
  /** -1.0 to 1.0; null if scoring failed for this headline. */
  sentiment?: number | null;
  sentiment_themes?: string[];
  sentiment_material?: boolean;
  sentiment_model?: string | null;
  sentiment_error?: string | null;
}

export interface CompareSentimentSummary {
  items_considered: number;
  mean_sentiment: number | null;
  very_negative_count: number;
  material_negative_count: number;
  most_negative: string | null;
}

// ---- Documents (Phase 5c-iii) ---------------------------------------------

export interface DocumentSummary {
  docId: string;
  title: string;
  sourceUrl: string | null;
  linkedSymbols: string[];
  fileKind: string;
  charCount: number;
  pageCount: number | null;
  uploadedAtUtc: string;
  receivedAtUtc: string;
}

export interface DocumentSection {
  heading: string | null;
  text: string;
  page: number | null;
}

export interface DocumentEnvelope {
  docId: string;
  title: string;
  sourceUrl: string | null;
  linkedSymbols: string[];
  fileKind: string;
  sha256: string;
  charCount: number;
  pageCount: number | null;
  extractedAtUtc: string;
  extractor: string;
  uploadedAtUtc: string;
  receivedAtUtc: string;
  uploader: string | null;
  sections: DocumentSection[];
}

export type SentimentStatus =
  | "scored"
  | "partial"
  | "all_failed"
  | "no_news"
  | "provider_down";

export interface CompareLlmTelemetry {
  calls_attempted: number;
  calls_succeeded: number;
  calls_failed: number;
  cache_hits: number;
  cache_misses: number;
  avg_latency_ms: number | null;
  max_latency_ms: number | null;
  total_scored: number;
}

export interface CompareLlmInfo {
  provider: string;
  model: string;
  healthy: boolean;
  prompt_version: string;
  demotion_rule: {
    mean_sentiment_threshold: number;
    min_material_negative_count: number;
    lookback_days: number;
    description: string;
    source?: string;
    settings_updated_at?: string | null;
  };
  telemetry?: CompareLlmTelemetry;
}

/** Plain-English rationale for a symbol's verdict.
 * `source` distinguishes LLM-generated (verified) prose from
 * deterministic template fallbacks. The UI shows the badge so a user
 * knows whether what they're reading was AI-written or built
 * mechanically from the input facts. */
export interface CompareRationale {
  summary: string;
  key_factors?: string[];
  caveats?: string[];
  /** Horizon-specific one-sentence rationales — TRADEPRO-SPEC-001 §7.
   * Optional because cached v1 entries pre-spec won't have them; the
   * UI should fall back to the unified `summary`. */
  swing_rationale?: string | null;
  long_term_rationale?: string | null;
  passive_rationale?: string | null;
  source?:
    | "llm"
    | "template"
    | "template_no_llm"
    | "template_llm_failed"
    | "template_empty_llm"
    | "template_llm_unverified";
  model?: string | null;
  prompt_version?: string;
  verified?: boolean;
  verification_notes?: string[];
  generated_at?: string | null;
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
  sentiment_summary?: CompareSentimentSummary;
  sentiment_status?: SentimentStatus;
  rationale?: CompareRationale;
  bucket?: "BUY" | "WAIT" | "AVOID";
  bucket_reason?: string;
  sentiment_demoted?: boolean;
  /** ISO currency code derived from the ticker venue (.L=GBP, no suffix=USD, …). */
  currency?: string;
  /** How many days behind the requested `to` date the latest bar is. 0 means
   * we have a bar for today; >7 means the price feed is stale for this row. */
  data_age_days?: number | null;
  rank: number;
  error: string | null;
  /** Family-3 signal: rank + zscore vs basket peers on 12-month return.
   * Annotation only — does not yet feed the bucket vote. Distinguishes
   * "this symbol is strong" from "the whole basket is strong". */
  cross_sectional_momentum?: CrossSectionalMomentum | null;
  /** Family-2 starter: cheap / fair / expensive based on dividend-yield
   * quartile vs basket peers. Proxy until we have a fundamentals
   * snapshot store with historical-P/E-vs-10y-median. */
  valuation_flag?: ValuationFlag | null;
  /** Phase-X composite swing-trade score 0-8 across four families.
   * Verdict bands: ≥6 STRONG_BUY, 4-5 BUY, 2-3 HOLD, 0-1 AVOID.
   * Computed AFTER all per-family annotations are attached. */
  swing_score?: SwingScore | null;
  /** TRADEPRO-SPEC-001 §6.2: three horizon-aware verdicts per row.
   * Annotation only — does not modify the bucket vote. Single-stock
   * symbols carry signal "N/A" on the passive horizon. */
  horizon_classification?: HorizonClassification | null;
  /** Mirror of market_state.range_position_pct surfaced at the row
   * top-level for convenience. 0-100; 100 = at 52w high. */
  range_pct?: number | null;
  /** Phase R: risk rating + audit trail. Every BUY/WAIT/AVOID
   * carries this so the user can size positions per rating. */
  risk_rating?: RiskRating | null;
}

export interface CrossSectionalMomentum {
  metric_name: string;
  value: number | null;
  rank: number | null;
  rank_pct: number | null;
  zscore: number | null;
  peer_count: number | null;
  basket_mean: number | null;
  basket_median: number | null;
  is_top_quartile: boolean;
}

export interface ValuationFlag {
  flag: "cheap" | "fair" | "expensive" | "n/a";
  /** Generic value (P/E ratio when lens=pe, yield % when lens=yield). */
  value?: number | null;
  basket_median?: number | null;
  /** Which lens picked this output. "pe" for stock baskets where
   * forward P/E was available across the basket; "yield" for ETF
   * baskets falling back to dividend yield. */
  lens_used?: "pe" | "yield";
  /** Legacy yield-specific fields — populated only when lens=yield.
   * Kept for backwards compat with renderers that read them
   * directly. */
  yield_pct: number | null;
  basket_median_yield_pct: number | null;
  /** P/E-specific mirrors — populated only when lens=pe. */
  pe_ratio?: number | null;
  basket_median_pe?: number | null;
  basis: string;
  metric: string;
}

export interface HorizonVerdict {
  signal: "BUY" | "WATCH" | "AVOID" | "N/A";
  /** "5/8" or "N/A" */
  score: string;
  /** Human-readable window: "1-8 weeks" / "6-18 months" / "3-5 years" */
  horizon: string;
  reasons: string[];
  entry_note?: string | null;
  raw_score?: number | null;
}

/** TRADEPRO-SPEC-001 — three independent horizon verdicts per row.
 * Same instrument scored against three different lenses (swing /
 * long-term / passive). Annotation only; doesn't override the
 * existing bucket vote. Single-stock symbols return signal "N/A"
 * on the passive horizon. */
export interface HorizonClassification {
  swing: HorizonVerdict;
  long_term: HorizonVerdict;
  passive: HorizonVerdict;
  range_pct: number | null;
}

/** Phase R risk rating attached to every compare row. The `rating`
 * is the headline pill (LOW / MEDIUM / HIGH / EXTREME). `baseline`
 * is the vol-only tier before escalators applied. `factors` is the
 * audit trail — every input that drove the rating, in plain English. */
export interface RiskRating {
  rating: "LOW" | "MEDIUM" | "HIGH" | "EXTREME";
  baseline: "LOW" | "MEDIUM" | "HIGH" | "EXTREME";
  escalators: number;
  factors: string[];
}

export interface SwingScore {
  /** 0-8, sum of 0-2 from each layer. */
  total: number;
  verdict: "STRONG_BUY" | "BUY" | "HOLD" | "AVOID";
  layers: {
    quality: number;
    valuation: number;
    event: number;
    price: number;
  };
  /** Per-layer one-liner explaining how that score was reached. */
  reasons: Record<string, string>;
}

export interface CompareCurrencyMix {
  is_mixed: boolean;
  primary: string;
  currencies: string[];
}

export interface CompareError {
  symbol: string;
  stage: string;
  error: string;
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
  currency_mix?: CompareCurrencyMix;
  errors?: CompareError[];
  llm?: CompareLlmInfo;
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

export interface InstrumentMatch {
  symbol: string;
  name: string;
  exchange: string | null;
  type: string | null;
  currency: string | null;
  source: string;
}

export interface InstrumentSearchResponse {
  query: string;
  count: number;
  items: InstrumentMatch[];
}

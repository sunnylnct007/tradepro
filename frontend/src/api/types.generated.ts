// AUTO-GENERATED — do not edit by hand.
// Regenerate with: uv run python tools/gen_ts_types.py
// Generated at:   2026-05-02T22:25:27.416069+00:00
// Source:         tradepro_strategies.schema (Pydantic)

export const SCHEMA_VERSION = '1.0.0';

export interface CompareBest {
  symbol: string;
  strategy: string;
  rank_metric: string;
  value?: number | null;
}

export interface CompareCurrencyMix {
  is_mixed: boolean;
  primary: string;
  currencies?: string[];
}

export interface CompareError {
  symbol: string;
  stage: string;
  error: string;
}

export interface CompareLlmDemotionRule {
  mean_sentiment_threshold: number;
  min_material_negative_count: number;
  lookback_days: number;
  description: string;
}

export interface CompareLlmInfo {
  provider: string;
  model: string;
  healthy: boolean;
  prompt_version: string;
  demotion_rule: CompareLlmDemotionRule;
  telemetry?: CompareLlmTelemetry | null;
}

export interface CompareLlmTelemetry {
  calls_attempted?: number;
  calls_succeeded?: number;
  calls_failed?: number;
  cache_hits?: number;
  cache_misses?: number;
  avg_latency_ms?: number | null;
  max_latency_ms?: number | null;
  total_scored?: number;
}

export interface ComparePayload {
  schema_version?: string;
  kind?: 'compare';
  generated_at: string;
  from: string;
  to: string;
  provider: string;
  currency: string;
  rank_metric: string;
  universe?: string | null;
  run_id?: string | null;
  symbols?: string[];
  strategies?: CompareStrategySpec[];
  regimes?: RegimeSpec[];
  market_context?: MarketContext | null;
  currency_mix?: CompareCurrencyMix | null;
  llm?: CompareLlmInfo | null;
  rows?: CompareRow[];
  errors?: CompareError[];
  best_per_strategy?: Record<string, Record<string, unknown>>;
  best_overall?: CompareBest | null;
}

export interface CompareRow {
  symbol: string;
  strategy: string;
  strategy_label: string;
  params?: Record<string, number>;
  bars?: number;
  stats?: Record<string, number | null>;
  regimes?: RegimeRow[];
  current_action: 'BUY' | 'SELL' | 'HOLD';
  latest_signal?: number;
  latest_bar?: string | null;
  in_position?: boolean;
  position_since?: string | null;
  market_state: MarketState;
  external_consensus?: ExternalConsensus | null;
  fundamentals?: Fundamentals | null;
  news?: NewsItem[];
  sentiment_summary?: SentimentSummary | null;
  sentiment_status?: 'scored' | 'partial' | 'all_failed' | 'no_news' | 'provider_down' | null;
  rationale?: Rationale | null;
  bucket?: 'BUY' | 'WAIT' | 'AVOID' | null;
  bucket_reason?: string | null;
  sentiment_demoted?: boolean;
  currency?: string | null;
  data_age_days?: number | null;
  rank?: number;
  error?: string | null;
}

export interface CompareStrategySpec {
  name: string;
  params?: Record<string, number>;
  label: string;
}

export interface DecisionCheck {
  name: string;
  status: 'pass' | 'warn' | 'fail';
  detail: string;
}

export interface ExternalConsensus {
  symbol: string;
  fetched_at: string;
  rating_key?: string | null;
  rating_label?: string | null;
  rating_mean?: number | null;
  n_analysts?: number | null;
  target_mean?: number | null;
  target_median?: number | null;
  target_high?: number | null;
  target_low?: number | null;
  current_price?: number | null;
  target_vs_current_pct?: number | null;
  source?: string;
}

export interface Fundamentals {
  symbol: string;
  fetched_at: string;
  fund_family?: string | null;
  category?: string | null;
  legal_type?: string | null;
  inception_date?: string | null;
  expense_ratio_pct?: number | null;
  aum_usd?: number | null;
  dividend_yield_pct?: number | null;
  distribution_yield_pct?: number | null;
  ytd_return_pct?: number | null;
  three_year_return_pct?: number | null;
  five_year_return_pct?: number | null;
  yield_to_maturity_pct?: number | null;
  duration_years?: number | null;
  top_holdings?: TopHolding[];
  sector_weights?: Record<string, number>;
  summary?: string | null;
  source?: string;
}

export interface MarketContext {
  as_of?: string | null;
  vix?: number | null;
  vix_regime?: 'calm' | 'normal' | 'stressed' | null;
  tnx?: number | null;
  tnx_change_30d?: number | null;
  tnx_trend?: 'rising' | 'falling' | 'flat' | null;
  spy_drawdown_pct?: number | null;
  active_stress_regimes?: string[];
  summary?: string;
}

export interface MarketState {
  symbol: string;
  as_of?: string | null;
  last_price?: number | null;
  sma_200?: number | null;
  above_sma_200?: boolean | null;
  pct_off_52w_high_pct?: number | null;
  drawdown_from_peak_pct?: number | null;
  rsi_14?: number | null;
  momentum_3m_pct?: number | null;
  momentum_12m_pct?: number | null;
  vol_30d_annual_pct?: number | null;
  entry_signal: 'BUY' | 'HOLD' | 'WAIT' | 'AVOID';
  entry_reason: string;
  decision_trace?: DecisionCheck[];
}

export interface NewsItem {
  title: string;
  publisher?: string | null;
  link?: string | null;
  published_at?: string | null;
  thumbnail?: string | null;
  sentiment?: number | null;
  sentiment_themes?: string[];
  sentiment_material?: boolean;
  sentiment_model?: string | null;
  sentiment_error?: string | null;
}

export interface Rationale {
  summary: string;
  key_factors?: string[];
  caveats?: string[];
  source?: 'llm' | 'template' | 'template_no_llm' | 'template_llm_failed' | 'template_empty_llm' | 'template_llm_unverified';
  model?: string | null;
  prompt_version?: string;
  verified?: boolean;
  verification_notes?: string[];
  generated_at?: string | null;
}

export interface RegimeRow {
  key: string;
  name: string;
  kind: 'crash' | 'drawdown' | 'recovery';
  bars?: number;
  return_pct?: number | null;
  max_drawdown_pct?: number | null;
}

export interface RegimeSpec {
  key: string;
  name: string;
  kind: 'crash' | 'drawdown' | 'recovery';
  start: string;
  end: string;
  description: string;
}

export interface SentimentSummary {
  items_considered?: number;
  mean_sentiment?: number | null;
  very_negative_count?: number;
  material_negative_count?: number;
  most_negative?: string | null;
}

export interface TopHolding {
  symbol?: string | null;
  name: string;
  weight_pct?: number | null;
}

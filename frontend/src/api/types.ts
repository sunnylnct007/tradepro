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

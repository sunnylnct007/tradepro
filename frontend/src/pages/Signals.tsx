import type { ReactNode } from "react";
import { useEffect, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { api } from "../api/client";
import type { HitRateResult, SignalDecision, Watchlist } from "../api/types";
import { config } from "../config";
import { Info } from "../components/Info";
import { PriceHistoryChart } from "../components/PriceHistoryChart";
import { StrategyPicker } from "../components/StrategyPicker";
import { SymbolPicker } from "../components/SymbolPicker";

const actionToneVar: Record<SignalDecision["action"], string> = {
  BUY: "var(--up)",
  SELL: "var(--down)",
  HOLD: "var(--neutral)",
};

export function Signals() {
  const [searchParams] = useSearchParams();
  const [watchlist, setWatchlist] = useState<Watchlist | null>(null);
  const [symbol, setSymbol] = useState(searchParams.get("symbol") ?? "BARC.L");
  const [strategy, setStrategy] = useState("sma_crossover");
  const [fast, setFast] = useState(20);
  const [slow, setSlow] = useState(50);
  const [rsiLow, setRsiLow] = useState(30);
  const [rsiHigh, setRsiHigh] = useState(70);
  const [macdFast, setMacdFast] = useState(12);
  const [macdSlow, setMacdSlow] = useState(26);
  const [macdSignal, setMacdSignal] = useState(9);
  const [donchian, setDonchian] = useState(20);
  const [ichimokuTenkan, setIchimokuTenkan] = useState(9);
  const [ichimokuKijun, setIchimokuKijun] = useState(26);
  const [ichimokuSenkouB, setIchimokuSenkouB] = useState(52);
  const [bollingerWindow, setBollingerWindow] = useState(20);
  const [bollingerStd, setBollingerStd] = useState(2.0);
  const [bollingerRsiOversold, setBollingerRsiOversold] = useState(35);
  const [decision, setDecision] = useState<SignalDecision | null>(null);
  const [hitRate, setHitRate] = useState<HitRateResult | null>(null);
  const [hitRateLoading, setHitRateLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  // Multi-strategy consensus: results of running all 5 strategies in
  // parallel against the same symbol so the user gets the same
  // "consensus across strategies" view the Decide page has, but for
  // an arbitrary symbol (not just the cached universes).
  const [multi, setMulti] = useState<MultiResult | null>(null);
  const [multiLoading, setMultiLoading] = useState(false);

  useEffect(() => {
    api.ukWatchlist().then(setWatchlist).catch(() => {});
  }, []);

  function paramsFor(): Record<string, number> | null {
    switch (strategy) {
      case "sma_crossover": return { fast, slow };
      case "rsi_mean_reversion": return { low: rsiLow, high: rsiHigh };
      case "macd_signal_cross": return { fast: macdFast, slow: macdSlow, signal: macdSignal };
      case "donchian_breakout": return { lookback: donchian };
      case "ichimoku_cloud":
        return { tenkan: ichimokuTenkan, kijun: ichimokuKijun, senkou_b: ichimokuSenkouB };
      case "bollinger_bounce":
        return { window: bollingerWindow, num_std: bollingerStd, rsi_oversold: bollingerRsiOversold };
      default: return null;
    }
  }

  async function runAllStrategies() {
    setMultiLoading(true);
    setError(null);
    setMulti(null);
    // Strategy params reuse the per-strategy state already on the
    // page (fast/slow, RSI bands, MACD triplet, donchian lookback).
    // buy_and_hold takes no params.
    const jobs: { name: string; params: Record<string, number> | null }[] = [
      { name: "buy_and_hold", params: null },
      { name: "sma_crossover", params: { fast, slow } },
      { name: "rsi_mean_reversion", params: { low: rsiLow, high: rsiHigh } },
      { name: "macd_signal_cross", params: { fast: macdFast, slow: macdSlow, signal: macdSignal } },
      { name: "donchian_breakout", params: { lookback: donchian } },
      { name: "ichimoku_cloud", params: { tenkan: ichimokuTenkan, kijun: ichimokuKijun, senkou_b: ichimokuSenkouB } },
      { name: "bollinger_bounce", params: { window: bollingerWindow, num_std: bollingerStd, rsi_oversold: bollingerRsiOversold } },
    ];
    try {
      const results = await Promise.all(
        jobs.map(async (j) => {
          try {
            const d = await api.evaluateSignal({
              symbol,
              provider: config.defaultProvider,
              strategy: j.name,
              lookbackDays: 365,
              params: j.params,
            });
            return { strategy: j.name, decision: d, error: null as string | null };
          } catch (e) {
            return { strategy: j.name, decision: null, error: String(e) };
          }
        }),
      );
      setMulti(buildMultiResult(results));
    } finally {
      setMultiLoading(false);
    }
  }

  async function evaluate() {
    setLoading(true);
    setError(null);
    setHitRate(null);
    const params = paramsFor();
    try {
      const d = await api.evaluateSignal({
        symbol,
        provider: config.defaultProvider,
        strategy,
        lookbackDays: 365,
        params,
      });
      setDecision(d);
      // Kick off the hit-rate in the background — slower, queries 10y.
      setHitRateLoading(true);
      api
        .hitRate({ symbol, provider: config.defaultProvider, strategy, lookbackYears: 10, params })
        .then(setHitRate)
        .catch(() => {})
        .finally(() => setHitRateLoading(false));
    } catch (e) {
      setError(String(e));
    } finally {
      setLoading(false);
    }
  }

  const ind = decision?.indicators;
  const fmt = (n: number | null | undefined, d = 2) =>
    n === null || n === undefined ? "—" : n.toFixed(d);

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 20 }}>
      <div>
        <h1 style={{ margin: 0, fontSize: 24 }}>Research — single-symbol signal</h1>
        <p style={{ color: "var(--text-dim)", margin: "6px 0 0 0", maxWidth: 880, lineHeight: 1.55 }}>
          <strong style={{ color: "var(--text)" }}>What this page does:</strong>{" "}
          Pick a symbol, run all 5 strategies in parallel, see the consensus
          BUY / SELL / HOLD with the live indicators (RSI, SMA20/50/200, distance
          from 52w high/low) behind the call. Plus a 10-year hit-rate showing how
          often this strategy combo would have been profitable historically.
        </p>
        <p style={{ color: "var(--text-muted)", margin: "8px 0 0 0", maxWidth: 880, fontSize: 12, lineHeight: 1.55 }}>
          <strong>Research vs Backtest:</strong> Research answers <em>"what's the
          verdict on this symbol RIGHT NOW?"</em>. Backtest replays a strategy on
          historical data and shows the equity curve over years. Use Research
          for live decisions; Backtest to validate a strategy before trusting it.
        </p>
      </div>

      {/* Symbol picker on its own row — same pattern as Backtest. The
          autocomplete needs space; the strategy-tuning grid below it
          covers the rest of the inputs. */}
      <section
        className="card"
        style={{
          display: "flex",
          flexDirection: "column",
          gap: 8,
          padding: "12px 14px",
        }}
      >
        <span className="stat-label">Symbol</span>
        <div style={{ maxWidth: 520 }}>
          <SymbolPicker value={symbol} onChange={setSymbol} placeholder="e.g. NVDA, VUKE.L" />
        </div>
        {watchlist && watchlist.items.length > 0 && (
          <div style={{ marginTop: 6, fontSize: 11, color: "var(--text-muted)", display: "flex", flexWrap: "wrap", gap: 6, alignItems: "center" }}>
            <span style={{ marginRight: 4 }}>Or pick from watchlist:</span>
            {watchlist.items.slice(0, 8).map((i) => {
              const active = symbol === i.symbol;
              return (
                <button
                  key={i.symbol}
                  type="button"
                  onClick={() => setSymbol(i.symbol)}
                  title={i.label}
                  style={{
                    padding: "2px 8px",
                    fontSize: 11,
                    borderRadius: 999,
                    border: "1px solid var(--border)",
                    boxShadow: active ? "inset 0 0 0 1px var(--accent, #4f8cff)" : "none",
                    background: active ? "var(--bg-hover)" : "transparent",
                    color: active ? "var(--text)" : "var(--text-dim)",
                    cursor: "pointer",
                    lineHeight: 1.4,
                  }}
                >
                  {i.symbol}
                </button>
              );
            })}
          </div>
        )}
      </section>

      <section
        className="card"
        style={{
          display: "grid",
          gridTemplateColumns: "repeat(auto-fit, minmax(150px, 1fr))",
          gap: 14,
          alignItems: "end",
        }}
      >
        <Labelled label="Strategy" help="strategy">
          <StrategyPicker value={strategy} onChange={setStrategy} />
        </Labelled>
        {strategy === "sma_crossover" && (
          <>
            <Labelled label="Fast SMA" help="fast_sma">
              <input type="number" value={fast} onChange={(e) => setFast(Number(e.target.value))} />
            </Labelled>
            <Labelled label="Slow SMA" help="slow_sma">
              <input type="number" value={slow} onChange={(e) => setSlow(Number(e.target.value))} />
            </Labelled>
          </>
        )}
        {strategy === "rsi_mean_reversion" && (
          <>
            <Labelled label="Oversold below" help="rsi14">
              <input type="number" value={rsiLow} onChange={(e) => setRsiLow(Number(e.target.value))} />
            </Labelled>
            <Labelled label="Overbought above" help="rsi14">
              <input type="number" value={rsiHigh} onChange={(e) => setRsiHigh(Number(e.target.value))} />
            </Labelled>
          </>
        )}
        {strategy === "macd_signal_cross" && (
          <>
            <Labelled label="Fast EMA" help="macd_fast">
              <input type="number" value={macdFast} onChange={(e) => setMacdFast(Number(e.target.value))} />
            </Labelled>
            <Labelled label="Slow EMA" help="macd_slow">
              <input type="number" value={macdSlow} onChange={(e) => setMacdSlow(Number(e.target.value))} />
            </Labelled>
            <Labelled label="Signal EMA" help="macd_signal">
              <input type="number" value={macdSignal} onChange={(e) => setMacdSignal(Number(e.target.value))} />
            </Labelled>
          </>
        )}
        {strategy === "donchian_breakout" && (
          <Labelled label="Lookback (bars)" help="donchian_lookback">
            <input type="number" value={donchian} onChange={(e) => setDonchian(Number(e.target.value))} />
          </Labelled>
        )}
        {strategy === "ichimoku_cloud" && (
          <>
            <Labelled label="Tenkan (fast)">
              <input type="number" value={ichimokuTenkan} onChange={(e) => setIchimokuTenkan(Number(e.target.value))} />
            </Labelled>
            <Labelled label="Kijun (base)">
              <input type="number" value={ichimokuKijun} onChange={(e) => setIchimokuKijun(Number(e.target.value))} />
            </Labelled>
            <Labelled label="Senkou B (cloud)">
              <input type="number" value={ichimokuSenkouB} onChange={(e) => setIchimokuSenkouB(Number(e.target.value))} />
            </Labelled>
          </>
        )}
        {strategy === "bollinger_bounce" && (
          <>
            <Labelled label="Window (bars)">
              <input type="number" value={bollingerWindow} onChange={(e) => setBollingerWindow(Number(e.target.value))} />
            </Labelled>
            <Labelled label="Std dev (× σ)">
              <input type="number" step="0.1" value={bollingerStd} onChange={(e) => setBollingerStd(Number(e.target.value))} />
            </Labelled>
            <Labelled label="RSI oversold below">
              <input type="number" value={bollingerRsiOversold} onChange={(e) => setBollingerRsiOversold(Number(e.target.value))} />
            </Labelled>
          </>
        )}
        <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
          <button
            className="primary"
            onClick={runAllStrategies}
            disabled={multiLoading || !symbol.trim()}
            title="Run every registered strategy on this symbol and show the consensus — same view the Decide page produces for cached universes"
          >
            {multiLoading ? "Running strategies…" : "Run all strategies"}
          </button>
          <button
            onClick={evaluate}
            disabled={loading || !symbol.trim()}
            style={{ fontSize: 11, padding: "4px 8px" }}
          >
            {loading ? "Evaluating…" : "Single strategy (advanced)"}
          </button>
        </div>
      </section>

      {error && (
        <div
          className="card"
          style={{ borderColor: "var(--down)", color: "var(--down)", background: "var(--down-soft)" }}
        >
          {error}
        </div>
      )}

      {multi && <MultiStrategyCard result={multi} symbol={symbol} />}

      {/* Price history sits BETWEEN the multi-strategy verdict and
          the single-strategy detail so the user sees the chart
          context for the verdict they just produced. Split-adjusted
          adj_close so 4:1 / 2:1 splits don't read as fake crashes. */}
      {(multi || decision) && symbol && (
        <PriceHistoryChart symbol={symbol} />
      )}

      {decision && (
        <div
          style={{
            display: "grid",
            gridTemplateColumns: "minmax(280px, 360px) 1fr",
            gap: 20,
            alignItems: "start",
          }}
        >
          <div
            className="card"
            style={{ borderLeft: `4px solid ${actionToneVar[decision.action]}` }}
          >
            <div className="stat-label">Recommendation</div>
            <div
              className="num"
              style={{
                fontSize: 42,
                fontWeight: 700,
                color: actionToneVar[decision.action],
                letterSpacing: "0.04em",
                marginTop: 4,
              }}
            >
              {decision.action}
            </div>
            <div style={{ fontSize: 12, color: "var(--text-dim)", marginTop: 4, display: "flex", alignItems: "center" }}>
              Confidence <span className="num" style={{ marginLeft: 4 }}>{Math.round(decision.confidence * 100)}%</span>
              <Info k="confidence" />
              {" · "}
              <span style={{ marginLeft: 6 }}>as of <span className="num">{decision.asOf.slice(0, 10)}</span></span>
            </div>
            <div style={{ marginTop: 14, fontSize: 13, color: "var(--text-dim)" }}>
              Strategy: <code>{decision.strategy}</code>
            </div>
            {(decision.suggestedStopLossPct || decision.suggestedTargetPct) && (
              <div style={{ marginTop: 10, fontSize: 13, display: "flex", gap: 14 }}>
                {decision.suggestedStopLossPct && (
                  <span>
                    <span className="stat-label" style={{ display: "block" }}>Stop</span>
                    <span className="down num">−{decision.suggestedStopLossPct}%</span>
                  </span>
                )}
                {decision.suggestedTargetPct && (
                  <span>
                    <span className="stat-label" style={{ display: "block" }}>Target</span>
                    <span className="up num">+{decision.suggestedTargetPct}%</span>
                  </span>
                )}
              </div>
            )}
          </div>

          <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
            <div className="card">
              <h3 style={{ margin: "0 0 10px 0", fontSize: 13, textTransform: "uppercase", letterSpacing: "0.08em", color: "var(--text-muted)" }}>
                Why this call
              </h3>
              <div>
                {decision.reasons.map((r, i) => {
                  const tone = toneOf(r, decision.action);
                  return (
                    <div key={i} className={`reason ${tone}`}>
                      <span className="dot" />
                      <span>{r}</span>
                    </div>
                  );
                })}
              </div>
            </div>
            <div className="card">
              <h3 style={{ margin: "0 0 12px 0", fontSize: 13, textTransform: "uppercase", letterSpacing: "0.08em", color: "var(--text-muted)" }}>
                Indicators
              </h3>
              <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(120px, 1fr))", gap: 14 }}>
                <Stat label="Last close" value={fmt(ind?.lastClose)} />
                <Stat label="SMA 20" value={fmt(ind?.sma20)} help="fast_sma" />
                <Stat label="SMA 50" value={fmt(ind?.sma50)} help="slow_sma" />
                <Stat label="SMA 200" value={fmt(ind?.sma200)} />
                <Stat label="RSI 14" value={fmt(ind?.rsi14, 1)} help="rsi14" />
                <Stat
                  label="vs 52w high"
                  value={ind?.priceVs52wHighPct != null ? `${fmt(ind.priceVs52wHighPct, 1)}%` : "—"}
                  tone={ind?.priceVs52wHighPct != null ? (ind.priceVs52wHighPct >= -3 ? "up" : "down") : undefined}
                  help="vs_52w"
                />
                <Stat
                  label="vs 52w low"
                  value={ind?.priceVs52wLowPct != null ? `${fmt(ind.priceVs52wLowPct, 1)}%` : "—"}
                  tone={ind?.priceVs52wLowPct != null ? (ind.priceVs52wLowPct > 20 ? "up" : undefined) : undefined}
                  help="vs_52w"
                />
              </div>
            </div>

            <HitRateCard loading={hitRateLoading} result={hitRate} />
          </div>
        </div>
      )}
    </div>
  );
}

function HitRateCard({ loading, result }: { loading: boolean; result: HitRateResult | null }) {
  const pct = (n: number | null | undefined, d = 1) =>
    n === null || n === undefined ? "—" : `${n >= 0 ? "+" : ""}${n.toFixed(d)}%`;

  if (!loading && !result) return null;

  return (
    <div className="card">
      <h3
        style={{
          margin: "0 0 12px 0",
          fontSize: 13,
          textTransform: "uppercase",
          letterSpacing: "0.08em",
          color: "var(--text-muted)",
          display: "flex",
          alignItems: "center",
        }}
      >
        Historical hit-rate <Info k="win_rate" />
        <span style={{ marginLeft: "auto", fontSize: 11, fontWeight: 400, color: "var(--text-muted)" }}>
          {result ? `${new Date(result.from).getFullYear()}–${new Date(result.to).getFullYear()}` : ""}
        </span>
      </h3>

      {loading && !result && <div style={{ color: "var(--text-dim)" }}>Simulating 10 years of this strategy…</div>}

      {result && result.totalTrades === 0 && (
        <div style={{ color: "var(--text-dim)", fontSize: 13 }}>
          No round-trip trades in the window. Either the strategy never fires (Buy &amp; Hold) or
          the window's too short — increase lookback to see more.
        </div>
      )}

      {result && result.totalTrades > 0 && (
        <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(130px, 1fr))", gap: 14 }}>
          <Stat
            label="Win rate"
            value={`${result.winRatePct.toFixed(0)}%`}
            tone={result.winRatePct >= 55 ? "up" : result.winRatePct < 45 ? "down" : undefined}
            help="win_rate"
          />
          <Stat label="Trades" value={`${result.winners}W / ${result.losers}L`} />
          <Stat label="Avg winner" value={pct(result.avgWinnerPct)} tone="up" />
          <Stat label="Avg loser" value={pct(result.avgLoserPct)} tone="down" />
          <Stat
            label="Expectancy"
            value={pct(result.expectancyPct, 2)}
            tone={result.expectancyPct > 0 ? "up" : "down"}
            help="expectancy"
          />
          <Stat label="Best / worst" value={`${pct(result.bestPct)} / ${pct(result.worstPct)}`} />
          <Stat
            label="Median hold"
            value={`${Math.round(result.medianHoldingDays)}d`}
            help="median_hold"
          />
          <Stat
            label="Cumulative (rough)"
            value={pct(result.totalReturnPct, 0)}
            tone={result.totalReturnPct > 0 ? "up" : "down"}
          />
        </div>
      )}
    </div>
  );
}

function Labelled({ label, help, children }: { label: string; help?: string; children: ReactNode }) {
  return (
    <label style={{ display: "flex", flexDirection: "column", gap: 5 }}>
      <span className="stat-label">
        {label}
        {help && <Info k={help as keyof typeof import("../docs/tooltips").HELP} />}
      </span>
      {children}
    </label>
  );
}

/** Classify a reason as up / down / neutral so the bullet dot matches the tone.
 * Heuristic: keyword match + agreement with the overall BUY/SELL call. */
function toneOf(reason: string, action: SignalDecision["action"]): "up" | "down" | "neutral" {
  const r = reason.toLowerCase();
  if (/triggered buy|up-trend|oversold|bounce|bullish/.test(r)) return "up";
  if (/triggered sell|down-trend|overbought|bearish|pullback/.test(r)) return "down";
  if (/neutral|no fresh signal|52w/.test(r)) return "neutral";
  return action === "BUY" ? "up" : action === "SELL" ? "down" : "neutral";
}

function Stat({ label, value, tone, help }: { label: string; value: string; tone?: "up" | "down"; help?: string }) {
  const colour = tone === "up" ? "var(--up)" : tone === "down" ? "var(--down)" : "var(--text)";
  return (
    <div>
      <div className="stat-label">
        {label}
        {help && <Info k={help as keyof typeof import("../docs/tooltips").HELP} />}
      </div>
      <div className="stat-value" style={{ color: colour }}>{value}</div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Multi-strategy consensus — runs all 5 strategies on the chosen symbol in
// parallel and produces a Decide-page-style verdict for any symbol (not
// just cached universes). Avoids the friction of "pick a strategy, run it,
// pick another, run it again" — the engine isn't a single-strategy tool.
// ---------------------------------------------------------------------------

interface PerStrategyResult {
  strategy: string;
  decision: SignalDecision | null;
  error: string | null;
}

interface MultiResult {
  perStrategy: PerStrategyResult[];
  buys: number;
  sells: number;
  holds: number;
  failed: number;
  meanConfidence: number | null;
  consensus: "BUY" | "SELL" | "HOLD" | "MIXED";
}

function buildMultiResult(perStrategy: PerStrategyResult[]): MultiResult {
  const buys = perStrategy.filter((r) => r.decision?.action === "BUY").length;
  const sells = perStrategy.filter((r) => r.decision?.action === "SELL").length;
  const holds = perStrategy.filter((r) => r.decision?.action === "HOLD").length;
  const failed = perStrategy.filter((r) => r.decision === null).length;
  const ok = perStrategy.filter((r) => r.decision !== null);
  const meanConf = ok.length === 0
    ? null
    : ok.reduce((s, r) => s + (r.decision!.confidence || 0), 0) / ok.length;
  let consensus: MultiResult["consensus"] = "MIXED";
  // Majority of NON-FAILED votes wins. With ≤3 ok votes the answer
  // is too noisy to call a consensus, so we report MIXED.
  if (ok.length >= 3) {
    if (buys >= Math.ceil(ok.length / 2 + 0.5)) consensus = "BUY";
    else if (sells >= Math.ceil(ok.length / 2 + 0.5)) consensus = "SELL";
    else if (holds + buys >= ok.length - 1 && buys === 0) consensus = "HOLD";
  }
  return { perStrategy, buys, sells, holds, failed, meanConfidence: meanConf, consensus };
}

function MultiStrategyCard({ result, symbol }: { result: MultiResult; symbol: string }) {
  const colour =
    result.consensus === "BUY" ? "var(--up)"
    : result.consensus === "SELL" ? "var(--down)"
    : "var(--neutral)";
  return (
    <section className="card" style={{ borderLeft: `4px solid ${colour}` }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline", flexWrap: "wrap", gap: 12, marginBottom: 12 }}>
        <div>
          <div className="stat-label">Multi-strategy consensus</div>
          <div style={{ marginTop: 4, display: "flex", alignItems: "baseline", gap: 12, flexWrap: "wrap" }}>
            <span style={{ fontSize: 28, fontWeight: 700, color: colour, letterSpacing: "0.04em" }}>
              {result.consensus}
            </span>
            <span style={{ fontSize: 13, color: "var(--text-dim)" }}>
              <strong>{symbol}</strong> · {result.buys} BUY · {result.sells} SELL · {result.holds} HOLD
              {result.failed > 0 && ` · ${result.failed} failed`}
            </span>
          </div>
        </div>
        {result.meanConfidence !== null && (
          <div style={{ textAlign: "right" }}>
            <div className="stat-label">Mean confidence</div>
            <div className="num" style={{ fontSize: 22, color: colour, fontWeight: 600 }}>
              {Math.round(result.meanConfidence * 100)}%
            </div>
          </div>
        )}
      </div>

      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(180px, 1fr))", gap: 10 }}>
        {result.perStrategy.map((r) => (
          <div
            key={r.strategy}
            style={{
              padding: "8px 10px",
              border: "1px solid var(--border)",
              borderRadius: 6,
              background: "rgba(0,0,0,0.12)",
            }}
          >
            <div style={{ fontSize: 11, color: "var(--text-muted)", textTransform: "uppercase", letterSpacing: "0.06em" }}>
              {r.strategy.replace(/_/g, " ")}
            </div>
            {r.decision ? (
              <div style={{ marginTop: 4 }}>
                <span
                  className="num"
                  style={{
                    fontSize: 18,
                    fontWeight: 700,
                    color: actionToneVar[r.decision.action],
                    letterSpacing: "0.04em",
                  }}
                >
                  {r.decision.action}
                </span>
                <span style={{ marginLeft: 8, fontSize: 11, color: "var(--text-muted)" }}>
                  {Math.round(r.decision.confidence * 100)}% conf
                </span>
                <div style={{ fontSize: 11, color: "var(--text-dim)", marginTop: 4, lineHeight: 1.4 }}>
                  {r.decision.reasons[0] || "—"}
                </div>
              </div>
            ) : (
              <div style={{ fontSize: 11, color: "var(--down)", marginTop: 4 }}>
                Failed: {(r.error || "").slice(0, 80)}
              </div>
            )}
          </div>
        ))}
      </div>

      <div style={{ marginTop: 10, fontSize: 11, color: "var(--text-muted)" }}>
        Decision aid, not advice. Runs all 5 strategies in parallel and counts the votes —
        same logic the Decide page uses for cached universes. Use the "Single strategy" button
        below to tune parameters.
      </div>
    </section>
  );
}

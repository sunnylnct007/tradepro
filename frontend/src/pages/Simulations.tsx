import type { ReactNode } from "react";
import { useMemo, useState } from "react";
import {
  CartesianGrid,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { api } from "../api/client";
import type { SimulationRequest, SimulationResult } from "../api/types";
import { config } from "../config";
import { Info } from "../components/Info";
import { StrategyPicker } from "../components/StrategyPicker";
import { SymbolPicker } from "../components/SymbolPicker";

// Popular tickers shown as one-click chips above the symbol box —
// covers the "I want to test something but don't know what ticker to
// type" friction the user flagged on the Backtest page. Curated to
// span equity-index / blue-chip / sector-leader / GBP / USD venues.
const POPULAR_SYMBOLS: { symbol: string; label: string }[] = [
  { symbol: "^FTSE", label: "FTSE 100" },
  { symbol: "^GSPC", label: "S&P 500" },
  { symbol: "VUKE.L", label: "FTSE 100 ETF" },
  { symbol: "VUSA.L", label: "S&P 500 ETF" },
  { symbol: "VOO", label: "S&P 500 (US)" },
  { symbol: "QQQ", label: "Nasdaq-100" },
  { symbol: "AAPL", label: "Apple" },
  { symbol: "MSFT", label: "Microsoft" },
  { symbol: "NVDA", label: "Nvidia" },
];

const isoDate = (d: Date) => d.toISOString().slice(0, 10);

export function Simulations() {
  const [symbol, setSymbol] = useState("^FTSE");
  const [strategy, setStrategy] = useState("buy_and_hold");
  const [from, setFrom] = useState(isoDate(new Date(Date.now() - 5 * 365 * 24 * 3600 * 1000)));
  const [to, setTo] = useState(isoDate(new Date()));
  const [capital, setCapital] = useState(10000);
  const [fast, setFast] = useState(20);
  const [slow, setSlow] = useState(50);
  const [rsiLow, setRsiLow] = useState(30);
  const [rsiHigh, setRsiHigh] = useState(70);
  const [macdFast, setMacdFast] = useState(12);
  const [macdSlow, setMacdSlow] = useState(26);
  const [macdSignal, setMacdSignal] = useState(9);
  const [donchian, setDonchian] = useState(20);
  const [stampDuty, setStampDuty] = useState(0.005);
  const [commission, setCommission] = useState(0);
  const [result, setResult] = useState<SimulationResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [running, setRunning] = useState(false);

  // Multi-strategy backtest: fan out to all 5 strategies in parallel
  // and show the equity curves + headline stats side-by-side. The
  // single-strategy `run()` below stays for parameter tuning, but this
  // is the realistic default — the engine isn't a single-strategy tool.
  const [multi, setMulti] = useState<MultiBacktest[] | null>(null);
  const [multiRunning, setMultiRunning] = useState(false);

  function paramsFor(s: string): Record<string, number> | null {
    switch (s) {
      case "sma_crossover": return { fast, slow };
      case "rsi_mean_reversion": return { low: rsiLow, high: rsiHigh };
      case "macd_signal_cross": return { fast: macdFast, slow: macdSlow, signal: macdSignal };
      case "donchian_breakout": return { lookback: donchian };
      default: return null;
    }
  }

  async function runAll() {
    setMultiRunning(true);
    setError(null);
    setMulti(null);
    const strategies = [
      "buy_and_hold",
      "sma_crossover",
      "rsi_mean_reversion",
      "macd_signal_cross",
      "donchian_breakout",
    ];
    try {
      const results = await Promise.all(
        strategies.map(async (s) => {
          try {
            const req: SimulationRequest = {
              symbol,
              provider: config.defaultProvider,
              strategy: s,
              from: new Date(from).toISOString(),
              to: new Date(to).toISOString(),
              initialCapital: capital,
              currency: config.defaultCurrency,
              fees: { commissionPerTrade: commission, stampDutyRate: stampDuty, fxSpread: 0 },
              params: paramsFor(s),
            };
            const r = await api.runSimulation(req);
            return { strategy: s, result: r, error: null as string | null };
          } catch (e) {
            return { strategy: s, result: null, error: String(e) };
          }
        }),
      );
      setMulti(results);
    } finally {
      setMultiRunning(false);
    }
  }

  async function run() {
    setRunning(true);
    setError(null);
    try {
      const req: SimulationRequest = {
        symbol,
        provider: config.defaultProvider,
        strategy,
        from: new Date(from).toISOString(),
        to: new Date(to).toISOString(),
        initialCapital: capital,
        currency: config.defaultCurrency,
        fees: { commissionPerTrade: commission, stampDutyRate: stampDuty, fxSpread: 0 },
        params:
          strategy === "sma_crossover"
            ? { fast, slow }
            : strategy === "rsi_mean_reversion"
              ? { low: rsiLow, high: rsiHigh }
              : strategy === "macd_signal_cross"
                ? { fast: macdFast, slow: macdSlow, signal: macdSignal }
                : strategy === "donchian_breakout"
                  ? { lookback: donchian }
                  : null,
      };
      const r = await api.runSimulation(req);
      setResult(r);
    } catch (e) {
      setError(String(e));
    } finally {
      setRunning(false);
    }
  }

  const chartData = useMemo(
    () =>
      (result?.equityCurve ?? []).map((p) => ({
        t: p.timestamp.slice(0, 10),
        equity: Number(p.equity.toFixed(2)),
      })),
    [result],
  );

  const ccy = (n: number) =>
    new Intl.NumberFormat("en-GB", {
      style: "currency",
      currency: result?.currency ?? "GBP",
      maximumFractionDigits: 0,
    }).format(n);

  const pnl = result ? result.finalEquity - result.initialCapital : 0;
  const pnlTone = pnl >= 0 ? "up" : "down";

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 20 }}>
      <div>
        <h1 style={{ margin: 0, fontSize: 24 }}>Simulations</h1>
        <p style={{ color: "var(--text-dim)", margin: "6px 0 0 0", maxWidth: 820 }}>
          How much money would this strategy have made? UK fee model by default (0.5% stamp duty on buys).
        </p>
        <div
          style={{
            display: "flex",
            flexWrap: "wrap",
            gap: 6,
            marginTop: 10,
            alignItems: "center",
          }}
        >
          <span style={{ fontSize: 11, color: "var(--text-muted)", marginRight: 4 }}>
            Quick pick:
          </span>
          {POPULAR_SYMBOLS.map((p) => (
            <button
              key={p.symbol}
              type="button"
              onClick={() => setSymbol(p.symbol)}
              title={p.label}
              style={{
                padding: "3px 9px",
                fontSize: 11,
                borderRadius: 999,
                border: `1px solid ${
                  symbol === p.symbol ? "var(--accent, #4f8cff)" : "var(--border)"
                }`,
                background: symbol === p.symbol ? "var(--bg-hover)" : "transparent",
                color: symbol === p.symbol ? "var(--text)" : "var(--text-dim)",
                fontWeight: symbol === p.symbol ? 600 : 400,
                cursor: "pointer",
                lineHeight: 1.4,
              }}
            >
              {p.symbol}
            </button>
          ))}
        </div>
      </div>

      <section
        className="card"
        style={{
          display: "grid",
          gridTemplateColumns: "repeat(auto-fit, minmax(150px, 1fr))",
          gap: 14,
          alignItems: "end",
        }}
      >
        <Labelled label="Symbol">
          <SymbolPicker value={symbol} onChange={setSymbol} placeholder="e.g. NVDA, VUKE.L" />
        </Labelled>
        <Labelled label="Strategy" help="strategy">
          <StrategyPicker value={strategy} onChange={setStrategy} />
        </Labelled>
        <Labelled label="From">
          <input type="date" value={from} onChange={(e) => setFrom(e.target.value)} />
        </Labelled>
        <Labelled label="To">
          <input type="date" value={to} onChange={(e) => setTo(e.target.value)} />
        </Labelled>
        <Labelled label={`Capital (${config.defaultCurrency})`} help="initial_capital">
          <input type="number" value={capital} onChange={(e) => setCapital(Number(e.target.value))} />
        </Labelled>
        <Labelled label="Stamp duty" help="stamp_duty">
          <input type="number" step="0.001" value={stampDuty} onChange={(e) => setStampDuty(Number(e.target.value))} />
        </Labelled>
        <Labelled label="Commission / trade" help="commission">
          <input type="number" value={commission} onChange={(e) => setCommission(Number(e.target.value))} />
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
        <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
          <button
            className="primary"
            onClick={runAll}
            disabled={multiRunning || !symbol.trim()}
            title="Backtest all 5 strategies on this symbol over the same window — see them side-by-side instead of running each manually"
          >
            {multiRunning ? "Backtesting 5 strategies…" : "Backtest all 5 strategies"}
          </button>
          <button
            onClick={run}
            disabled={running || !symbol.trim()}
            style={{ fontSize: 11, padding: "4px 8px" }}
          >
            {running ? "Running…" : "Single strategy (advanced)"}
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

      {multi && <MultiBacktestCard results={multi} symbol={symbol} initialCapital={capital} />}

      {result && (
        <>
          <section
            className="card"
            style={{
              display: "grid",
              gridTemplateColumns: "repeat(auto-fit, minmax(130px, 1fr))",
              gap: 16,
            }}
          >
            <Stat label="Final equity" value={ccy(result.finalEquity)} />
            <Stat label="P&L" value={ccy(pnl)} tone={pnlTone} />
            <Stat label="Total return" value={`${result.totalReturnPct.toFixed(2)}%`} tone={result.totalReturnPct >= 0 ? "up" : "down"} />
            <Stat label="CAGR" value={`${result.cagrPct.toFixed(2)}%`} tone={result.cagrPct >= 0 ? "up" : "down"} help="cagr" />
            <Stat label="Max drawdown" value={`${result.maxDrawdownPct.toFixed(2)}%`} tone="down" help="max_drawdown" />
            <Stat label="Sharpe" value={result.sharpeRatio.toFixed(2)} help="sharpe" />
            <Stat label="Trades" value={String(result.tradeCount)} />
          </section>

          <section className="card" style={{ padding: 8 }}>
            <div style={{ height: 400 }}>
              <ResponsiveContainer width="100%" height="100%">
                <LineChart data={chartData}>
                  <CartesianGrid strokeDasharray="3 3" />
                  <XAxis dataKey="t" minTickGap={40} />
                  <YAxis domain={["auto", "auto"]} />
                  <Tooltip
                    formatter={(v: number) => ccy(v)}
                    labelStyle={{ color: "var(--text-dim)" }}
                  />
                  <Line
                    type="monotone"
                    dataKey="equity"
                    stroke="var(--accent)"
                    dot={false}
                    strokeWidth={2}
                  />
                </LineChart>
              </ResponsiveContainer>
            </div>
          </section>
        </>
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
// Multi-strategy backtest comparison — same symbol + window, all 5
// strategies side-by-side. Replaces the friction of "run buy_and_hold,
// switch, run sma_crossover, switch, run rsi…" with one click that
// fans out and shows the equity curves on top of each other.
// ---------------------------------------------------------------------------

interface MultiBacktest {
  strategy: string;
  result: SimulationResult | null;
  error: string | null;
}

const STRATEGY_COLOURS: Record<string, string> = {
  buy_and_hold: "#9ba1ad",
  sma_crossover: "#4f8cff",
  rsi_mean_reversion: "#1fc16b",
  macd_signal_cross: "#9b6eff",
  donchian_breakout: "#e8a23a",
};

function MultiBacktestCard({
  results, symbol, initialCapital,
}: {
  results: MultiBacktest[];
  symbol: string;
  initialCapital: number;
}) {
  const ok = results.filter((r) => r.result !== null);
  // Build a sparse-friendly chart series — each strategy contributes
  // its own equity curve keyed by date. Sample every ~Nth point so we
  // don't render 1000+ datapoints × 5 series.
  const chartData = useMemo(() => {
    if (ok.length === 0) return [];
    const stride = Math.max(1, Math.floor((ok[0].result!.equityCurve.length || 1) / 200));
    const allDates = new Set<string>();
    const byStratByDate: Record<string, Record<string, number>> = {};
    for (const r of ok) {
      byStratByDate[r.strategy] = {};
      r.result!.equityCurve.forEach((p, i) => {
        if (i % stride !== 0 && i !== r.result!.equityCurve.length - 1) return;
        const d = p.timestamp.slice(0, 10);
        allDates.add(d);
        byStratByDate[r.strategy][d] = Number(p.equity.toFixed(2));
      });
    }
    return Array.from(allDates).sort().map((d) => {
      const row: Record<string, string | number> = { t: d };
      for (const r of ok) {
        const v = byStratByDate[r.strategy][d];
        if (v !== undefined) row[r.strategy] = v;
      }
      return row;
    });
  }, [results]);

  // Best by total return — used to highlight the "winner" of the
  // comparison and put a small banner up top.
  const winner = useMemo(() => {
    return ok.reduce<MultiBacktest | null>((best, r) => {
      if (!best) return r;
      return (r.result!.totalReturnPct > best.result!.totalReturnPct) ? r : best;
    }, null);
  }, [results]);

  return (
    <div className="card" style={{ display: "flex", flexDirection: "column", gap: 14 }}>
      <div style={{ display: "flex", alignItems: "baseline", justifyContent: "space-between", gap: 10, flexWrap: "wrap" }}>
        <div>
          <div className="stat-label">5-strategy backtest comparison</div>
          <div style={{ marginTop: 2, color: "var(--text-dim)", fontSize: 13 }}>
            <strong>{symbol}</strong> · same window, same fees, same starting capital
            {" "}({new Intl.NumberFormat("en-GB", { style: "currency", currency: "GBP", maximumFractionDigits: 0 }).format(initialCapital)})
          </div>
        </div>
        {winner && winner.result && (
          <div style={{ textAlign: "right" }}>
            <div className="stat-label">Winner</div>
            <div style={{ fontSize: 16, fontWeight: 600, color: "var(--up)" }}>
              {winner.strategy.replace(/_/g, " ")}
              <span className="num" style={{ marginLeft: 8 }}>
                +{winner.result.totalReturnPct.toFixed(1)}%
              </span>
            </div>
          </div>
        )}
      </div>

      {chartData.length > 0 && (
        <ResponsiveContainer width="100%" height={260}>
          <LineChart data={chartData} margin={{ top: 8, right: 16, left: 0, bottom: 0 }}>
            <CartesianGrid stroke="rgba(155,161,173,0.15)" strokeDasharray="3 3" />
            <XAxis dataKey="t" tick={{ fill: "#9ba1ad", fontSize: 10 }} minTickGap={40} />
            <YAxis tick={{ fill: "#9ba1ad", fontSize: 10 }} />
            <Tooltip
              contentStyle={{
                background: "rgba(20,24,33,0.92)",
                border: "1px solid rgba(155,161,173,0.3)",
                borderRadius: 6,
                color: "white",
                fontSize: 12,
              }}
            />
            {ok.map((r) => (
              <Line
                key={r.strategy}
                type="monotone"
                dataKey={r.strategy}
                stroke={STRATEGY_COLOURS[r.strategy] || "#cbd2dc"}
                strokeWidth={1.6}
                dot={false}
                name={r.strategy.replace(/_/g, " ")}
                connectNulls
              />
            ))}
          </LineChart>
        </ResponsiveContainer>
      )}

      <div style={{ overflowX: "auto" }}>
        <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 13 }}>
          <thead>
            <tr style={{ background: "var(--bg-hover, rgba(255,255,255,0.05))", color: "var(--text-dim)" }}>
              <th style={{ padding: "6px 10px", textAlign: "left" }}>Strategy</th>
              <th style={{ padding: "6px 10px", textAlign: "right" }}>Total return</th>
              <th style={{ padding: "6px 10px", textAlign: "right" }}>CAGR</th>
              <th style={{ padding: "6px 10px", textAlign: "right" }}>Sharpe</th>
              <th style={{ padding: "6px 10px", textAlign: "right" }}>Max DD</th>
              <th style={{ padding: "6px 10px", textAlign: "right" }}>Trades</th>
            </tr>
          </thead>
          <tbody>
            {results.map((r) => (
              <tr key={r.strategy} style={{ borderTop: "1px solid var(--border)" }}>
                <td style={{ padding: "6px 10px" }}>
                  <span
                    style={{
                      display: "inline-block",
                      width: 8, height: 8, borderRadius: 4,
                      background: STRATEGY_COLOURS[r.strategy] || "#cbd2dc",
                      marginRight: 6,
                    }}
                  />
                  {r.strategy.replace(/_/g, " ")}
                </td>
                {r.result ? (
                  <>
                    <td className="num" style={{ padding: "6px 10px", textAlign: "right", color: r.result.totalReturnPct >= 0 ? "var(--up)" : "var(--down)", fontWeight: 600 }}>
                      {r.result.totalReturnPct >= 0 ? "+" : ""}{r.result.totalReturnPct.toFixed(1)}%
                    </td>
                    <td className="num" style={{ padding: "6px 10px", textAlign: "right" }}>{r.result.cagrPct.toFixed(1)}%</td>
                    <td className="num" style={{ padding: "6px 10px", textAlign: "right" }}>{r.result.sharpeRatio.toFixed(2)}</td>
                    <td className="num" style={{ padding: "6px 10px", textAlign: "right", color: "var(--down)" }}>{r.result.maxDrawdownPct.toFixed(1)}%</td>
                    <td className="num" style={{ padding: "6px 10px", textAlign: "right" }}>{r.result.tradeCount}</td>
                  </>
                ) : (
                  <td colSpan={5} style={{ padding: "6px 10px", color: "var(--down)", fontSize: 11 }}>
                    Failed: {(r.error || "").slice(0, 100)}
                  </td>
                )}
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <div style={{ fontSize: 11, color: "var(--text-muted)" }}>
        Past performance is not indicative of future returns. Different strategies fit different
        regimes — the highest-CAGR backtest may have larger drawdowns or longer recovery times.
      </div>
    </div>
  );
}

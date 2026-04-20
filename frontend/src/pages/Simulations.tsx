import type { ReactNode } from "react";
import { useEffect, useMemo, useState } from "react";
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

const isoDate = (d: Date) => d.toISOString().slice(0, 10);

export function Simulations() {
  const [symbol, setSymbol] = useState("^FTSE");
  const [provider, setProvider] = useState(config.defaultProvider);
  const [strategy, setStrategy] = useState("buy_and_hold");
  const [strategies, setStrategies] = useState<string[]>([]);
  const [from, setFrom] = useState(isoDate(new Date(Date.now() - 5 * 365 * 24 * 3600 * 1000)));
  const [to, setTo] = useState(isoDate(new Date()));
  const [capital, setCapital] = useState(10000);
  const [fast, setFast] = useState(20);
  const [slow, setSlow] = useState(50);
  const [stampDuty, setStampDuty] = useState(0.005);
  const [commission, setCommission] = useState(0);
  const [result, setResult] = useState<SimulationResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [running, setRunning] = useState(false);

  useEffect(() => {
    api.strategies().then((r) => setStrategies(r.strategies)).catch(() => {});
  }, []);

  async function run() {
    setRunning(true);
    setError(null);
    try {
      const req: SimulationRequest = {
        symbol,
        provider,
        strategy,
        from: new Date(from).toISOString(),
        to: new Date(to).toISOString(),
        initialCapital: capital,
        currency: config.defaultCurrency,
        fees: { commissionPerTrade: commission, stampDutyRate: stampDuty, fxSpread: 0 },
        params: strategy === "sma_crossover" ? { fast, slow } : null,
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
          <input className="num" value={symbol} onChange={(e) => setSymbol(e.target.value)} />
        </Labelled>
        <Labelled label="Provider" help="provider">
          <select value={provider} onChange={(e) => setProvider(e.target.value)}>
            <option value="yahoo">Yahoo Finance</option>
            <option value="binance">Binance (crypto)</option>
            <option value="stooq">Stooq (flaky)</option>
          </select>
        </Labelled>
        <Labelled label="Strategy" help="strategy">
          <select value={strategy} onChange={(e) => setStrategy(e.target.value)}>
            {(strategies.length ? strategies : ["buy_and_hold", "sma_crossover"]).map((s) => (
              <option key={s} value={s}>{s}</option>
            ))}
          </select>
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
        <button className="primary" onClick={run} disabled={running}>
          {running ? "Running…" : "Run simulation"}
        </button>
      </section>

      {error && (
        <div
          className="card"
          style={{ borderColor: "var(--down)", color: "var(--down)", background: "var(--down-soft)" }}
        >
          {error}
        </div>
      )}

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

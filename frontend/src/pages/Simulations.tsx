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
    new Intl.NumberFormat("en-GB", { style: "currency", currency: result?.currency ?? "GBP" }).format(n);

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
      <h2 style={{ margin: 0 }}>Simulations</h2>
      <p style={{ margin: 0, color: "#555" }}>
        Test how much money a strategy <em>would have</em> made over a historical window. UK fee
        model applies by default (0.5% stamp duty on buys).
      </p>

      <section
        style={{
          display: "grid",
          gridTemplateColumns: "repeat(auto-fit, minmax(180px, 1fr))",
          gap: 12,
        }}
      >
        <Labelled label="Symbol">
          <input value={symbol} onChange={(e) => setSymbol(e.target.value)} />
        </Labelled>
        <Labelled label="Provider">
          <select value={provider} onChange={(e) => setProvider(e.target.value)}>
            <option value="yahoo">yahoo</option>
            <option value="stooq">stooq</option>
            <option value="binance">binance</option>
          </select>
        </Labelled>
        <Labelled label="Strategy">
          <select value={strategy} onChange={(e) => setStrategy(e.target.value)}>
            {(strategies.length ? strategies : ["buy_and_hold", "sma_crossover"]).map((s) => (
              <option key={s} value={s}>{s}</option>
            ))}
          </select>
        </Labelled>
        <Labelled label="From"><input type="date" value={from} onChange={(e) => setFrom(e.target.value)} /></Labelled>
        <Labelled label="To"><input type="date" value={to} onChange={(e) => setTo(e.target.value)} /></Labelled>
        <Labelled label={`Capital (${config.defaultCurrency})`}>
          <input type="number" value={capital} onChange={(e) => setCapital(Number(e.target.value))} />
        </Labelled>
        <Labelled label="Stamp duty (fraction)">
          <input type="number" step="0.001" value={stampDuty} onChange={(e) => setStampDuty(Number(e.target.value))} />
        </Labelled>
        <Labelled label="Commission / trade">
          <input type="number" value={commission} onChange={(e) => setCommission(Number(e.target.value))} />
        </Labelled>
        {strategy === "sma_crossover" && (
          <>
            <Labelled label="Fast SMA">
              <input type="number" value={fast} onChange={(e) => setFast(Number(e.target.value))} />
            </Labelled>
            <Labelled label="Slow SMA">
              <input type="number" value={slow} onChange={(e) => setSlow(Number(e.target.value))} />
            </Labelled>
          </>
        )}
      </section>

      <button onClick={run} disabled={running} style={{ alignSelf: "flex-start", padding: "8px 16px" }}>
        {running ? "Running…" : "Run simulation"}
      </button>

      {error && <div style={{ color: "#b00020" }}>Error: {error}</div>}

      {result && (
        <>
          <div style={{ display: "flex", gap: 24, flexWrap: "wrap" }}>
            <Stat label="Final equity" value={ccy(result.finalEquity)} />
            <Stat label="P&L" value={ccy(result.finalEquity - result.initialCapital)} />
            <Stat label="Total return" value={`${result.totalReturnPct.toFixed(2)}%`} />
            <Stat label="CAGR" value={`${result.cagrPct.toFixed(2)}%`} />
            <Stat label="Max drawdown" value={`${result.maxDrawdownPct.toFixed(2)}%`} />
            <Stat label="Sharpe" value={result.sharpeRatio.toFixed(2)} />
            <Stat label="Trades" value={String(result.tradeCount)} />
          </div>

          <div style={{ height: 400, background: "#fafafa", borderRadius: 8, padding: 8 }}>
            <ResponsiveContainer width="100%" height="100%">
              <LineChart data={chartData}>
                <CartesianGrid stroke="#eee" />
                <XAxis dataKey="t" minTickGap={40} />
                <YAxis domain={["auto", "auto"]} />
                <Tooltip />
                <Line type="monotone" dataKey="equity" stroke="#0b3d91" dot={false} strokeWidth={1.5} />
              </LineChart>
            </ResponsiveContainer>
          </div>
        </>
      )}
    </div>
  );
}

function Labelled({ label, children }: { label: string; children: ReactNode }) {
  return (
    <label style={{ display: "flex", flexDirection: "column", gap: 4, fontSize: 12, color: "#555" }}>
      {label}
      {children}
    </label>
  );
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <div style={{ fontSize: 12, color: "#888" }}>{label}</div>
      <div style={{ fontSize: 18, fontWeight: 600 }}>{value}</div>
    </div>
  );
}

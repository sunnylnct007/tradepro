/**
 * SimulationView — the /desk "Simulate" cockpit view. Two modes:
 *
 *   Strategy  → the server-computed portfolio Monte Carlo (the trader's
 *               quant-engine ensemble), reusing EquityPipelineCard so we don't
 *               duplicate the validation surface. Pills switch strategy.
 *   Symbol    → a per-symbol Monte Carlo computed IN-BROWSER from the symbol's
 *               daily candle returns (block-bootstrap, mirrors the Python
 *               engine), so any instrument can be stress-projected without a
 *               backtest run.
 *
 * Both render through MonteCarloPanel, so the fan chart + chips + explainer are
 * identical whether the projection is server (portfolio) or client (symbol).
 */
import { useCallback, useEffect, useState } from "react";
import { api } from "../../api/client";
import { config } from "../../config";
import { EquityPipelineCard } from "../EquityPipelineCard";
import { MonteCarloPanel } from "./MonteCarloPanel";
import { runBlockBootstrapMC, returnsFromCloses, type MonteCarloData } from "../../util/monteCarlo";

const SEP = "#1b2233";

type Mode = "strategy" | "symbol";

// Strategies that have an equity-pipeline artifact (server MC). EquityPipelineCard
// shows an honest empty state for any that haven't been run yet.
const STRATEGIES = ["ichimoku_equity", "intraday_flat", "ichimoku_fx_mr"];
// Quick-pick symbols for the per-symbol MC (the trader can also type one).
const QUICK_SYMBOLS = ["SPY", "QQQ", "AAPL", "NVDA", "GLD", "TSLA"];
const YEARS = [3, 5, 10];
const SIMS = [500, 2000];

export function SimulationView() {
  const [mode, setMode] = useState<Mode>("strategy");
  const [strategy, setStrategy] = useState(STRATEGIES[0]);

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 14 }}>
      <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
        <h2 style={{ margin: 0, fontSize: 16, fontWeight: 700 }}>Simulation</h2>
        <span style={{ fontSize: 11, color: "var(--text-muted)" }}>
          Monte Carlo — range of plausible futures from past returns
        </span>
        <div style={{ marginLeft: "auto", display: "flex", gap: 4 }}>
          <Pill on={mode === "strategy"} onClick={() => setMode("strategy")}>Strategy</Pill>
          <Pill on={mode === "symbol"} onClick={() => setMode("symbol")}>Symbol</Pill>
        </div>
      </div>

      {/* Plain-English intro so the screen explains itself. */}
      <div style={{
        fontSize: 12, color: "var(--text-dim)", lineHeight: 1.6,
        background: "rgba(255,255,255,0.02)", border: "1px solid var(--border)",
        borderRadius: 8, padding: "10px 12px",
      }}>
        This asks: <strong style={{ color: "var(--text)" }}>“given how this has behaved in the past,
        where might it end up?”</strong> It replays history thousands of times in random order to build a
        cone of possible outcomes — a realistic <em>range</em>, not a prediction.{" "}
        <strong>Strategy</strong> projects a quant desk's whole book;{" "}
        <strong>Symbol</strong> projects any single ticker you type. Read the cone + the chips below it;
        full definitions sit under each chart.
      </div>

      {mode === "strategy" ? (
        <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
          <div style={{ display: "flex", gap: 4, flexWrap: "wrap" }}>
            {STRATEGIES.map((s) => (
              <Pill key={s} on={s === strategy} onClick={() => setStrategy(s)}>{s}</Pill>
            ))}
          </div>
          {/* The card carries the server-side ensemble MC (and the rest of the
              strategy-validation surface). Honest empty state if not yet run. */}
          <EquityPipelineCard strategy={strategy} />
        </div>
      ) : (
        <SymbolSimulation />
      )}
    </div>
  );
}

function SymbolSimulation() {
  const [input, setInput] = useState("SPY");
  const [symbol, setSymbol] = useState("SPY");
  const [years, setYears] = useState(5);
  const [nSims, setNSims] = useState(500);
  const [mc, setMc] = useState<MonteCarloData | null>(null);
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [obs, setObs] = useState(0);

  const run = useCallback(async (sym: string, yrs: number, sims: number) => {
    setLoading(true); setErr(null); setMc(null);
    try {
      const to = new Date();
      // ~6y of daily history so the bootstrap has a deep sample.
      const from = new Date(Date.now() - 6 * 365 * 24 * 3600 * 1000);
      const s = await api.candles({
        symbol: sym, provider: config.defaultProvider, interval: "1d",
        from: from.toISOString().slice(0, 10), to: to.toISOString().slice(0, 10),
      });
      const rets = returnsFromCloses((s.candles ?? []).map((c) => c.close));
      setObs(rets.length);
      const data = runBlockBootstrapMC(rets, { years: yrs, nSims: sims, initial: 10_000, seed: 42 });
      if (!data) { setErr(`Not enough price history for ${sym} to simulate (need ≥1 month of daily bars).`); return; }
      setMc(data);
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { void run(symbol, years, nSims); }, [symbol, years, nSims, run]);

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
      <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
        <form
          onSubmit={(e) => { e.preventDefault(); setSymbol(input.trim().toUpperCase()); }}
          style={{ display: "flex", gap: 6 }}
        >
          <input
            value={input}
            onChange={(e) => setInput(e.target.value)}
            placeholder="Symbol (e.g. SPY, AAPL, GBPUSD=X)"
            style={{
              background: "#0a0e17", border: `1px solid ${SEP}`, borderRadius: 6,
              padding: "6px 10px", color: "var(--text)", fontSize: 13, width: 220,
            }}
          />
          <button type="submit" className="primary" style={{ fontSize: 12, padding: "6px 12px" }}>Run</button>
        </form>
        <div style={{ display: "flex", gap: 4, flexWrap: "wrap" }}>
          {QUICK_SYMBOLS.map((s) => (
            <Pill key={s} on={s === symbol} onClick={() => { setInput(s); setSymbol(s); }}>{s}</Pill>
          ))}
        </div>
        <div style={{ marginLeft: "auto", display: "flex", gap: 8, alignItems: "center" }}>
          <Group label="Horizon">
            {YEARS.map((y) => <Pill key={y} on={y === years} onClick={() => setYears(y)}>{y}y</Pill>)}
          </Group>
          <Group label="Paths">
            {SIMS.map((n) => <Pill key={n} on={n === nSims} onClick={() => setNSims(n)}>{n.toLocaleString()}</Pill>)}
          </Group>
        </div>
      </div>

      {loading ? (
        <Note>Simulating {symbol}…</Note>
      ) : err ? (
        <Note tone="down">{err}</Note>
      ) : mc ? (
        <MonteCarloPanel mc={mc} title={`${symbol} — Monte Carlo`} subtitle={`${obs.toLocaleString()} daily returns sampled`} />
      ) : (
        <Note>Enter a symbol to simulate.</Note>
      )}
    </div>
  );
}

function Pill({ on, onClick, children }: { on: boolean; onClick: () => void; children: React.ReactNode }) {
  return (
    <button
      onClick={onClick}
      style={{
        fontSize: 12, padding: "5px 10px", borderRadius: 6, cursor: "pointer",
        border: `1px solid ${on ? "var(--accent, #4f8cff)" : SEP}`,
        background: on ? "rgba(79,140,255,0.12)" : "transparent",
        color: on ? "var(--accent, #4f8cff)" : "var(--text-dim)", fontWeight: on ? 700 : 500,
      }}
    >
      {children}
    </button>
  );
}

function Group({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div style={{ display: "flex", alignItems: "center", gap: 4 }}>
      <span style={{ fontSize: 10, color: "var(--text-muted)", textTransform: "uppercase", letterSpacing: "0.06em" }}>{label}</span>
      {children}
    </div>
  );
}

function Note({ children, tone }: { children: React.ReactNode; tone?: "down" }) {
  return (
    <div style={{
      padding: "20px 14px", textAlign: "center", fontSize: 13,
      color: tone === "down" ? "var(--down, #ef4444)" : "var(--text-muted)",
      border: `1px solid ${SEP}`, borderRadius: 8,
    }}>
      {children}
    </div>
  );
}

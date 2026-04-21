import type { ReactNode } from "react";
import { Link } from "react-router-dom";
import { useEffect, useState } from "react";
import { api } from "../api/client";
import type { ScanResult, ScanResultItem } from "../api/types";
import { Info } from "../components/Info";
import { StrategyPicker } from "../components/StrategyPicker";

export function Scanner() {
  const [watchlist, setWatchlist] = useState("uk");
  const [strategy, setStrategy] = useState("sma_crossover");
  const [fast, setFast] = useState(20);
  const [slow, setSlow] = useState(50);
  const [rsiLow, setRsiLow] = useState(30);
  const [rsiHigh, setRsiHigh] = useState(70);
  const [macdFast, setMacdFast] = useState(12);
  const [macdSlow, setMacdSlow] = useState(26);
  const [macdSignal, setMacdSignal] = useState(9);
  const [donchian, setDonchian] = useState(20);
  const [watchlistNames, setWatchlistNames] = useState<string[]>(["uk"]);
  const [result, setResult] = useState<ScanResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    api.watchlists().then((r) => setWatchlistNames(r.names)).catch(() => {});
  }, []);

  function paramsFor(): Record<string, number> | null {
    switch (strategy) {
      case "sma_crossover": return { fast, slow };
      case "rsi_mean_reversion": return { low: rsiLow, high: rsiHigh };
      case "macd_signal_cross": return { fast: macdFast, slow: macdSlow, signal: macdSignal };
      case "donchian_breakout": return { lookback: donchian };
      default: return null;
    }
  }

  async function scan() {
    setLoading(true);
    setError(null);
    try {
      const r = await api.scanSignals({
        watchlist,
        strategy,
        params: paramsFor(),
      });
      setResult(r);
    } catch (e) {
      setError(String(e));
    } finally {
      setLoading(false);
    }
  }

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 20 }}>
      <div>
        <h1 style={{ margin: 0, fontSize: 24 }}>What's worth buying or selling today?</h1>
        <p style={{ color: "var(--text-dim)", margin: "6px 0 0 0", maxWidth: 820 }}>
          Run a trading strategy across a defined watchlist. Results are ranked by signal
          strength. This is a decision aid, not advice.
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
        <Labelled label="Watchlist" help="watchlist">
          <select value={watchlist} onChange={(e) => setWatchlist(e.target.value)}>
            {watchlistNames.map((n) => (
              <option key={n} value={n}>{n}</option>
            ))}
          </select>
        </Labelled>
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
        <button className="primary" onClick={scan} disabled={loading}>
          {loading ? "Scanning…" : "Run scan"}
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
          <div style={{ fontSize: 12, color: "var(--text-muted)", display: "flex", gap: 16, flexWrap: "wrap" }}>
            <span>Generated <span className="num">{new Date(result.generatedAt).toLocaleString()}</span></span>
            <span>Watchlist <code>{result.watchlist}</code></span>
            <span>Strategy <code>{result.strategy}</code></span>
          </div>

          <div
            style={{
              display: "grid",
              gridTemplateColumns: "repeat(auto-fit, minmax(300px, 1fr))",
              gap: 16,
            }}
          >
            <Bucket title="Buy" tone="up" items={result.buys} />
            <Bucket title="Sell" tone="down" items={result.sells} />
            <Bucket title="Hold" tone="neutral" items={result.holds} />
          </div>

          {result.errors.length > 0 && (
            <details className="card" style={{ borderColor: "var(--down)" }}>
              <summary style={{ color: "var(--down)", cursor: "pointer" }}>
                {result.errors.length} provider error(s)
              </summary>
              <ul style={{ color: "var(--text-dim)", marginTop: 8 }}>
                {result.errors.map((e, i) => <li key={i}>{e}</li>)}
              </ul>
            </details>
          )}
        </>
      )}
    </div>
  );
}

function Bucket({ title, tone, items }: { title: string; tone: "up" | "down" | "neutral"; items: ScanResultItem[] }) {
  const colour = tone === "up" ? "var(--up)" : tone === "down" ? "var(--down)" : "var(--neutral)";
  return (
    <div className="card" style={{ borderTop: `3px solid ${colour}`, paddingTop: 14 }}>
      <div style={{ display: "flex", alignItems: "baseline", justifyContent: "space-between", marginBottom: 10 }}>
        <h3 style={{ margin: 0, color: colour, textTransform: "uppercase", letterSpacing: "0.08em", fontSize: 12 }}>
          {title}
        </h3>
        <span className="num" style={{ color: "var(--text-muted)", fontSize: 12 }}>{items.length}</span>
      </div>
      {items.length === 0 && <div style={{ color: "var(--text-muted)", fontSize: 13 }}>Nothing here.</div>}
      <ul style={{ margin: 0, padding: 0, listStyle: "none" }}>
        {items.map((i) => (
          <li
            key={i.symbol}
            style={{
              padding: "10px 0",
              borderBottom: "1px solid rgba(37, 50, 86, 0.4)",
            }}
          >
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline", gap: 8 }}>
              <Link
                to={`/signals?symbol=${encodeURIComponent(i.symbol)}`}
                style={{ color: "var(--text)", fontWeight: 600 }}
              >
                <span className="num">{i.symbol}</span>
              </Link>
              <span className="num" style={{ color: colour, fontSize: 12, fontWeight: 600 }}>
                {Math.round(i.decision.confidence * 100)}%
              </span>
            </div>
            <div style={{ fontSize: 12, color: "var(--text-dim)", marginTop: 2 }}>
              {i.label}
            </div>
            <ul style={{ margin: "4px 0 0 0", padding: 0, listStyle: "none" }}>
              {i.decision.reasons.slice(0, 2).map((r, idx) => (
                <li
                  key={idx}
                  style={{
                    fontSize: 11,
                    color: idx === 0 ? "var(--text-dim)" : "var(--text-muted)",
                    marginTop: 2,
                  }}
                >
                  · {r}
                </li>
              ))}
            </ul>
          </li>
        ))}
      </ul>
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

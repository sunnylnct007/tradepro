import type { ReactNode } from "react";
import { Link } from "react-router-dom";
import { useEffect, useState } from "react";
import { api } from "../api/client";
import type { ScanResult, ScanResultItem } from "../api/types";
import { config } from "../config";

export function Scanner() {
  const [watchlist, setWatchlist] = useState("uk");
  const [strategy, setStrategy] = useState("sma_crossover");
  const [fast, setFast] = useState(20);
  const [slow, setSlow] = useState(50);
  const [provider, setProvider] = useState(config.defaultProvider);
  const [watchlistNames, setWatchlistNames] = useState<string[]>(["uk"]);
  const [result, setResult] = useState<ScanResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    api.watchlists().then((r) => setWatchlistNames(r.names)).catch(() => {});
  }, []);

  async function scan() {
    setLoading(true);
    setError(null);
    try {
      const r = await api.scanSignals({
        watchlist,
        strategy,
        provider,
        params: strategy === "sma_crossover" ? { fast, slow } : null,
      });
      setResult(r);
    } catch (e) {
      setError(String(e));
    } finally {
      setLoading(false);
    }
  }

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
      <div>
        <h2 style={{ margin: 0 }}>What's worth buying or selling today?</h2>
        <p style={{ color: "#555", margin: "4px 0 0 0" }}>
          Runs the chosen trading strategy across a defined watchlist and ranks
          the names by signal strength. Start with the UK large-caps, then add
          your own lists. This is a decision aid, not advice.
        </p>
      </div>

      <section
        style={{
          display: "grid",
          gridTemplateColumns: "repeat(auto-fit, minmax(160px, 1fr))",
          gap: 12,
        }}
      >
        <Labelled label="Watchlist">
          <select value={watchlist} onChange={(e) => setWatchlist(e.target.value)}>
            {watchlistNames.map((n) => (
              <option key={n} value={n}>{n}</option>
            ))}
          </select>
        </Labelled>
        <Labelled label="Strategy">
          <select value={strategy} onChange={(e) => setStrategy(e.target.value)}>
            <option value="sma_crossover">sma_crossover</option>
            <option value="buy_and_hold">buy_and_hold</option>
          </select>
        </Labelled>
        <Labelled label="Provider">
          <select value={provider} onChange={(e) => setProvider(e.target.value)}>
            <option value="yahoo">yahoo</option>
            <option value="stooq">stooq</option>
            <option value="binance">binance</option>
          </select>
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

      <button onClick={scan} disabled={loading} style={{ alignSelf: "flex-start", padding: "8px 16px" }}>
        {loading ? "Scanning…" : "Run scan"}
      </button>

      {error && <div style={{ color: "#b00020" }}>Error: {error}</div>}

      {result && (
        <>
          <div style={{ fontSize: 12, color: "#888" }}>
            Generated {new Date(result.generatedAt).toLocaleString()} · watchlist <code>{result.watchlist}</code> · strategy <code>{result.strategy}</code>
          </div>

          <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(280px, 1fr))", gap: 16 }}>
            <Bucket title="Buy" colour="#0a7a34" items={result.buys} />
            <Bucket title="Sell" colour="#b00020" items={result.sells} />
            <Bucket title="Hold" colour="#666" items={result.holds} />
          </div>

          {result.errors.length > 0 && (
            <details>
              <summary style={{ color: "#b00020", cursor: "pointer" }}>
                {result.errors.length} provider error(s)
              </summary>
              <ul style={{ color: "#b00020" }}>
                {result.errors.map((e, i) => <li key={i}>{e}</li>)}
              </ul>
            </details>
          )}
        </>
      )}
    </div>
  );
}

function Bucket({ title, colour, items }: { title: string; colour: string; items: ScanResultItem[] }) {
  return (
    <div style={{ background: "#fafafa", borderRadius: 12, padding: 16, borderTop: `4px solid ${colour}` }}>
      <h3 style={{ margin: "0 0 8px 0", color: colour }}>{title} · {items.length}</h3>
      {items.length === 0 && <div style={{ color: "#888" }}>Nothing here.</div>}
      <ul style={{ margin: 0, padding: 0, listStyle: "none" }}>
        {items.map((i) => (
          <li key={i.symbol} style={{ padding: "8px 0", borderBottom: "1px solid #eee" }}>
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline" }}>
              <div>
                <strong>{i.symbol}</strong> <span style={{ color: "#777" }}>— {i.label}</span>
              </div>
              <div style={{ fontSize: 12, color: "#555" }}>
                {Math.round(i.decision.confidence * 100)}%
              </div>
            </div>
            <div style={{ fontSize: 12, color: "#555", marginTop: 2 }}>
              {i.decision.reasons[0]}
            </div>
            <div style={{ fontSize: 11, marginTop: 4 }}>
              <Link to={`/signals?symbol=${encodeURIComponent(i.symbol)}`}>details →</Link>
            </div>
          </li>
        ))}
      </ul>
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

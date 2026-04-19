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
        <Labelled label="Watchlist">
          <select value={watchlist} onChange={(e) => setWatchlist(e.target.value)}>
            {watchlistNames.map((n) => (
              <option key={n} value={n}>{n}</option>
            ))}
          </select>
        </Labelled>
        <Labelled label="Strategy">
          <select value={strategy} onChange={(e) => setStrategy(e.target.value)}>
            <option value="sma_crossover">SMA crossover</option>
            <option value="buy_and_hold">Buy &amp; hold</option>
          </select>
        </Labelled>
        <Labelled label="Provider">
          <select value={provider} onChange={(e) => setProvider(e.target.value)}>
            <option value="yahoo">Yahoo Finance</option>
            <option value="stooq">Stooq</option>
            <option value="binance">Binance (crypto)</option>
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
            <div style={{ fontSize: 11, color: "var(--text-muted)", marginTop: 4 }}>
              {i.decision.reasons[0]}
            </div>
          </li>
        ))}
      </ul>
    </div>
  );
}

function Labelled({ label, children }: { label: string; children: ReactNode }) {
  return (
    <label style={{ display: "flex", flexDirection: "column", gap: 5 }}>
      <span className="stat-label">{label}</span>
      {children}
    </label>
  );
}

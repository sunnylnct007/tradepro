import type { ReactNode } from "react";
import { useEffect, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { api } from "../api/client";
import type { SignalDecision, Watchlist } from "../api/types";
import { config } from "../config";

const actionColour: Record<SignalDecision["action"], string> = {
  BUY: "#0a7a34",
  SELL: "#b00020",
  HOLD: "#666",
};

export function Signals() {
  const [searchParams] = useSearchParams();
  const [watchlist, setWatchlist] = useState<Watchlist | null>(null);
  const [symbol, setSymbol] = useState(searchParams.get("symbol") ?? "BARC.L");
  const [strategy, setStrategy] = useState("sma_crossover");
  const [provider, setProvider] = useState(config.defaultProvider);
  const [fast, setFast] = useState(20);
  const [slow, setSlow] = useState(50);
  const [decision, setDecision] = useState<SignalDecision | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    api.ukWatchlist().then(setWatchlist).catch(() => {});
  }, []);

  async function evaluate() {
    setLoading(true);
    setError(null);
    try {
      const d = await api.evaluateSignal({
        symbol,
        provider,
        strategy,
        lookbackDays: 365,
        params: strategy === "sma_crossover" ? { fast, slow } : null,
      });
      setDecision(d);
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
    <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
      <h2 style={{ margin: 0 }}>Signals — should I buy or sell?</h2>
      <p style={{ margin: 0, color: "#555" }}>
        Pick a product and a strategy. The engine runs the strategy on the latest
        data and tells you what action it's suggesting <em>right now</em>, with
        the indicators behind the call. This is a decision aid, not advice.
      </p>

      <section
        style={{
          display: "grid",
          gridTemplateColumns: "repeat(auto-fit, minmax(180px, 1fr))",
          gap: 12,
        }}
      >
        {watchlist && (
          <Labelled label="From watchlist">
            <select value={symbol} onChange={(e) => setSymbol(e.target.value)}>
              {watchlist.items.map((i) => (
                <option key={i.symbol} value={i.symbol}>{i.label} ({i.symbol})</option>
              ))}
            </select>
          </Labelled>
        )}
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
            <option value="sma_crossover">sma_crossover</option>
            <option value="buy_and_hold">buy_and_hold</option>
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

      <button onClick={evaluate} disabled={loading} style={{ alignSelf: "flex-start", padding: "8px 16px" }}>
        {loading ? "Evaluating…" : "Get signal"}
      </button>

      {error && <div style={{ color: "#b00020" }}>Error: {error}</div>}

      {decision && (
        <div
          style={{
            display: "grid",
            gridTemplateColumns: "minmax(260px, 320px) 1fr",
            gap: 24,
            alignItems: "start",
          }}
        >
          <div
            style={{
              padding: 20,
              borderRadius: 12,
              background: "#fafafa",
              borderLeft: `6px solid ${actionColour[decision.action]}`,
            }}
          >
            <div style={{ fontSize: 12, color: "#888" }}>Recommendation</div>
            <div style={{ fontSize: 36, fontWeight: 700, color: actionColour[decision.action] }}>
              {decision.action}
            </div>
            <div style={{ fontSize: 12, color: "#555", marginTop: 4 }}>
              Confidence {Math.round(decision.confidence * 100)}% · as of {decision.asOf.slice(0, 10)}
            </div>
            <div style={{ marginTop: 12, fontSize: 13, color: "#333" }}>
              Strategy: <code>{decision.strategy}</code>
            </div>
            {(decision.suggestedStopLossPct || decision.suggestedTargetPct) && (
              <div style={{ marginTop: 8, fontSize: 13 }}>
                {decision.suggestedStopLossPct && (
                  <div>Suggested stop-loss: −{decision.suggestedStopLossPct}%</div>
                )}
                {decision.suggestedTargetPct && (
                  <div>Suggested target: +{decision.suggestedTargetPct}%</div>
                )}
              </div>
            )}
          </div>

          <div>
            <h3 style={{ margin: "0 0 8px 0" }}>Why</h3>
            <ul style={{ margin: 0, paddingLeft: 18, lineHeight: 1.7 }}>
              {decision.reasons.map((r, i) => (
                <li key={i}>{r}</li>
              ))}
            </ul>

            <h3 style={{ margin: "16px 0 8px 0" }}>Indicators</h3>
            <div style={{ display: "flex", gap: 20, flexWrap: "wrap" }}>
              <Stat label="Last close" value={fmt(ind?.lastClose)} />
              <Stat label="SMA 20" value={fmt(ind?.sma20)} />
              <Stat label="SMA 50" value={fmt(ind?.sma50)} />
              <Stat label="SMA 200" value={fmt(ind?.sma200)} />
              <Stat label="RSI 14" value={fmt(ind?.rsi14, 1)} />
              <Stat label="vs 52w high" value={ind?.priceVs52wHighPct != null ? `${fmt(ind.priceVs52wHighPct, 1)}%` : "—"} />
              <Stat label="vs 52w low" value={ind?.priceVs52wLowPct != null ? `${fmt(ind.priceVs52wLowPct, 1)}%` : "—"} />
            </div>
          </div>
        </div>
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
      <div style={{ fontSize: 16, fontWeight: 600 }}>{value}</div>
    </div>
  );
}

import type { ReactNode } from "react";
import { useEffect, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { api } from "../api/client";
import type { SignalDecision, Watchlist } from "../api/types";
import { config } from "../config";

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
    <div style={{ display: "flex", flexDirection: "column", gap: 20 }}>
      <div>
        <h1 style={{ margin: 0, fontSize: 24 }}>Signal detail</h1>
        <p style={{ color: "var(--text-dim)", margin: "6px 0 0 0" }}>
          Single-symbol recommendation with the indicators behind the call.
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
        {watchlist && (
          <Labelled label="Watchlist pick">
            <select value={symbol} onChange={(e) => setSymbol(e.target.value)}>
              {watchlist.items.map((i) => (
                <option key={i.symbol} value={i.symbol}>{i.label}</option>
              ))}
            </select>
          </Labelled>
        )}
        <Labelled label="Symbol">
          <input className="num" value={symbol} onChange={(e) => setSymbol(e.target.value)} />
        </Labelled>
        <Labelled label="Provider">
          <select value={provider} onChange={(e) => setProvider(e.target.value)}>
            <option value="yahoo">Yahoo Finance</option>
            <option value="stooq">Stooq</option>
            <option value="binance">Binance</option>
          </select>
        </Labelled>
        <Labelled label="Strategy">
          <select value={strategy} onChange={(e) => setStrategy(e.target.value)}>
            <option value="sma_crossover">SMA crossover</option>
            <option value="buy_and_hold">Buy &amp; hold</option>
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
        <button className="primary" onClick={evaluate} disabled={loading}>
          {loading ? "Evaluating…" : "Get signal"}
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
            <div style={{ fontSize: 12, color: "var(--text-dim)", marginTop: 4 }}>
              Confidence <span className="num">{Math.round(decision.confidence * 100)}%</span>
              {" · "}
              as of <span className="num">{decision.asOf.slice(0, 10)}</span>
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
                Reasoning
              </h3>
              <ul style={{ margin: 0, paddingLeft: 18, lineHeight: 1.8, color: "var(--text)" }}>
                {decision.reasons.map((r, i) => <li key={i}>{r}</li>)}
              </ul>
            </div>
            <div className="card">
              <h3 style={{ margin: "0 0 12px 0", fontSize: 13, textTransform: "uppercase", letterSpacing: "0.08em", color: "var(--text-muted)" }}>
                Indicators
              </h3>
              <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(120px, 1fr))", gap: 14 }}>
                <Stat label="Last close" value={fmt(ind?.lastClose)} />
                <Stat label="SMA 20" value={fmt(ind?.sma20)} />
                <Stat label="SMA 50" value={fmt(ind?.sma50)} />
                <Stat label="SMA 200" value={fmt(ind?.sma200)} />
                <Stat label="RSI 14" value={fmt(ind?.rsi14, 1)} />
                <Stat
                  label="vs 52w high"
                  value={ind?.priceVs52wHighPct != null ? `${fmt(ind.priceVs52wHighPct, 1)}%` : "—"}
                  tone={ind?.priceVs52wHighPct != null ? (ind.priceVs52wHighPct >= -3 ? "up" : "down") : undefined}
                />
                <Stat
                  label="vs 52w low"
                  value={ind?.priceVs52wLowPct != null ? `${fmt(ind.priceVs52wLowPct, 1)}%` : "—"}
                  tone={ind?.priceVs52wLowPct != null ? (ind.priceVs52wLowPct > 20 ? "up" : undefined) : undefined}
                />
              </div>
            </div>
          </div>
        </div>
      )}
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

function Stat({ label, value, tone }: { label: string; value: string; tone?: "up" | "down" }) {
  const colour = tone === "up" ? "var(--up)" : tone === "down" ? "var(--down)" : "var(--text)";
  return (
    <div>
      <div className="stat-label">{label}</div>
      <div className="stat-value" style={{ color: colour }}>{value}</div>
    </div>
  );
}

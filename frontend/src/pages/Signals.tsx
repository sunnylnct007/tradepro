import type { ReactNode } from "react";
import { useEffect, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { api } from "../api/client";
import type { SignalDecision, Watchlist } from "../api/types";
import { config } from "../config";
import { Info } from "../components/Info";

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
        provider: config.defaultProvider,
        strategy,
        lookbackDays: 365,
        params:
          strategy === "sma_crossover"
            ? { fast, slow }
            : strategy === "rsi_mean_reversion"
              ? { low: rsiLow, high: rsiHigh }
              : null,
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
          <Labelled label="Watchlist pick" help="watchlist">
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
        <Labelled label="Strategy" help="strategy">
          <select value={strategy} onChange={(e) => setStrategy(e.target.value)}>
            <option value="sma_crossover">SMA crossover</option>
            <option value="rsi_mean_reversion">RSI mean-reversion</option>
            <option value="buy_and_hold">Buy &amp; hold</option>
          </select>
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
          </div>
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

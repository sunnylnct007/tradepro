import { useEffect, useState } from "react";
import { config } from "../config";
import { getIdToken } from "../firebase";

/** UI-editable runtime config.
 *
 * Today: sentiment-demotion thresholds. Future: watchlists, regime
 * windows, fee presets, LLM model picker (each adds a section here).
 *
 * Read live values on mount; PUT on save. The Mac comparator picks
 * up the new values on the next run — no Python redeploy needed.
 * The Compare page's LLM bar always shows the *literal* numbers
 * that fired so changes are immediately visible. */

interface SentimentSettings {
  meanSentimentThreshold: number;
  minMaterialNegativeCount: number;
  lookbackDays: number;
}

type PlacementMode = "auto" | "manual";

interface PaperSettings {
  placementMode: PlacementMode;
}

interface IntradayGate {
  minRiskRewardRatio: number;
  maxSpreadPct: number;
  minConfidence: number;
}

interface IntradayStrategySettings {
  enabled: boolean;
  /** Param overrides merged on top of the strategy's compiled
   * default_params(). Empty object → use all defaults. */
  params?: Record<string, unknown>;
}

interface IntradaySettings {
  symbols: string[];
  scanIntervalMinutes: number;
  sessionStartUtc: string;
  sessionEndUtc: string;
  gate: IntradayGate;
  autoPlaceConfidenceThreshold: number;
  riskPerTradeUsd: number;
  /** Per-strategy on/off + param overrides. Missing entry =
   * "auto-enable with defaults". Lives separately from the catalog
   * (sourced from /api/paper/strategies). */
  strategies?: Record<string, IntradayStrategySettings>;
}

/** Catalog entry from /api/paper/strategies. Pushed by the Mac via
 * tradepro-paper-strategies-push; the UI uses it to enumerate
 * available strategies + show their default params. */
interface CatalogStrategy {
  name: string;
  class: string;
  summary?: string;
  default_params: Record<string, unknown>;
}

interface CatalogPayload {
  count: number;
  strategies: CatalogStrategy[];
}

interface AppSettings {
  sentiment: SentimentSettings;
  paper?: PaperSettings;
  intraday?: IntradaySettings;
  updatedAtUtc: string;
}

const DEFAULT_SENTIMENT: SentimentSettings = {
  meanSentimentThreshold: -0.30,
  minMaterialNegativeCount: 2,
  lookbackDays: 7,
};

const DEFAULT_PAPER: PaperSettings = {
  placementMode: "manual",
};

const DEFAULT_INTRADAY: IntradaySettings = {
  symbols: [],
  scanIntervalMinutes: 1,
  sessionStartUtc: "13:30",
  sessionEndUtc: "20:00",
  gate: {
    minRiskRewardRatio: 2.0,
    maxSpreadPct: 0.3,
    minConfidence: 0.70,
  },
  autoPlaceConfidenceThreshold: 0.85,
  riskPerTradeUsd: 100,
  strategies: undefined,
};

export function Settings() {
  const [data, setData] = useState<AppSettings | null>(null);
  const [draftSentiment, setDraftSentiment] = useState<SentimentSettings>(DEFAULT_SENTIMENT);
  const [draftPaper, setDraftPaper] = useState<PaperSettings>(DEFAULT_PAPER);
  const [draftIntraday, setDraftIntraday] = useState<IntradaySettings>(DEFAULT_INTRADAY);
  const [symbolInput, setSymbolInput] = useState("");
  const [catalog, setCatalog] = useState<CatalogStrategy[] | null>(null);
  const [catalogError, setCatalogError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [savedAt, setSavedAt] = useState<string | null>(null);

  useEffect(() => {
    fetch(new URL("/api/settings", config.apiBaseUrl).toString())
      .then((r) => r.ok ? r.json() : Promise.reject(`${r.status}`))
      .then((d: AppSettings) => {
        setData(d);
        setDraftSentiment(d.sentiment);
        setDraftPaper(d.paper ?? DEFAULT_PAPER);
        setDraftIntraday(d.intraday ?? DEFAULT_INTRADAY);
      })
      .catch((e) => setError(`Couldn't load settings: ${e}`));
  }, []);

  useEffect(() => {
    // Strategy catalog is pushed from the Mac via
    // tradepro-paper-strategies-push; 404 until the first push.
    // Surface the empty state so the user knows what to run.
    fetch(new URL("/api/paper/strategies", config.apiBaseUrl).toString())
      .then(async (r) => {
        if (r.status === 404) {
          setCatalogError(
            "No strategy catalog yet. Run `uv run tradepro-paper-strategies-push` on the Mac to populate.",
          );
          return null;
        }
        if (!r.ok) {
          setCatalogError(`Catalog load failed: HTTP ${r.status}`);
          return null;
        }
        return r.json() as Promise<{ payload?: CatalogPayload } | CatalogPayload>;
      })
      .then((body) => {
        if (!body) return;
        const payload: CatalogPayload | undefined =
          "payload" in body ? body.payload : (body as CatalogPayload);
        if (payload?.strategies) {
          setCatalog(payload.strategies);
          setCatalogError(null);
        }
      })
      .catch((e) => setCatalogError(`Catalog load failed: ${e}`));
  }, []);

  const dirty =
    !data
    || data.sentiment.meanSentimentThreshold !== draftSentiment.meanSentimentThreshold
    || data.sentiment.minMaterialNegativeCount !== draftSentiment.minMaterialNegativeCount
    || data.sentiment.lookbackDays !== draftSentiment.lookbackDays
    || (data.paper?.placementMode ?? DEFAULT_PAPER.placementMode) !== draftPaper.placementMode
    || JSON.stringify(data.intraday ?? DEFAULT_INTRADAY) !== JSON.stringify(draftIntraday);

  async function save() {
    setSaving(true);
    setError(null);
    try {
      const token = await getIdToken();
      const headers: Record<string, string> = { "content-type": "application/json" };
      if (token) headers["authorization"] = `Bearer ${token}`;
      const resp = await fetch(new URL("/api/settings", config.apiBaseUrl).toString(), {
        method: "PUT",
        headers,
        body: JSON.stringify({
          sentiment: draftSentiment,
          paper: draftPaper,
          intraday: draftIntraday,
          updatedAtUtc: new Date().toISOString(),
        }),
      });
      if (!resp.ok) {
        const body = await resp.text();
        throw new Error(`${resp.status}: ${body.slice(0, 200)}`);
      }
      const fresh: AppSettings = await resp.json();
      setData(fresh);
      setDraftSentiment(fresh.sentiment);
      setDraftPaper(fresh.paper ?? DEFAULT_PAPER);
      setDraftIntraday(fresh.intraday ?? DEFAULT_INTRADAY);
      setSavedAt(new Date().toLocaleTimeString());
    } catch (e) {
      setError(String(e));
    } finally {
      setSaving(false);
    }
  }

  function reset() {
    if (data) {
      setDraftSentiment(data.sentiment);
      setDraftPaper(data.paper ?? DEFAULT_PAPER);
      setDraftIntraday(data.intraday ?? DEFAULT_INTRADAY);
    }
  }

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 18, maxWidth: 880 }}>
      <div>
        <h1 style={{ margin: 0, fontSize: 24 }}>Settings</h1>
        <p style={{ color: "var(--text-dim)", margin: "6px 0 0 0" }}>
          Tune the rules the comparator applies. Saved values are picked up
          on the next <code>tradepro-compare</code> run by the Strategy
          Engine — no redeploy needed. The Compare page's LLM banner shows
          the literal numbers that fired, so changes are visible immediately.
        </p>
      </div>

      {error && (
        <div className="card" style={{ borderColor: "var(--down)", color: "var(--down)", padding: "10px 14px" }}>
          {error}
        </div>
      )}

      {savedAt && !dirty && (
        <div className="card" style={{ borderLeft: "3px solid var(--up)", color: "var(--text-dim)", padding: "8px 12px", fontSize: 12 }}>
          Saved at {savedAt}.
        </div>
      )}

      <section className="card" style={{ padding: "18px 20px", display: "flex", flexDirection: "column", gap: 16 }}>
        <div>
          <h2 style={{ margin: 0, fontSize: 16 }}>Sentiment demotion</h2>
          <p style={{ margin: "4px 0 0 0", color: "var(--text-dim)", fontSize: 13 }}>
            When the rule engine would say <strong>BUY</strong>, downgrade to{" "}
            <strong>WAIT</strong> if the news sentiment trend is sharply
            negative. The numbers below define "sharply".
          </p>
        </div>

        <Field
          label="Mean sentiment threshold"
          help="LLM-aggregated sentiment over the lookback window. -1.0 = very negative, +1.0 = very positive. Demotion fires when mean ≤ this value. Range: -1.0 to 1.0."
        >
          <input
            type="number"
            step="0.05"
            min={-1}
            max={1}
            value={draftSentiment.meanSentimentThreshold}
            onChange={(e) => setDraftSentiment({ ...draftSentiment, meanSentimentThreshold: Number(e.target.value) })}
            style={{ width: 120 }}
          />
        </Field>

        <Field
          label="Min material-negative headlines"
          help="Even if the mean is bad, demotion requires at least N headlines flagged as 'material' (i.e. price-moving) AND with sentiment ≤ -0.5. Filters noise from a single bearish op-ed. Range: 0 to 50."
        >
          <input
            type="number"
            step="1"
            min={0}
            max={50}
            value={draftSentiment.minMaterialNegativeCount}
            onChange={(e) => setDraftSentiment({ ...draftSentiment, minMaterialNegativeCount: Number(e.target.value) })}
            style={{ width: 80 }}
          />
        </Field>

        <Field
          label="Lookback days"
          help="Rolling window the rule looks at. 7 = last week's news. Smaller = more reactive but choppier. Range: 1 to 60."
        >
          <input
            type="number"
            step="1"
            min={1}
            max={60}
            value={draftSentiment.lookbackDays}
            onChange={(e) => setDraftSentiment({ ...draftSentiment, lookbackDays: Number(e.target.value) })}
            style={{ width: 80 }}
          />
        </Field>

        <div
          style={{
            padding: "10px 12px",
            background: "rgba(255,255,255,0.04)",
            borderLeft: "3px solid var(--neutral)",
            borderRadius: 4,
            fontSize: 12,
            color: "var(--text-dim)",
          }}
        >
          <strong style={{ color: "var(--text)" }}>Active rule (preview):</strong>{" "}
          BUY → WAIT when {draftSentiment.lookbackDays}-day rolling mean sentiment{" "}
          <code>≤ {draftSentiment.meanSentimentThreshold}</code>{" "}
          AND <code>≥ {draftSentiment.minMaterialNegativeCount}</code> material-negative
          headlines.
        </div>

      </section>

      <section className="card" style={{ padding: "18px 20px", display: "flex", flexDirection: "column", gap: 12 }}>
        <div>
          <h2 style={{ margin: 0, fontSize: 16 }}>Paper-trading placement mode</h2>
          <p style={{ margin: "4px 0 0 0", color: "var(--text-dim)", fontSize: 13 }}>
            Controls what happens when a paper strategy emits an order intent.
            Saved values are picked up by the Mac engine on the next
            <code> tradepro-paper</code> run when <code>--placement-mode</code>
            {" "}isn't passed explicitly on the command line.
          </p>
        </div>
        <div style={{ display: "flex", gap: 8 }}>
          {(["auto", "manual"] as const).map((m) => (
            <button
              key={m}
              onClick={() => setDraftPaper({ placementMode: m })}
              className={draftPaper.placementMode === m ? "primary" : ""}
              style={{
                fontSize: 13,
                padding: "8px 16px",
                fontWeight: draftPaper.placementMode === m ? 600 : 400,
              }}
            >
              {m === "auto" ? "Auto · post directly to T212" : "Manual · queue for Approve/Reject"}
            </button>
          ))}
        </div>
        <div
          style={{
            padding: "10px 12px",
            background: "rgba(255,255,255,0.04)",
            borderLeft: `3px solid ${draftPaper.placementMode === "auto" ? "var(--down)" : "var(--neutral)"}`,
            borderRadius: 4,
            fontSize: 12,
            color: "var(--text-dim)",
          }}
        >
          {draftPaper.placementMode === "auto" ? (
            <>
              <strong style={{ color: "var(--down)" }}>Auto mode active:</strong>{" "}
              Strategies will post orders to T212 immediately. Use only when
              you're confident the strategy + risk caps are tuned — there's no
              human-in-the-loop step.
            </>
          ) : (
            <>
              <strong style={{ color: "var(--text)" }}>Manual mode active:</strong>{" "}
              Strategies push intents to the Pending Orders tab on the Paper
              page. Nothing reaches T212 until you click Approve. Safest
              default for testing a new strategy.
            </>
          )}
        </div>
      </section>

      <section className="card" style={{ padding: "18px 20px", display: "flex", flexDirection: "column", gap: 14 }}>
        <div>
          <h2 style={{ margin: 0, fontSize: 16 }}>Intraday automation</h2>
          <p style={{ margin: "4px 0 0 0", color: "var(--text-dim)", fontSize: 13 }}>
            Knobs the continuous-mode engine reads on every scan cycle.
            Change them live — no Mac restart needed. The engine only
            runs during the session window and only fires an order when
            ALL three pre-trade gate conditions pass.
          </p>
        </div>

        <Field
          label="Watchlist"
          help="Tickers being scanned. Press Enter or comma to add. Click × to remove. Leave empty to pause the engine without touching the rest of the config."
        >
          <div style={{ display: "flex", flexWrap: "wrap", gap: 6, marginBottom: 6 }}>
            {draftIntraday.symbols.length === 0 && (
              <span style={{ fontSize: 12, color: "var(--text-muted)", fontStyle: "italic" }}>
                Empty — engine will skip every scan until you add a symbol.
              </span>
            )}
            {draftIntraday.symbols.map((s) => (
              <span
                key={s}
                style={{
                  display: "inline-flex",
                  alignItems: "center",
                  gap: 6,
                  padding: "3px 10px",
                  background: "rgba(255,255,255,0.06)",
                  border: "1px solid var(--border)",
                  borderRadius: 12,
                  fontSize: 12,
                  fontFamily: "var(--mono, monospace)",
                }}
              >
                {s}
                <button
                  type="button"
                  onClick={() => setDraftIntraday({
                    ...draftIntraday,
                    symbols: draftIntraday.symbols.filter((x) => x !== s),
                  })}
                  style={{
                    background: "transparent",
                    border: "none",
                    color: "var(--text-muted)",
                    cursor: "pointer",
                    padding: 0,
                    fontSize: 14,
                    lineHeight: 1,
                  }}
                  aria-label={`Remove ${s}`}
                >
                  ×
                </button>
              </span>
            ))}
          </div>
          <input
            type="text"
            value={symbolInput}
            placeholder="e.g. AAPL"
            onChange={(e) => setSymbolInput(e.target.value.toUpperCase())}
            onKeyDown={(e) => {
              if (e.key === "Enter" || e.key === ",") {
                e.preventDefault();
                const next = symbolInput.trim().replace(/,$/, "");
                if (next && !draftIntraday.symbols.includes(next)) {
                  setDraftIntraday({
                    ...draftIntraday,
                    symbols: [...draftIntraday.symbols, next],
                  });
                }
                setSymbolInput("");
              }
            }}
            style={{ width: 160, textTransform: "uppercase" }}
          />
        </Field>

        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 14 }}>
          <Field
            label="Scan interval (minutes)"
            help="How often the engine evaluates each watched symbol. 1 = every minute (default). Higher = lower noise, lower fill rate."
          >
            <input
              type="number"
              min={1}
              max={60}
              step={1}
              value={draftIntraday.scanIntervalMinutes}
              onChange={(e) => setDraftIntraday({
                ...draftIntraday,
                scanIntervalMinutes: Number(e.target.value),
              })}
              style={{ width: 100 }}
            />
          </Field>

          <Field
            label="Risk per trade (USD)"
            help="Position size is set so a stop-loss hit costs at most this much. Lower = smaller positions."
          >
            <input
              type="number"
              min={1}
              step={5}
              value={draftIntraday.riskPerTradeUsd}
              onChange={(e) => setDraftIntraday({
                ...draftIntraday,
                riskPerTradeUsd: Number(e.target.value),
              })}
              style={{ width: 100 }}
            />
          </Field>

          <Field
            label="Session start (UTC)"
            help="HH:mm. Engine sleeps before this time. Default 13:30 = US market open during DST."
          >
            <input
              type="time"
              value={draftIntraday.sessionStartUtc}
              onChange={(e) => setDraftIntraday({
                ...draftIntraday,
                sessionStartUtc: e.target.value,
              })}
              style={{ width: 120 }}
            />
          </Field>

          <Field
            label="Session end (UTC)"
            help="HH:mm. Engine stops after this time. Default 20:00 = US market close during DST."
          >
            <input
              type="time"
              value={draftIntraday.sessionEndUtc}
              onChange={(e) => setDraftIntraday({
                ...draftIntraday,
                sessionEndUtc: e.target.value,
              })}
              style={{ width: 120 }}
            />
          </Field>
        </div>

        <div style={{ borderTop: "1px solid var(--border)", paddingTop: 12 }}>
          <div style={{ fontSize: 13, fontWeight: 600, marginBottom: 4 }}>Pre-trade gate</div>
          <div style={{ fontSize: 11, color: "var(--text-muted)", marginBottom: 10 }}>
            ALL three must pass before the engine even considers placing the order.
          </div>
          <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr 1fr", gap: 14 }}>
            <Field
              label="Min R:R ratio"
              help="Reward / risk. 2.0 means the take-profit target is at least 2× the stop-loss distance."
            >
              <input
                type="number"
                min={0.5}
                step={0.1}
                value={draftIntraday.gate.minRiskRewardRatio}
                onChange={(e) => setDraftIntraday({
                  ...draftIntraday,
                  gate: { ...draftIntraday.gate, minRiskRewardRatio: Number(e.target.value) },
                })}
                style={{ width: 90 }}
              />
            </Field>

            <Field
              label="Max spread (%)"
              help="Bid/ask spread as % of mid price. Skip the trade if wider — execution will eat too much."
            >
              <input
                type="number"
                min={0}
                step={0.05}
                value={draftIntraday.gate.maxSpreadPct}
                onChange={(e) => setDraftIntraday({
                  ...draftIntraday,
                  gate: { ...draftIntraday.gate, maxSpreadPct: Number(e.target.value) },
                })}
                style={{ width: 90 }}
              />
            </Field>

            <Field
              label="Min confidence"
              help="Strategy emitter's confidence in [0, 1]. Skip if below."
            >
              <input
                type="number"
                min={0}
                max={1}
                step={0.05}
                value={draftIntraday.gate.minConfidence}
                onChange={(e) => setDraftIntraday({
                  ...draftIntraday,
                  gate: { ...draftIntraday.gate, minConfidence: Number(e.target.value) },
                })}
                style={{ width: 90 }}
              />
            </Field>
          </div>
        </div>

        <Field
          label="Auto-place confidence threshold"
          help="Orders at or above this confidence go straight to T212 demo. Below it, they queue as Pending for human Approve/Reject. Range: 0 to 1."
        >
          <input
            type="number"
            min={0}
            max={1}
            step={0.05}
            value={draftIntraday.autoPlaceConfidenceThreshold}
            onChange={(e) => setDraftIntraday({
              ...draftIntraday,
              autoPlaceConfidenceThreshold: Number(e.target.value),
            })}
            style={{ width: 100 }}
          />
        </Field>

        <div style={{ borderTop: "1px solid var(--border)", paddingTop: 12 }}>
          <div style={{ display: "flex", alignItems: "baseline", justifyContent: "space-between", marginBottom: 4 }}>
            <div style={{ fontSize: 13, fontWeight: 600 }}>Strategies</div>
            <div style={{ fontSize: 11, color: "var(--text-muted)" }}>
              Source: Mac registry → /api/paper/strategies
            </div>
          </div>
          <div style={{ fontSize: 11, color: "var(--text-muted)", marginBottom: 10 }}>
            Toggle individual strategies on/off. A new strategy added to the Mac registry auto-enables on first scan — no UI work needed to plug it in. Strategies without an explicit toggle inherit "on".
          </div>
          {catalogError && (
            <div style={{
              fontSize: 12,
              padding: "8px 10px",
              borderLeft: "3px solid var(--neutral)",
              background: "rgba(255,255,255,0.04)",
              borderRadius: 4,
              color: "var(--text-dim)",
            }}>
              {catalogError}
            </div>
          )}
          {catalog && catalog.length > 0 && (
            <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
              {catalog.map((s) => {
                // Skip the registry's back-compat alias — same class,
                // shown once under its canonical name only.
                if (s.name === "opening_range_breakout") return null;
                const cfg = draftIntraday.strategies?.[s.name];
                const isEnabled = cfg?.enabled ?? true;   // default-on
                return (
                  <div
                    key={s.name}
                    style={{
                      display: "flex",
                      alignItems: "center",
                      gap: 10,
                      padding: "8px 10px",
                      background: "rgba(255,255,255,0.02)",
                      borderRadius: 4,
                      border: "1px solid var(--border)",
                    }}
                  >
                    <input
                      type="checkbox"
                      checked={isEnabled}
                      onChange={(e) => {
                        const next = { ...(draftIntraday.strategies ?? {}) };
                        next[s.name] = {
                          ...(next[s.name] ?? { params: {} }),
                          enabled: e.target.checked,
                        };
                        setDraftIntraday({ ...draftIntraday, strategies: next });
                      }}
                      style={{ cursor: "pointer" }}
                    />
                    <div style={{ flex: 1, minWidth: 0 }}>
                      <div
                        style={{
                          fontSize: 13,
                          fontWeight: 600,
                          fontFamily: "var(--mono, monospace)",
                          color: isEnabled ? "var(--text)" : "var(--text-muted)",
                        }}
                      >
                        {s.name}
                      </div>
                      {s.summary && (
                        <div
                          style={{
                            fontSize: 11,
                            color: "var(--text-muted)",
                            marginTop: 2,
                            overflow: "hidden",
                            textOverflow: "ellipsis",
                            whiteSpace: "nowrap",
                          }}
                          title={s.summary}
                        >
                          {s.summary}
                        </div>
                      )}
                    </div>
                    <details style={{ fontSize: 11 }}>
                      <summary style={{ cursor: "pointer", color: "var(--text-muted)" }}>
                        defaults
                      </summary>
                      <pre style={{
                        margin: "6px 0 0 0",
                        padding: "6px 8px",
                        background: "rgba(0,0,0,0.2)",
                        borderRadius: 4,
                        fontSize: 10,
                        maxWidth: 320,
                        overflowX: "auto",
                      }}>
                        {JSON.stringify(s.default_params, null, 2)}
                      </pre>
                    </details>
                  </div>
                );
              })}
            </div>
          )}
        </div>

        <div
          style={{
            padding: "10px 12px",
            background: "rgba(255,255,255,0.04)",
            borderLeft: "3px solid var(--neutral)",
            borderRadius: 4,
            fontSize: 12,
            color: "var(--text-dim)",
          }}
        >
          <strong style={{ color: "var(--text)" }}>Active rule (preview):</strong>{" "}
          Scan {draftIntraday.symbols.length} symbol{draftIntraday.symbols.length === 1 ? "" : "s"}
          {" "}every {draftIntraday.scanIntervalMinutes}m between
          {" "}{draftIntraday.sessionStartUtc}–{draftIntraday.sessionEndUtc} UTC.
          Place only if R:R ≥ {draftIntraday.gate.minRiskRewardRatio}
          {" "}AND spread &lt; {draftIntraday.gate.maxSpreadPct}%
          {" "}AND confidence ≥ {draftIntraday.gate.minConfidence}.
          Auto-place when confidence ≥ {draftIntraday.autoPlaceConfidenceThreshold}, else queue.
        </div>
      </section>

      <section className="card" style={{ padding: "14px 18px", color: "var(--text-dim)", fontSize: 12 }}>
        <strong style={{ color: "var(--text)" }}>Coming soon:</strong>{" "}
        per-strategy enable/disable + params, custom regime windows, fee-model
        presets per broker, LLM model selection. See{" "}
        <a href="https://github.com/sunnylnct007/tradepro/blob/main/ROADMAP.md" target="_blank" rel="noreferrer" style={{ color: "var(--text)" }}>
          ROADMAP → Phase 7
        </a>.
      </section>

      <div
        style={{
          position: "sticky",
          bottom: 0,
          background: "var(--bg)",
          borderTop: "1px solid var(--border)",
          padding: "12px 0",
          display: "flex",
          gap: 10,
          alignItems: "center",
          zIndex: 5,
        }}
      >
        <button className="primary" onClick={save} disabled={!dirty || saving}>
          {saving ? "Saving…" : "Save"}
        </button>
        <button onClick={reset} disabled={!dirty || saving}>
          Reset
        </button>
        {dirty && !saving && (
          <span style={{ fontSize: 12, color: "var(--text-muted)" }}>
            Unsaved changes
          </span>
        )}
        {data && (
          <span style={{ marginLeft: "auto", color: "var(--text-muted)", fontSize: 11 }}>
            Last updated: {new Date(data.updatedAtUtc).toLocaleString()}
          </span>
        )}
      </div>
    </div>
  );
}

function Field({
  label,
  help,
  children,
}: {
  label: string;
  help: string;
  children: React.ReactNode;
}) {
  return (
    <label style={{ display: "flex", flexDirection: "column", gap: 4 }}>
      <span style={{ fontSize: 13, fontWeight: 600 }}>{label}</span>
      <span style={{ fontSize: 11, color: "var(--text-muted)", maxWidth: 600 }}>{help}</span>
      <div style={{ marginTop: 4 }}>{children}</div>
    </label>
  );
}

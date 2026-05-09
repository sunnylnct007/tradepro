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

interface AppSettings {
  sentiment: SentimentSettings;
  updatedAtUtc: string;
}

const DEFAULTS: SentimentSettings = {
  meanSentimentThreshold: -0.30,
  minMaterialNegativeCount: 2,
  lookbackDays: 7,
};

export function Settings() {
  const [data, setData] = useState<AppSettings | null>(null);
  const [draft, setDraft] = useState<SentimentSettings>(DEFAULTS);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [savedAt, setSavedAt] = useState<string | null>(null);

  useEffect(() => {
    fetch(new URL("/api/settings", config.apiBaseUrl).toString())
      .then((r) => r.ok ? r.json() : Promise.reject(`${r.status}`))
      .then((d: AppSettings) => { setData(d); setDraft(d.sentiment); })
      .catch((e) => setError(`Couldn't load settings: ${e}`));
  }, []);

  const dirty =
    !data
    || data.sentiment.meanSentimentThreshold !== draft.meanSentimentThreshold
    || data.sentiment.minMaterialNegativeCount !== draft.minMaterialNegativeCount
    || data.sentiment.lookbackDays !== draft.lookbackDays;

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
          sentiment: draft,
          updatedAtUtc: new Date().toISOString(),
        }),
      });
      if (!resp.ok) {
        const body = await resp.text();
        throw new Error(`${resp.status}: ${body.slice(0, 200)}`);
      }
      const fresh: AppSettings = await resp.json();
      setData(fresh);
      setDraft(fresh.sentiment);
      setSavedAt(new Date().toLocaleTimeString());
    } catch (e) {
      setError(String(e));
    } finally {
      setSaving(false);
    }
  }

  function reset() {
    if (data) setDraft(data.sentiment);
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
            value={draft.meanSentimentThreshold}
            onChange={(e) => setDraft({ ...draft, meanSentimentThreshold: Number(e.target.value) })}
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
            value={draft.minMaterialNegativeCount}
            onChange={(e) => setDraft({ ...draft, minMaterialNegativeCount: Number(e.target.value) })}
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
            value={draft.lookbackDays}
            onChange={(e) => setDraft({ ...draft, lookbackDays: Number(e.target.value) })}
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
          BUY → WAIT when {draft.lookbackDays}-day rolling mean sentiment{" "}
          <code>≤ {draft.meanSentimentThreshold}</code>{" "}
          AND <code>≥ {draft.minMaterialNegativeCount}</code> material-negative
          headlines.
        </div>

        <div style={{ display: "flex", gap: 10, alignItems: "center" }}>
          <button
            className="primary"
            onClick={save}
            disabled={!dirty || saving}
          >
            {saving ? "Saving…" : "Save"}
          </button>
          <button onClick={reset} disabled={!dirty || saving}>
            Reset
          </button>
          {data && (
            <span style={{ marginLeft: "auto", color: "var(--text-muted)", fontSize: 11 }}>
              Last updated: {new Date(data.updatedAtUtc).toLocaleString()}
            </span>
          )}
        </div>
      </section>

      <section className="card" style={{ padding: "14px 18px", color: "var(--text-dim)", fontSize: 12 }}>
        <strong style={{ color: "var(--text)" }}>Coming soon:</strong>{" "}
        editable watchlists, custom regime windows, fee-model presets per
        broker, LLM model selection. See{" "}
        <a href="https://github.com/sunnylnct007/tradepro/blob/main/ROADMAP.md" target="_blank" rel="noreferrer" style={{ color: "var(--text)" }}>
          ROADMAP → Phase 7
        </a>.
      </section>
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

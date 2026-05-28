/**
 * SettingsKvSection — operator-tunable knobs from the
 * app_settings_kv table. The legacy strongly-typed AppSettings
 * editor (sentiment, intraday gate, etc.) still lives in
 * `pages/Settings.tsx`; this section sits next to it so all
 * settings appear on the same page.
 *
 * Each row renders the right input for its value_type. Save on
 * blur / Enter, with a small "saved Xs ago" pill so the trader
 * knows the change landed.
 */
import { useEffect, useState } from "react";
import { api } from "../../api/client";

type SettingRow = Awaited<ReturnType<typeof api.settingsKv>>["settings"][number];

export function SettingsKvSection() {
  const [rows, setRows] = useState<SettingRow[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  const load = async () => {
    setLoading(true);
    try {
      const r = await api.settingsKv();
      setRows(r.settings);
      setError(null);
    } catch (e) {
      setError(String(e));
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { void load(); }, []);

  if (loading && rows.length === 0) {
    return <Section title="Tunable knobs">
      <div style={{ color: "var(--text-muted)", fontSize: 12 }}>Loading…</div>
    </Section>;
  }
  if (error && rows.length === 0) {
    return <Section title="Tunable knobs">
      <div style={{ color: "var(--down)", fontSize: 12 }}>
        Couldn't load settings: {error}. The settings-kv endpoint may not
        be deployed yet.
      </div>
    </Section>;
  }

  // Group by category so the page reads as sections.
  const byCategory = new Map<string, SettingRow[]>();
  for (const r of rows) {
    const list = byCategory.get(r.category) ?? [];
    list.push(r);
    byCategory.set(r.category, list);
  }
  const categories = Array.from(byCategory.keys()).sort();

  return (
    <>
      {categories.map((c) => (
        <Section key={c} title={c}>
          {(byCategory.get(c) ?? []).map((row) => (
            <SettingRowEditor key={row.key} row={row} onSaved={() => void load()} />
          ))}
        </Section>
      ))}
    </>
  );
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section
      style={{
        border: "1px solid var(--border)",
        borderRadius: 8,
        padding: 14,
        marginBottom: 14,
        background: "var(--surface-1, rgba(255,255,255,0.02))",
      }}
    >
      <h3 style={{
        margin: "0 0 10px", fontSize: 13, fontWeight: 700,
        letterSpacing: "0.04em", textTransform: "uppercase",
        color: "var(--text-dim)",
      }}>
        {title}
      </h3>
      {children}
    </section>
  );
}

function SettingRowEditor({ row, onSaved }: { row: SettingRow; onSaved: () => void }) {
  const [draft, setDraft] = useState<string>(() => stringify(row.value, row.valueType));
  const [saving, setSaving] = useState(false);
  const [feedback, setFeedback] = useState<string | null>(null);
  const dirty = draft !== stringify(row.value, row.valueType);

  const save = async () => {
    setSaving(true);
    setFeedback(null);
    try {
      const parsed = parseDraft(draft, row.valueType);
      await api.updateSettingKv(row.key, parsed);
      setFeedback("✓ saved");
      onSaved();
    } catch (e) {
      setFeedback(String(e));
    } finally {
      setSaving(false);
    }
  };

  return (
    <div
      style={{
        display: "grid",
        gridTemplateColumns: "minmax(220px, 1fr) 200px auto",
        gap: 10, alignItems: "start",
        padding: "10px 0",
        borderTop: "1px solid var(--border)",
      }}
    >
      <div>
        <div style={{ fontSize: 12, fontWeight: 600 }}>
          {row.label ?? row.key}
        </div>
        {row.description && (
          <div style={{ fontSize: 11, color: "var(--text-dim)", marginTop: 4, lineHeight: 1.4 }}>
            {row.description}
          </div>
        )}
        <div style={{ fontSize: 10, color: "var(--text-muted)", fontFamily: "monospace", marginTop: 4 }}>
          {row.key}
          {row.minValue != null && ` · min ${row.minValue}`}
          {row.maxValue != null && ` · max ${row.maxValue}`}
        </div>
      </div>
      <Input row={row} draft={draft} setDraft={setDraft} />
      <div style={{ display: "flex", flexDirection: "column", alignItems: "flex-end", gap: 4 }}>
        <button
          disabled={!dirty || saving}
          onClick={save}
          style={{
            padding: "5px 12px", fontSize: 12, fontWeight: 600,
            border: "none", borderRadius: 4,
            background: !dirty || saving ? "var(--text-muted)" : "#1fc16b",
            color: "white", cursor: !dirty || saving ? "default" : "pointer",
          }}
        >
          {saving ? "Saving…" : dirty ? "Save" : "Saved"}
        </button>
        {feedback && (
          <span style={{ fontSize: 10, color: feedback.startsWith("✓") ? "#1fc16b" : "var(--down)" }}>
            {feedback}
          </span>
        )}
      </div>
    </div>
  );
}

function Input({
  row, draft, setDraft,
}: {
  row: SettingRow;
  draft: string;
  setDraft: (s: string) => void;
}) {
  const baseStyle: React.CSSProperties = {
    width: "100%", padding: "5px 8px", fontSize: 12,
    border: "1px solid var(--border)", borderRadius: 4,
    background: "transparent", color: "var(--text)",
    fontFamily: row.valueType === "json" ? "monospace" : undefined,
  };
  if (row.valueType === "number") {
    return (
      <input
        type="number"
        min={row.minValue ?? undefined}
        max={row.maxValue ?? undefined}
        value={draft}
        onChange={(e) => setDraft(e.target.value)}
        style={baseStyle}
      />
    );
  }
  if (row.valueType === "bool") {
    return (
      <select
        value={draft}
        onChange={(e) => setDraft(e.target.value)}
        style={baseStyle}
      >
        <option value="true">true</option>
        <option value="false">false</option>
      </select>
    );
  }
  if (row.valueType === "json") {
    return (
      <textarea
        value={draft}
        onChange={(e) => setDraft(e.target.value)}
        rows={3}
        style={baseStyle}
      />
    );
  }
  return (
    <input
      type="text"
      value={draft}
      onChange={(e) => setDraft(e.target.value)}
      style={baseStyle}
    />
  );
}

// Helpers to convert between the stored JSON value (number / string /
// bool / object) and the editor's text input. Strings are unquoted
// in the input so the trader doesn't have to type quotes.
function stringify(value: unknown, valueType: string): string {
  if (value == null) return "";
  if (valueType === "string" && typeof value === "string") return value;
  if (valueType === "number" && typeof value === "number") return String(value);
  if (valueType === "bool") return value ? "true" : "false";
  return JSON.stringify(value);
}

function parseDraft(text: string, valueType: string): unknown {
  if (valueType === "number") {
    const n = Number(text);
    if (!Number.isFinite(n)) throw new Error("not a number");
    return n;
  }
  if (valueType === "bool") return text === "true";
  if (valueType === "string") return text;
  // json / other → parse
  try { return JSON.parse(text); }
  catch { return text; } // tolerate a bare string in the JSON field
}

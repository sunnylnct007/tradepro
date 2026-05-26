/**
 * TriggerPanel — compact form on the cockpit to fire a paper session
 * without leaving /trader. Strategy + Universe + Symbols + Session
 * date + Lookback in one row of pills + small inputs.
 *
 * Universe pill row hydrates the Symbols textarea in one click —
 * "scan all of S&P 500" is a single tap. Trader can still hand-edit
 * after, or skip the universe row entirely for a small symbol set.
 *
 * Lives in its own file (extracted from TraderCockpit.tsx) for
 * readability — 200 lines of form state was making the cockpit
 * shell hard to read.
 */
import { useEffect, useState } from "react";
import { api } from "../../api/client";

type Strat =
  Awaited<ReturnType<typeof api.paperStrategies>>["strategies"][number];
type Universe =
  Awaited<ReturnType<typeof api.universes>>["universes"][number];

const triggerInput: React.CSSProperties = {
  padding: "5px 8px", fontSize: 12,
  border: "1px solid var(--border)", borderRadius: 4,
  background: "transparent", color: "var(--text)",
};

export function TriggerPanel({ onTriggered }: { onTriggered: () => void }) {
  const [strategies, setStrategies] = useState<Strat[]>([]);
  const [universes, setUniverses] = useState<Universe[]>([]);
  const [selected, setSelected] = useState<Strat | null>(null);
  // Symbols as CSV so the trader can paste / hand-curate after
  // picking a universe. Run() splits + cleans.
  const [symbolsText, setSymbolsText] = useState("");
  const [pickedUniverse, setPickedUniverse] = useState<string | null>(null);
  const [loadingUniverse, setLoadingUniverse] = useState(false);
  const todayIso = new Date().toISOString().slice(0, 10);
  const [date, setDate] = useState(todayIso);
  const [lookback, setLookback] = useState<number | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [feedback, setFeedback] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    api.paperStrategies()
      .then((r) => { if (!cancelled) setStrategies(r.strategies); })
      .catch((e) => { if (!cancelled) setFeedback(`Strategy catalog failed: ${e}`); });
    // Universe catalog — optional (older API images won't have the
    // endpoint). Silent on failure so the form still works.
    api.universes()
      .then((r) => { if (!cancelled) setUniverses(r.universes); })
      .catch(() => { /* universe pipeline not yet ingested */ });
    return () => { cancelled = true; };
  }, []);

  const pickStrategy = (s: Strat) => {
    setSelected(s);
    setLookback(s.default_lookback_days ?? 0);
    if (s.name === "ichimoku_fx_mr") setSymbolsText("");
    else if (!symbolsText) setSymbolsText("AAPL,MSFT,NVDA,TSLA");
  };

  // Pick a universe → fetch its symbols (server-side INCLUDE /
  // EXCLUDE overrides already applied → "effective" only) and
  // replace the symbols textbox.
  const pickUniverse = async (name: string) => {
    setLoadingUniverse(true);
    setPickedUniverse(name);
    try {
      const u = await api.universe(name);
      const tickers = u.symbols.filter((s) => s.effective).map((s) => s.ticker);
      setSymbolsText(tickers.join(","));
      setFeedback(`Loaded ${tickers.length} symbols from ${name}`);
    } catch (e) {
      setFeedback(`Universe load failed: ${e}`);
    } finally {
      setLoadingUniverse(false);
    }
  };

  const run = async () => {
    if (!selected) return;
    const isFx = selected.name === "ichimoku_fx_mr";
    const symbols = isFx ? [] :
      symbolsText.split(",").map((s) => s.trim().toUpperCase()).filter(Boolean);
    if (!isFx && symbols.length === 0) {
      setFeedback("Enter at least one symbol before triggering");
      return;
    }
    setSubmitting(true);
    setFeedback(null);
    try {
      await api.runIntraday({
        strategy: selected.name,
        symbols,
        session_date: date,
        lookback_days: lookback ?? 0,
        params: selected.default_params,
      });
      setFeedback(`✓ Queued ${selected.name} on ${symbols.length || "G10"} symbols for ${date}`);
      onTriggered();
    } catch (e) {
      setFeedback(`Failed: ${e}`);
    } finally {
      setSubmitting(false);
    }
  };

  if (strategies.length === 0) {
    return <div style={{ fontSize: 12, color: "var(--text-muted)" }}>Loading strategies…</div>;
  }
  return (
    <div>
      <StrategyPills strategies={strategies} selected={selected} onPick={pickStrategy} />
      {selected && selected.name !== "ichimoku_fx_mr" && universes.length > 0 && (
        <UniversePills
          universes={universes}
          picked={pickedUniverse}
          loading={loadingUniverse}
          onPick={(n) => void pickUniverse(n)}
        />
      )}
      {selected && (
        <SessionInputs
          isFx={selected.name === "ichimoku_fx_mr"}
          symbolsText={symbolsText}
          setSymbolsText={setSymbolsText}
          date={date}
          maxDate={todayIso}
          setDate={setDate}
          lookback={lookback ?? 0}
          setLookback={setLookback}
          submitting={submitting}
          onRun={run}
        />
      )}
      {feedback && (
        <div style={{
          marginTop: 8, fontSize: 11,
          color: feedback.startsWith("✓") ? "#1fc16b" : "var(--down)",
        }}>{feedback}</div>
      )}
      {selected && (
        <div style={{ marginTop: 8, fontSize: 10, color: "var(--text-muted)" }}>
          {selected.summary}
        </div>
      )}
    </div>
  );
}

function StrategyPills({
  strategies, selected, onPick,
}: {
  strategies: Strat[]; selected: Strat | null; onPick: (s: Strat) => void;
}) {
  return (
    <div style={{ display: "flex", flexWrap: "wrap", gap: 6, marginBottom: 10 }}>
      {strategies.map((s) => {
        const isSelected = selected?.name === s.name;
        const tone = s.source === "trader-quant" ? "#1fc16b"
          : s.source === "alpha-engine" ? "#4f8cff"
          : "var(--text-dim)";
        return (
          <button
            key={s.name}
            onClick={() => onPick(s)}
            style={{
              padding: "4px 11px", fontSize: 11, borderRadius: 999,
              border: `1px solid ${isSelected ? tone : "var(--border)"}`,
              background: isSelected ? `${tone}1a` : "transparent",
              color: isSelected ? tone : "var(--text-dim)",
              cursor: "pointer", fontFamily: "monospace",
            }}
            title={s.summary}
          >
            {s.name}
          </button>
        );
      })}
    </div>
  );
}

function UniversePills({
  universes, picked, loading, onPick,
}: {
  universes: Universe[];
  picked: string | null;
  loading: boolean;
  onPick: (n: string) => void;
}) {
  return (
    <div style={{ display: "flex", flexWrap: "wrap", gap: 6, marginBottom: 10, alignItems: "baseline" }}>
      <span style={{
        fontSize: 9, color: "var(--text-muted)",
        textTransform: "uppercase", letterSpacing: "0.06em",
      }}>
        Universe
      </span>
      {universes.map((u) => {
        const isPicked = picked === u.name;
        return (
          <button
            key={u.name}
            onClick={() => onPick(u.name)}
            disabled={loading}
            title={`${u.symbolCount} symbols · fetched ${new Date(u.fetchedAtUtc).toLocaleString()}${
              u.excludedOverrides ? ` · ${u.excludedOverrides} excluded by you` : ""
            }`}
            style={{
              padding: "3px 9px", fontSize: 10, borderRadius: 999,
              border: `1px solid ${isPicked ? "#a855f7" : "var(--border)"}`,
              background: isPicked ? "rgba(168,85,247,0.10)" : "transparent",
              color: isPicked ? "#a855f7" : "var(--text-dim)",
              cursor: loading ? "wait" : "pointer",
              fontFamily: "monospace", letterSpacing: "0.02em",
            }}
          >
            {u.name}
            <span style={{ marginLeft: 4, opacity: 0.7 }}>
              {u.symbolCount - u.excludedOverrides}
            </span>
          </button>
        );
      })}
    </div>
  );
}

function SessionInputs({
  isFx, symbolsText, setSymbolsText, date, maxDate, setDate,
  lookback, setLookback, submitting, onRun,
}: {
  isFx: boolean;
  symbolsText: string;
  setSymbolsText: (s: string) => void;
  date: string;
  maxDate: string;
  setDate: (s: string) => void;
  lookback: number;
  setLookback: (n: number) => void;
  submitting: boolean;
  onRun: () => void;
}) {
  const symCount = symbolsText.split(",").filter(Boolean).length;
  return (
    <div style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "end" }}>
      <FieldGroup label={`Symbols${symbolsText ? ` (${symCount})` : ""}`}>
        <textarea
          placeholder={isFx ? "G10 (auto)" : "AAPL,MSFT,NVDA — or pick a Universe pill above"}
          value={symbolsText}
          onChange={(e) => setSymbolsText(e.target.value)}
          disabled={isFx}
          rows={2}
          style={{ ...triggerInput, width: 280, fontFamily: "monospace", resize: "vertical" }}
        />
      </FieldGroup>
      <FieldGroup label="Session date">
        <input
          type="date"
          value={date}
          max={maxDate}
          onChange={(e) => setDate(e.target.value)}
          style={triggerInput}
        />
      </FieldGroup>
      <FieldGroup label="Lookback (days)">
        <input
          type="number"
          min={0}
          max={365}
          value={lookback}
          onChange={(e) => setLookback(Number(e.target.value))}
          style={{ ...triggerInput, width: 70 }}
        />
      </FieldGroup>
      <button
        onClick={onRun}
        disabled={submitting}
        style={{
          padding: "6px 14px", fontSize: 12, fontWeight: 600,
          background: submitting ? "var(--text-muted)" : "#1fc16b",
          color: "white", border: "none", borderRadius: 4,
          cursor: submitting ? "wait" : "pointer",
        }}
      >
        {submitting ? "Queueing…" : "Run"}
      </button>
    </div>
  );
}

function FieldGroup({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 2 }}>
      <span style={{
        fontSize: 9, color: "var(--text-muted)",
        letterSpacing: "0.04em", textTransform: "uppercase",
      }}>
        {label}
      </span>
      {children}
    </div>
  );
}

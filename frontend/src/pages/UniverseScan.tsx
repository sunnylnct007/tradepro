/**
 * /scan — single-purpose "run strategy across the whole universe +
 * see every symbol's signal at a glance" page.
 *
 * Why a dedicated page (rather than just the cockpit's Trigger panel
 * + SymbolScanGrid widget): the cockpit is busy. When the trader's
 * morning workflow is "scan the index, decide what to act on", they
 * want one screen with the strategy picker, the universe picker, a
 * "run" button, and the result grid — without the distraction of
 * cash / orders / health / positions / etc.
 *
 * Step 1 of the trader's universe workflow:
 *   1. Pick strategy (pills)
 *   2. Pick universe (pills, with effective-symbol count)
 *   3. Click "Scan now"
 *   4. Wait for completion (we poll the ops queue every 5s)
 *   5. Grid view: every symbol's fire/skip + signal detail
 *   6. Drill into Session Detail for the Ichimoku cloud chart per
 *      symbol if needed
 *   7. (Step 2 — picker for top-N to actually place orders — done
 *      from the cockpit "approve top N" button on OMS PENDING_APPROVAL)
 */
import { useCallback, useEffect, useMemo, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { api } from "../api/client";
import { SymbolScanGrid } from "../components/cockpit/SymbolScanGrid";
import type { DecisionEntry, LatestSession } from "../types/cockpit";

type Strat = Awaited<ReturnType<typeof api.paperStrategies>>["strategies"][number];
type Universe = Awaited<ReturnType<typeof api.universes>>["universes"][number];

/**
 * Pull a readable string out of an API fetch failure. Errors from the
 * `get` / `post` helpers in api/client.ts look like
 *   "500 Internal Server Error: {\"error\":\"...\",\"type\":\"...\",...}"
 * — we surface the JSON `error` + `type` if present, falling back to
 * the raw message. Saves the trader from reading a JSON dump on the
 * Scan screen.
 */
function humaniseFetchError(e: unknown): string {
  const raw = e instanceof Error ? e.message : String(e);
  const m = raw.match(/^(\d{3})\s+([^:]+):\s*(.*)$/s);
  if (!m) return raw;
  const [, code, statusText, body] = m;
  try {
    const j = JSON.parse(body);
    if (j && typeof j === "object") {
      const parts: string[] = [`HTTP ${code}`];
      if (j.type) parts.push(String(j.type));
      if (j.error) parts.push(String(j.error));
      return parts.join(" — ");
    }
  } catch {
    /* not JSON; fall through */
  }
  return `HTTP ${code} ${statusText}${body ? `: ${body.slice(0, 240)}` : ""}`;
}

export function UniverseScan() {
  const [params, setParams] = useSearchParams();
  const [allStrategies, setAllStrategies] = useState<Strat[]>([]);
  // Filter to trader-quant entries only — scaffolds (orb, bollinger,
  // ma_crossover etc.) aren't ready for universe scans and confused
  // the trader looking for "Ichi". Until those mature their statuses
  // they're hidden here; the catalog at /strategies still lists them.
  const strategies = allStrategies.filter((s) => s.source === "trader-quant");
  const [universes, setUniverses] = useState<Universe[]>([]);
  const [selectedStrategy, setSelectedStrategy] = useState<string>(
    params.get("strategy") ?? "ichimoku_equity",
  );
  const [selectedUniverse, setSelectedUniverse] = useState<string>(
    params.get("universe") ?? "sp500",
  );
  const [submitting, setSubmitting] = useState(false);
  const [feedback, setFeedback] = useState<string | null>(null);
  const [lastRequestId, setLastRequestId] = useState<string | null>(
    params.get("session"),
  );
  const [latestSession, setLatestSession] = useState<LatestSession | null>(null);

  useEffect(() => {
    api.paperStrategies()
      .then((r) => setAllStrategies(r.strategies))
      .catch((e) => setFeedback(`Strategy catalog failed: ${e}`));
    api.universes()
      .then((r) => setUniverses(r.universes))
      .catch(() => { /* universes are optional pre-ingest */ });
  }, []);

  // Poll the ops queue for the session we just triggered until it
  // completes — then extract the decisions into a LatestSession the
  // grid can render. Stops polling on terminal state.
  const reloadSession = useCallback(async () => {
    if (!lastRequestId) return;
    try {
      const s = await api.getOpsSession(lastRequestId);
      const rs = (s.result_summary ?? {}) as Record<string, unknown>;
      const strategies = (rs.strategies as Array<Record<string, unknown>>) ?? [];
      const decisions: DecisionEntry[] = [];
      let barsSeen = 0;
      const charts: Record<string, unknown> = {};
      const topCharts = rs.charts as Record<string, unknown> | undefined;
      if (topCharts) Object.assign(charts, topCharts);
      for (const st of strategies) {
        const bs = st.bars_seen as Array<unknown> | undefined;
        if (Array.isArray(bs)) barsSeen += bs.length;
        const sc = st.charts as Record<string, unknown> | undefined;
        if (sc) Object.assign(charts, sc);
        const ds = st.decisions as Array<Record<string, unknown>>;
        if (!Array.isArray(ds)) continue;
        for (const d of ds) {
          decisions.push({
            barTs: (d.bar_ts as string) || null,
            symbol: (d.symbol as string) || "",
            action: (d.action as string) || "",
            reason: (d.reason as string) || "",
            detail: (d.detail as Record<string, unknown>) || {},
          });
        }
      }
      decisions.sort((a, b) => (b.barTs ?? "").localeCompare(a.barTs ?? ""));
      setLatestSession({
        strategy: selectedStrategy,
        requestId: lastRequestId,
        completedAtUtc: s.completed_at_utc,
        decisions,
        barsSeen,
        charts,
      });
      const completed = (s.state ?? "").toLowerCase() === "completed";
      if (completed) {
        setFeedback(`Session completed · ${decisions.length} decisions`);
      } else {
        setFeedback(`Session ${s.state.toLowerCase()} · waiting for completion`);
      }
    } catch (e) {
      setFeedback(`Session load failed: ${e}`);
    }
  }, [lastRequestId, selectedStrategy]);

  useEffect(() => { void reloadSession(); }, [reloadSession]);
  useEffect(() => {
    if (!lastRequestId) return;
    const completed = (latestSession?.completedAtUtc ?? null) !== null;
    if (completed) return;
    const id = setInterval(() => void reloadSession(), 5000);
    return () => clearInterval(id);
  }, [lastRequestId, latestSession, reloadSession]);

  const run = async () => {
    setSubmitting(true);
    setFeedback("Loading universe symbols…");
    try {
      const u = await api.universe(selectedUniverse);
      const symbols = u.symbols.filter((s) => s.effective).map((s) => s.ticker);
      if (symbols.length === 0) {
        setFeedback(`Universe ${selectedUniverse} is empty — refresh from the Mac.`);
        return;
      }
      const selected = strategies.find((s) => s.name === selectedStrategy);
      const lookback = selected?.default_lookback_days ?? 0;
      setFeedback(`Triggering scan on ${symbols.length} symbols…`);
      const today = new Date().toISOString().slice(0, 10);
      const res = await api.runIntraday({
        strategy: selectedStrategy,
        symbols,
        session_date: today,
        lookback_days: lookback,
        // Scan mode: keep orders out of the broker. The cockpit's
        // "approve top N" button is the next step in the trader
        // workflow once the scan completes.
        placement_mode: "manual",
        params: selected?.default_params ?? {},
      });
      setLastRequestId(res.requestId);
      setLatestSession(null);
      setParams({ strategy: selectedStrategy, universe: selectedUniverse, session: res.requestId });
      setFeedback(`Queued ${symbols.length} symbols — request ${res.requestId.slice(0, 8)}…`);
    } catch (e) {
      setFeedback(`Failed: ${humaniseFetchError(e)}`);
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div style={{ padding: 24, maxWidth: 1200 }}>
      <h1 style={{ margin: 0, fontSize: 22 }}>Universe scan</h1>
      <p style={{ color: "var(--text-dim)", fontSize: 13, marginTop: 6, marginBottom: 16 }}>
        Run the selected strategy across every symbol in the chosen
        universe. Result grid below shows each symbol's fire / skip
        signal at a glance. From there, the cockpit's "approve top N"
        button (on the OMS pending-intents panel) is the next step to
        actually place orders.
      </p>

      <ScanForm
        strategies={strategies}
        universes={universes}
        strategy={selectedStrategy}
        universe={selectedUniverse}
        onStrategy={setSelectedStrategy}
        onUniverse={setSelectedUniverse}
        onRun={() => void run()}
        submitting={submitting}
        feedback={feedback}
      />

      {latestSession ? (
        <div style={{ marginTop: 14 }}>
          <SessionSummary session={latestSession} requestId={lastRequestId ?? ""} />
          <SymbolScanGrid latestSessions={[latestSession]} />
        </div>
      ) : lastRequestId ? (
        <div style={{ marginTop: 14, fontSize: 12, color: "var(--text-muted)" }}>
          Polling session {lastRequestId.slice(0, 8)}… results stream in when the
          Mac daemon completes the run.
        </div>
      ) : (
        <div style={{ marginTop: 14, fontSize: 12, color: "var(--text-muted)" }}>
          Pick a strategy + universe and click "Scan now". A previous scan
          (if any in the URL) re-loads here.
        </div>
      )}
    </div>
  );
}

function ScanForm({
  strategies, universes, strategy, universe,
  onStrategy, onUniverse, onRun, submitting, feedback,
}: {
  strategies: Strat[];
  universes: Universe[];
  strategy: string;
  universe: string;
  onStrategy: (s: string) => void;
  onUniverse: (u: string) => void;
  onRun: () => void;
  submitting: boolean;
  feedback: string | null;
}) {
  const selectedStrat = strategies.find((s) => s.name === strategy);
  return (
    <div style={{
      padding: "12px 14px",
      border: "1px solid var(--border)",
      borderRadius: 8,
      background: "var(--surface-1, rgba(255,255,255,0.02))",
      marginBottom: 14,
    }}>
      <PillRow label="Strategy">
        {strategies.map((s) => (
          <Pill key={s.name} label={s.name} active={s.name === strategy} onClick={() => onStrategy(s.name)} color="#1fc16b" />
        ))}
      </PillRow>
      <PillRow label="Universe">
        {universes.map((u) => (
          <Pill
            key={u.name}
            label={`${u.name} (${u.symbolCount - u.excludedOverrides})`}
            active={u.name === universe}
            onClick={() => onUniverse(u.name)}
            color="#a855f7"
          />
        ))}
        {universes.length === 0 && (
          <span style={{ fontSize: 11, color: "var(--text-muted)" }}>
            No universes ingested. Run <code>tradepro-refresh-universes --push</code>.
          </span>
        )}
      </PillRow>
      {selectedStrat?.caveats && selectedStrat.caveats.length > 0 && (
        <div style={{
          marginTop: 8, padding: "6px 10px",
          border: "1px solid rgba(245,158,11,0.35)",
          background: "rgba(245,158,11,0.06)",
          borderRadius: 6, fontSize: 11,
        }}>
          <strong style={{ color: "#f59e0b" }}>⚠ {strategy}:</strong>{" "}
          {selectedStrat.caveats[0]}
        </div>
      )}
      <div style={{ display: "flex", alignItems: "center", gap: 12, marginTop: 12 }}>
        <button
          onClick={onRun}
          disabled={submitting || !strategy || !universe}
          style={{
            padding: "6px 18px", fontSize: 13, fontWeight: 600,
            background: submitting ? "var(--text-muted)" : "#1fc16b",
            color: "white", border: "none", borderRadius: 4,
            cursor: submitting ? "wait" : "pointer",
          }}
        >
          {submitting ? "Queueing…" : "Scan now"}
        </button>
        {feedback && (
          <span style={{
            fontSize: 11,
            color: feedback.startsWith("Failed") ? "var(--down)" : "var(--text-dim)",
          }}>
            {feedback}
          </span>
        )}
      </div>
    </div>
  );
}

function PillRow({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div style={{ display: "flex", flexWrap: "wrap", gap: 6, alignItems: "baseline", marginBottom: 8 }}>
      <span style={{
        fontSize: 9, color: "var(--text-muted)",
        textTransform: "uppercase", letterSpacing: "0.06em",
        minWidth: 60,
      }}>
        {label}
      </span>
      {children}
    </div>
  );
}

function Pill({
  label, active, onClick, color,
}: {
  label: string; active: boolean; onClick: () => void; color: string;
}) {
  return (
    <button
      onClick={onClick}
      style={{
        padding: "3px 10px", fontSize: 11, borderRadius: 999,
        border: `1px solid ${active ? color : "var(--border)"}`,
        background: active ? `${color}1a` : "transparent",
        color: active ? color : "var(--text-dim)",
        cursor: "pointer", fontFamily: "monospace", letterSpacing: "0.02em",
      }}
    >
      {label}
    </button>
  );
}

function SessionSummary({
  session, requestId,
}: {
  session: LatestSession;
  requestId: string;
}) {
  const fires = useMemo(() => session.decisions.filter((d) => d.action.startsWith("fire-")).length, [session]);
  const skips = useMemo(() => session.decisions.filter((d) => d.action.startsWith("skip-")).length, [session]);
  return (
    <div style={{
      display: "flex", gap: 16, alignItems: "baseline",
      marginBottom: 10, fontSize: 12,
    }}>
      <span><strong>{fires}</strong> fire</span>
      <span><strong>{skips}</strong> skip</span>
      <span>· bars seen: <strong>{session.barsSeen}</strong></span>
      <span>· completed: <strong>{session.completedAtUtc ? new Date(session.completedAtUtc).toLocaleTimeString() : "in flight"}</strong></span>
      <Link
        to={`/paper-live/session/${encodeURIComponent(requestId)}`}
        style={{
          marginLeft: "auto", fontSize: 11, color: "var(--text-muted)",
          textDecoration: "none", borderBottom: "1px dotted var(--text-muted)",
        }}
      >
        Session detail (charts) →
      </Link>
    </div>
  );
}

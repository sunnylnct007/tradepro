/**
 * Strategies page — the trader-owned signal-generator catalogue.
 *
 * A STRATEGY generates trading signals (it may lean on indicators
 * internally — those are shown as "uses", not as catalog entries; raw
 * indicators are an analytical concern surfaced on /decide). Each
 * strategy is owned by a TRADER (desk persona, one trader → many
 * strategies) and runs in one of two EXECUTION MODES:
 *
 *   LIVE        — mapped to a real broker; approved orders route there.
 *   SIGNAL-ONLY — mapped to PAPER / unmapped; signals are generated and
 *                 recorded for evaluation but never placed on a broker.
 *                 Toggle this to shadow-evaluate a strategy (or to run
 *                 the options desk, which is signal-only by design).
 *
 * Layout: strategies grouped under their owning desk. Each row shows
 * the execution-mode pill + a one-click toggle (wired to the existing
 * strategy_broker_map endpoint — LIVE writes the desk's broker, SIGNAL-
 * ONLY writes PAPER), promotion lifecycle, and an ad-hoc runner. The
 * sessions queue below tracks runs as the Mac worker claims them.
 */
import React, { useCallback, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api/client";
import {
  DESK_ORDER,
  DESKS,
  deskFor,
  executionMode,
  metaFor,
  STRATEGY_ALIASES,
  type DeskId,
} from "../util/strategyMeta";

type Strategy = {
  name: string;
  class: string;
  summary: string;
  source?: string;
  status?: string;
  default_lookback_days?: number;
  default_params: Record<string, unknown>;
};

type StatusOverride = {
  StrategyId: string;
  Status: string;
  UpdatedAtUtc: string;
  UpdatedBy: string;
};

const STATUS_FLOW = ["evaluating", "backtest-ok", "scheduled", "live-eligible"] as const;
type LifecycleStatus = (typeof STATUS_FLOW)[number];

function nextStatus(current: string): LifecycleStatus | null {
  const idx = (STATUS_FLOW as readonly string[]).indexOf(current);
  if (idx === -1 || idx === STATUS_FLOW.length - 1) return null;
  return STATUS_FLOW[idx + 1];
}

function statusBadge(status: string): { bg: string; fg: string } {
  switch (status) {
    case "live-eligible":
      return { bg: "rgba(31,193,107,0.18)", fg: "#1fc16b" };
    case "scheduled":
      return { bg: "rgba(79,140,255,0.15)", fg: "#4f8cff" };
    case "backtest-ok":
      return { bg: "rgba(217,119,6,0.15)", fg: "#d97706" };
    default:
      return { bg: "rgba(107,114,128,0.15)", fg: "#9ca3af" };
  }
}

type Session = {
  request_id: string;
  kind: string;
  state: string;
  params: Record<string, unknown>;
  claimed_by: string | null;
  requested_at_utc: string;
  claimed_at_utc: string | null;
  completed_at_utc: string | null;
  result_summary: Record<string, unknown> | null;
  error: string | null;
};

type BrokerMap = {
  validBrokers: string[];
  defaultBroker: string | null;
  byStrategy: Record<string, string>; // explicit mappings only
};

export function Strategies() {
  const [strategies, setStrategies] = useState<Strategy[]>([]);
  const [sessions, setSessions] = useState<Session[]>([]);
  const [brokerMap, setBrokerMap] = useState<BrokerMap>({ validBrokers: [], defaultBroker: null, byStrategy: {} });
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [busyName, setBusyName] = useState<string | null>(null);
  const [togglingName, setTogglingName] = useState<string | null>(null);
  const [symbolFor, setSymbolFor] = useState<Record<string, string>>({});
  const todayIso = new Date().toISOString().slice(0, 10);
  const [dateFor, setDateFor] = useState<Record<string, string>>({});
  const [lookbackFor, setLookbackFor] = useState<Record<string, number>>({});
  const [statusOverrides, setStatusOverrides] = useState<Record<string, StatusOverride>>({});
  const [expandedName, setExpandedName] = useState<string | null>(null);
  const [promotingName, setPromotingName] = useState<string | null>(null);

  const effectiveStatus = (s: Strategy): string =>
    statusOverrides[s.name]?.Status || s.status || "evaluating";

  /** Broker the backend would actually use: explicit mapping wins,
   * else the global default. Drives the LIVE / SIGNAL-ONLY pill. */
  // LIVE means an EXPLICIT broker mapping — NOT inheriting the global
  // default. Only ichimoku_equity/ichimoku_fx_mr/intraday_flat are mapped,
  // so everything else reads SIGNAL-ONLY (which is the truth). Previously
  // unmapped strategies inherited defaultBroker and falsely showed
  // "LIVE → T212" (e.g. ma_crossover), which is exactly the confusion to
  // kill: the trader only plugged ichimoku_equity into 212.
  const effectiveBroker = (name: string): string | null =>
    brokerMap.byStrategy[name] ?? null;
  const isExplicit = (name: string): boolean => name in brokerMap.byStrategy;

  const loadStatusOverrides = useCallback(async () => {
    try {
      const { overrides } = await api.strategyStatusOverrides();
      const map: Record<string, StatusOverride> = {};
      for (const o of overrides) map[o.StrategyId] = o;
      setStatusOverrides(map);
    } catch (e) {
      console.warn("strategyStatusOverrides failed:", e);
    }
  }, []);

  const loadBrokerMap = useCallback(async () => {
    try {
      const res = await api.strategyBrokerMap();
      const byStrategy: Record<string, string> = {};
      for (const m of res.mappings) byStrategy[m.strategy_id] = m.broker;
      setBrokerMap({ validBrokers: res.validBrokers, defaultBroker: res.defaultBroker, byStrategy });
    } catch (e) {
      console.warn("strategyBrokerMap failed:", e);
    }
  }, []);

  const promote = async (s: Strategy) => {
    const target = nextStatus(effectiveStatus(s));
    if (!target) return;
    setPromotingName(s.name);
    setError(null);
    try {
      await api.setStrategyStatus(s.name, target);
      await loadStatusOverrides();
    } catch (e) {
      setError(String(e));
    } finally {
      setPromotingName(null);
    }
  };

  const resetStatus = async (s: Strategy) => {
    setPromotingName(s.name);
    setError(null);
    try {
      await api.clearStrategyStatus(s.name);
      await loadStatusOverrides();
    } catch (e) {
      setError(String(e));
    } finally {
      setPromotingName(null);
    }
  };

  // Flip a strategy between LIVE and SIGNAL-ONLY by rewriting its broker
  // mapping. SIGNAL-ONLY ⇒ explicit "PAPER" (overrides any real default
  // so it can't quietly route live). LIVE ⇒ the desk's configured broker.
  const toggleExecution = async (name: string) => {
    const meta = metaFor(name);
    const live = executionMode(effectiveBroker(name)) === "live";
    if (!live && !meta?.liveBroker) {
      setError(`${name} has no broker plugged — map one on Settings before going live.`);
      return;
    }
    setTogglingName(name);
    setError(null);
    try {
      const target = live ? "PAPER" : (meta!.liveBroker as string);
      await api.updateStrategyBrokerMap(name, {
        broker: target,
        note: live ? "signal-only (evaluation) — set from catalog" : "live — set from catalog",
      });
      await loadBrokerMap();
    } catch (e) {
      setError(String(e));
    } finally {
      setTogglingName(null);
    }
  };

  const loadSessions = useCallback(async () => {
    try {
      const { sessions: rows } = await api.opsSessions(undefined, 30);
      setSessions(rows);
    } catch (e) {
      setError(String(e));
    }
  }, []);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    (async () => {
      try {
        const [s, q] = await Promise.all([
          api.paperStrategies(),
          api.opsSessions(undefined, 30),
          loadStatusOverrides(),
          loadBrokerMap(),
        ]);
        if (cancelled) return;
        setStrategies(s.strategies);
        setSessions(q.sessions);
        setLoading(false);
      } catch (e) {
        if (!cancelled) {
          setError(String(e));
          setLoading(false);
        }
      }
    })();
    return () => { cancelled = true; };
  }, [loadStatusOverrides, loadBrokerMap]);

  useEffect(() => {
    const hasActive = sessions.some(s => s.state === "Pending" || s.state === "Claimed");
    if (!hasActive) return;
    const t = setInterval(() => { void loadSessions(); }, 5000);
    return () => clearInterval(t);
  }, [sessions, loadSessions]);

  const runStrategy = async (strategy: Strategy) => {
    const symbol = (symbolFor[strategy.name] || "").trim().toUpperCase();
    if (!symbol) {
      setError(`enter a symbol for ${strategy.name} before triggering`);
      return;
    }
    const session_date = dateFor[strategy.name] || todayIso;
    const lookback_days = lookbackFor[strategy.name] ?? strategy.default_lookback_days ?? 0;
    setBusyName(strategy.name);
    setError(null);
    try {
      await api.runIntraday({
        strategy: strategy.name,
        symbols: [symbol],
        session_date,
        lookback_days,
        params: strategy.default_params,
      });
      await loadSessions();
    } catch (e) {
      setError(String(e));
    } finally {
      setBusyName(null);
    }
  };

  const cancel = async (requestId: string) => {
    try {
      await api.cancelOpsSession(requestId);
      await loadSessions();
    } catch (e) {
      setError(String(e));
    }
  };

  if (loading) {
    return <div style={page}>Loading strategies and queue…</div>;
  }

  // Group strategies under their owning desk; drop registry aliases so a
  // strategy never appears twice. Unknown strategies fall to "trend".
  const visible = strategies.filter(s => !STRATEGY_ALIASES.has(s.name));
  const byDesk = new Map<DeskId, Strategy[]>();
  for (const s of visible) {
    const d = (metaFor(s.name)?.desk ?? "trend") as DeskId;
    if (!byDesk.has(d)) byDesk.set(d, []);
    byDesk.get(d)!.push(s);
  }
  const liveCount = visible.filter(s => executionMode(effectiveBroker(s.name)) === "live").length;

  return (
    <div style={page}>
      <header style={pageHeader}>
        <h1 style={{ fontSize: 22, margin: 0 }}>Strategies</h1>
        <p style={subhead}>
          Signal generators, grouped by the desk that owns them. <strong>{liveCount}</strong> live
          (routing to a broker) · <strong>{visible.length - liveCount}</strong> signal-only
          (recorded for evaluation, never placed). Strategies may use indicators internally —
          raw indicators live on <Link to="/compare" style={{ color: "var(--accent, #4f8cff)" }}>Decide</Link>.
        </p>
      </header>

      {error && <div style={errorBox}>{error}</div>}

      {DESK_ORDER.filter(d => byDesk.has(d)).map(deskId => {
        const desk = DESKS[deskId];
        const rows = byDesk.get(deskId)!;
        const deskLive = rows.filter(s => executionMode(effectiveBroker(s.name)) === "live").length;
        return (
          <section key={deskId} style={cardStyle}>
            <div style={deskHeader}>
              <div>
                <h2 style={deskTitle}>{desk.trader}</h2>
                <div style={smallMuted}>{desk.blurb}</div>
              </div>
              <div style={{ textAlign: "right", fontSize: 11, color: "var(--text-muted)" }}>
                <div>{rows.length} {rows.length === 1 ? "strategy" : "strategies"}</div>
                <div>
                  <span style={{ color: deskLive ? "#1fc16b" : "var(--text-muted)" }}>{deskLive} live</span>
                  {" · "}
                  <span>{rows.length - deskLive} signal-only</span>
                </div>
              </div>
            </div>

            <div style={{ overflowX: "auto" }}>
            <table style={tableStyle}>
              <thead>
                <tr>
                  <Th>Strategy</Th>
                  <Th>Execution</Th>
                  <Th>Lifecycle</Th>
                  <Th>Symbol</Th>
                  <Th>Date</Th>
                  <Th>Lookback</Th>
                  <Th>Actions</Th>
                </tr>
              </thead>
              <tbody>
                {rows.map(s => (
                  <StrategyRow
                    key={s.name}
                    s={s}
                    meta={metaFor(s.name)}
                    mode={executionMode(effectiveBroker(s.name))}
                    broker={effectiveBroker(s.name)}
                    brokerExplicit={isExplicit(s.name)}
                    status={effectiveStatus(s)}
                    isOverridden={!!statusOverrides[s.name]}
                    override={statusOverrides[s.name]}
                    expanded={expandedName === s.name}
                    busyRun={busyName === s.name}
                    busyPromote={promotingName === s.name}
                    busyToggle={togglingName === s.name}
                    symbol={symbolFor[s.name] || ""}
                    date={dateFor[s.name] || todayIso}
                    todayIso={todayIso}
                    lookback={lookbackFor[s.name] ?? s.default_lookback_days ?? 0}
                    onExpand={() => setExpandedName(expandedName === s.name ? null : s.name)}
                    onSymbol={(v) => setSymbolFor({ ...symbolFor, [s.name]: v })}
                    onDate={(v) => setDateFor({ ...dateFor, [s.name]: v })}
                    onLookback={(v) => setLookbackFor({ ...lookbackFor, [s.name]: v })}
                    onRun={() => runStrategy(s)}
                    onPromote={() => promote(s)}
                    onReset={() => resetStatus(s)}
                    onToggleExec={() => toggleExecution(s.name)}
                  />
                ))}
              </tbody>
            </table>
            </div>
          </section>
        );
      })}

      <section style={cardStyle}>
        <h2 style={sectionHead}>Sessions ({sessions.length})</h2>
        {sessions.length === 0 && (
          <div style={smallMuted}>No sessions yet. Trigger one above.</div>
        )}
        {sessions.length > 0 && (
          <table style={tableStyle}>
            <thead>
              <tr>
                <Th>Status</Th>
                <Th>Strategy / Symbol</Th>
                <Th>Enqueued</Th>
                <Th>Claimed by</Th>
                <Th>Result</Th>
                <Th>{" "}</Th>
              </tr>
            </thead>
            <tbody>
              {sessions.map(sess => {
                const params = sess.params || {};
                const strategy = (params as Record<string, string>).strategy || "—";
                // Clamp the symbol list — some sessions carry the full S&P
                // (400+ tickers) which otherwise blows the row width out and
                // breaks the page. Show count + first few, full list on hover.
                const symArr = (params as Record<string, string[]>).symbols || [];
                const symbols = symArr.length === 0
                  ? "—"
                  : symArr.length > 5
                    ? `${symArr.slice(0, 5).join(", ")} +${symArr.length - 5} more`
                    : symArr.join(", ");
                return (
                  <tr key={sess.request_id}>
                    <Td>
                      <span style={statusStyle(sess.state)}>{sess.state}</span>
                    </Td>
                    <Td>
                      <div style={{ fontWeight: 600 }}>{strategy}</div>
                      <div
                        style={{ ...smallMuted, maxWidth: 360, whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}
                        title={symArr.join(", ")}
                      >
                        {symbols}
                      </div>
                    </Td>
                    <Td>
                      <div style={smallMuted}>
                        {new Date(sess.requested_at_utc).toLocaleTimeString()}
                      </div>
                    </Td>
                    <Td>{sess.claimed_by || "—"}</Td>
                    <Td>
                      {sess.result_summary
                        ? <code style={codeStyle}>
                            {JSON.stringify(sess.result_summary).slice(0, 120)}
                          </code>
                        : "—"}
                    </Td>
                    <Td>
                      {sess.state === "Pending" && (
                        <button onClick={() => cancel(sess.request_id)} style={cancelButtonStyle}>
                          Cancel
                        </button>
                      )}
                    </Td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        )}
      </section>
    </div>
  );
}

function StrategyRow({
  s, meta, mode, broker, brokerExplicit, status, isOverridden, override,
  expanded, busyRun, busyPromote, busyToggle,
  symbol, date, todayIso, lookback,
  onExpand, onSymbol, onDate, onLookback, onRun, onPromote, onReset, onToggleExec,
}: {
  s: Strategy;
  meta: ReturnType<typeof metaFor>;
  mode: "live" | "signal-only";
  broker: string | null;
  brokerExplicit: boolean;
  status: string;
  isOverridden: boolean;
  override?: StatusOverride;
  expanded: boolean;
  busyRun: boolean;
  busyPromote: boolean;
  busyToggle: boolean;
  symbol: string;
  date: string;
  todayIso: string;
  lookback: number;
  onExpand: () => void;
  onSymbol: (v: string) => void;
  onDate: (v: string) => void;
  onLookback: (v: number) => void;
  onRun: () => void;
  onPromote: () => void;
  onReset: () => void;
  onToggleExec: () => void;
}) {
  const statB = statusBadge(status);
  const next = nextStatus(status);
  const live = mode === "live";
  const canGoLive = !!meta?.liveBroker;
  const indicators = meta?.indicators ?? [];

  return (
    <React.Fragment>
      <tr>
        <Td>
          <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
            <button onClick={onExpand} style={chevronStyle} title={expanded ? "Hide params" : "Show default params"}>
              {expanded ? "▾" : "▸"}
            </button>
            <div>
              <div style={{ display: "flex", alignItems: "center", gap: 6, flexWrap: "wrap" }}>
                <span style={{ fontWeight: 600 }}>{s.name}</span>
                {meta && (
                  <span style={{ fontSize: 10, color: "var(--text-muted)" }}>{meta.assetClass}</span>
                )}
              </div>
              <div style={smallMuted}>{s.summary}</div>
              {indicators.length > 0 && (
                <div style={{ fontSize: 10, color: "var(--text-dim)", marginTop: 2 }}>
                  uses {indicators.join(" · ")}
                </div>
              )}
            </div>
          </div>
        </Td>

        <Td>
          <div style={{ display: "flex", flexDirection: "column", gap: 4, alignItems: "flex-start" }}>
            <span
              style={live ? execPillLive : execPillSignal}
              title={live
                ? `Approved orders route to ${broker}. Toggle to stop placing and only record signals.`
                : "Signals are generated + recorded for evaluation but never placed on a broker."}
            >
              {live ? `LIVE → ${broker}` : "SIGNAL-ONLY"}
            </span>
            {live && !brokerExplicit && (
              <span style={{ fontSize: 9, color: "#d97706" }} title="No explicit mapping — inheriting the global default broker.">
                via default ⚠
              </span>
            )}
            <button
              onClick={onToggleExec}
              disabled={busyToggle || (!live && !canGoLive)}
              style={toggleBtn(busyToggle, !live && !canGoLive)}
              title={!live && !canGoLive ? "No broker plugged — map one on Settings first" : live ? "Stop placing on broker — keep recording signals" : `Start routing to ${meta?.liveBroker}`}
            >
              {busyToggle ? "…" : live ? "→ signal-only" : canGoLive ? "→ go live" : "no broker"}
            </button>
          </div>
        </Td>

        <Td>
          <div style={{ display: "flex", alignItems: "center", gap: 6, flexWrap: "wrap" }}>
            <span
              style={{ fontSize: 11, padding: "2px 8px", borderRadius: 999, background: statB.bg, color: statB.fg, letterSpacing: "0.03em" }}
              title={isOverridden ? "operator override" : "code default"}
            >
              {status}{isOverridden ? " *" : ""}
            </span>
            {next && (
              <button onClick={onPromote} disabled={busyPromote} style={promoteButtonStyle(busyPromote)} title={`Promote to ${next}`}>
                {busyPromote ? "…" : `→ ${next}`}
              </button>
            )}
            {isOverridden && (
              <button onClick={onReset} disabled={busyPromote} style={resetButtonStyle} title="Drop the override; use the code default">
                reset
              </button>
            )}
          </div>
        </Td>

        <Td>
          <input type="text" placeholder="AAPL" value={symbol} onChange={e => onSymbol(e.target.value)} style={inputStyle} />
        </Td>
        <Td>
          <input
            type="date" value={date} max={todayIso}
            onChange={e => onDate(e.target.value)} style={inputStyle}
            title="Use a past trading day on weekends/holidays to get real bars"
          />
        </Td>
        <Td>
          <input
            type="number" min={0} max={365} value={lookback}
            onChange={e => onLookback(Math.max(0, Number(e.target.value) || 0))}
            style={{ ...inputStyle, width: 70 }}
            title="Extend bar fetch backwards from the session date. Default comes from strategy.default_lookback_days."
          />
        </Td>
        <Td>
          <button onClick={onRun} disabled={busyRun} style={runButtonStyle(busyRun)}>
            {busyRun ? "Queueing…" : "Run"}
          </button>
        </Td>
      </tr>

      {expanded && (
        <tr style={{ background: "rgba(255,255,255,0.02)" }}>
          <td colSpan={7} style={{ padding: "10px 14px" }}>
            <div style={{ fontSize: 11, color: "var(--text-dim)", marginBottom: 6 }}>
              {s.class} · default_lookback_days={s.default_lookback_days ?? 0}
              {meta && <> · owner: <strong style={{ color: "var(--text-muted)" }}>{deskFor(s.name).trader}</strong></>}
              {isOverridden && override && (
                <> · override: <span style={{ color: "var(--text-muted)" }}>{override.UpdatedBy} at {new Date(override.UpdatedAtUtc).toLocaleString()}</span></>
              )}
            </div>
            <pre
              style={{
                margin: 0, padding: 10,
                background: "var(--bg-hover, rgba(255,255,255,0.03))",
                border: "1px solid var(--border)", borderRadius: 6,
                fontSize: 11, color: "var(--text-dim)", overflowX: "auto",
              }}
            >
              {JSON.stringify(s.default_params, null, 2)}
            </pre>
          </td>
        </tr>
      )}
    </React.Fragment>
  );
}

const Th = ({ children }: { children?: React.ReactNode }) =>
  <th style={{
    textAlign: "left", padding: "8px 10px", fontSize: 11,
    textTransform: "uppercase", color: "var(--text-muted)",
    letterSpacing: 0.5, fontWeight: 600, borderBottom: "1px solid var(--border)",
  }}>{children}</th>;

const Td = ({ children }: { children: React.ReactNode }) =>
  <td style={{ padding: "10px", fontSize: 13, borderBottom: "1px solid var(--border)", verticalAlign: "top" }}>{children}</td>;

function statusStyle(status: string): React.CSSProperties {
  const colour =
    status === "Completed" ? "#1f9e6e" :
    status === "Pending" ? "#a07e1c" :
    status === "Claimed" ? "#3b82a4" :
    status === "Failed" ? "#a83a3a" :
    status === "Cancelled" ? "#888" : "#666";
  return { background: colour, color: "#fff", padding: "3px 9px", borderRadius: 4, fontSize: 11, fontWeight: 600, letterSpacing: 0.4 };
}

const page: React.CSSProperties = {
  maxWidth: 1100, margin: "32px auto", padding: "0 20px",
  display: "flex", flexDirection: "column", gap: 18,
};
const pageHeader: React.CSSProperties = { marginBottom: 4 };
const subhead: React.CSSProperties = { margin: "4px 0 0", color: "var(--text-muted)", fontSize: 13, lineHeight: 1.6 };
const cardStyle: React.CSSProperties = {
  // --surface-* is NOT defined in this app's theme; the old #fff fallback
  // rendered white cards on the dark theme and hid the light text. Use the
  // real dark panel token.
  background: "var(--bg-panel, rgba(255,255,255,0.02))", border: "1px solid var(--border)", borderRadius: 8, padding: 16,
};
const deskHeader: React.CSSProperties = {
  display: "flex", justifyContent: "space-between", alignItems: "flex-start",
  gap: 12, marginBottom: 12, paddingBottom: 10, borderBottom: "1px solid var(--border)", flexWrap: "wrap",
};
const deskTitle: React.CSSProperties = { margin: 0, fontSize: 15, fontWeight: 700 };
const sectionHead: React.CSSProperties = {
  margin: "0 0 12px", fontSize: 14, fontWeight: 700,
  textTransform: "uppercase", letterSpacing: 0.7, color: "var(--text-muted)",
};
const tableStyle: React.CSSProperties = { width: "100%", borderCollapse: "collapse", fontSize: 13 };
const smallMuted: React.CSSProperties = { fontSize: 11, color: "var(--text-muted)" };
const inputStyle: React.CSSProperties = {
  width: 90, padding: "4px 8px", fontSize: 12,
  border: "1px solid var(--border)", borderRadius: 4,
  background: "var(--bg-elevated, rgba(255,255,255,0.04))", color: "var(--text)",
};
const codeStyle: React.CSSProperties = {
  fontSize: 11, fontFamily: "var(--font-mono, monospace)",
  background: "var(--bg-hover, rgba(255,255,255,0.05))", color: "var(--text-dim)",
  padding: "2px 5px", borderRadius: 3,
};
const errorBox: React.CSSProperties = {
  padding: 10, border: "1px solid #ef4444", borderRadius: 6,
  background: "rgba(239,68,68,0.1)", color: "#ef4444", fontSize: 13,
};
const execPillBase: React.CSSProperties = {
  fontSize: 10, fontWeight: 700, letterSpacing: "0.04em",
  padding: "2px 8px", borderRadius: 999, whiteSpace: "nowrap",
};
const execPillLive: React.CSSProperties = { ...execPillBase, background: "rgba(31,193,107,0.15)", color: "#1fc16b" };
const execPillSignal: React.CSSProperties = { ...execPillBase, background: "rgba(79,140,255,0.13)", color: "#4f8cff" };
function toggleBtn(busy: boolean, disabled: boolean): React.CSSProperties {
  return {
    fontSize: 10, padding: "2px 7px",
    border: `1px solid ${disabled ? "var(--border)" : "var(--text-dim)"}`,
    borderRadius: 4, background: "transparent",
    color: disabled ? "var(--text-muted)" : "var(--text-dim)",
    cursor: busy ? "wait" : disabled ? "not-allowed" : "pointer",
  };
}
function runButtonStyle(busy: boolean): React.CSSProperties {
  return {
    padding: "5px 11px", fontSize: 12, fontWeight: 600,
    background: busy ? "#888" : "#3b82a4", color: "#fff",
    border: "none", borderRadius: 4, cursor: busy ? "wait" : "pointer",
  };
}
const cancelButtonStyle: React.CSSProperties = {
  padding: "4px 9px", fontSize: 11, background: "transparent",
  color: "#a83a3a", border: "1px solid #a83a3a", borderRadius: 4, cursor: "pointer",
};
const chevronStyle: React.CSSProperties = {
  width: 18, height: 18, padding: 0, fontSize: 11,
  background: "transparent", color: "var(--text-muted)",
  border: "1px solid var(--border)", borderRadius: 3, cursor: "pointer", lineHeight: "1",
};
function promoteButtonStyle(busy: boolean): React.CSSProperties {
  return {
    padding: "4px 9px", fontSize: 11, fontWeight: 500, background: "transparent",
    color: busy ? "var(--text-muted)" : "#1fc16b",
    border: `1px solid ${busy ? "var(--border)" : "#1fc16b"}`,
    borderRadius: 4, cursor: busy ? "wait" : "pointer", whiteSpace: "nowrap",
  };
}
const resetButtonStyle: React.CSSProperties = {
  padding: "4px 9px", fontSize: 11, background: "transparent",
  color: "var(--text-muted)", border: "1px solid var(--border)", borderRadius: 4, cursor: "pointer",
};

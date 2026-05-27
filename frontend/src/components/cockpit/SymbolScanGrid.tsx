/**
 * SymbolScanGrid — compact per-symbol cards showing the latest
 * session's signal status across the whole universe. Reads the
 * decisions emitted by every strategy's latest session + renders
 * one card per (strategy, symbol) tuple in a responsive CSS grid.
 *
 * Versus the existing Decisions-tab table on Session Detail:
 *   * Card layout reads at a glance — trader sees 500 symbols'
 *     signal state without scrolling row-by-row.
 *   * Filter pills (ALL / FIRE / SKIP) + sector / strategy chips
 *     so the trader can scope to "show me all the AAPL-like fires
 *     today" without leaving /trader.
 *   * Click a card → drills into the session detail decisions tab
 *     pre-filtered to that symbol.
 */
import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { CockpitCard } from "../CockpitCard";
import type { LatestSession } from "../../types/cockpit";

type Card = {
  key: string;
  strategy: string;
  symbol: string;
  action: string;
  reason: string;
  /** Free-form signal/decision detail dict from the strategy. */
  detail: Record<string, unknown>;
  /** Pretty session-detail link for drill-down. */
  sessionId: string;
};

export function SymbolScanGrid({
  latestSessions, onHide,
}: {
  latestSessions: LatestSession[];
  onHide?: () => void;
}) {
  // Date filter — default to today's UTC session only so the grid
  // doesn't muddle "yesterday's signals" with "today's signals" when
  // multiple runs exist. Opt-in to historical via the pill.
  const [scope, setScope] = useState<"today" | "all">(() => {
    if (typeof window === "undefined") return "today";
    const saved = localStorage.getItem("cockpit.scan-grid.scope");
    return saved === "all" ? "all" : "today";
  });
  useEffect(() => {
    try { localStorage.setItem("cockpit.scan-grid.scope", scope); } catch { /* noop */ }
  }, [scope]);

  // View mode — text cards (default for small scans) vs heatmap
  // (dense color grid, designed for 500-symbol universe scans where
  // a card grid wastes a screen per row).
  const [view, setView] = useState<"cards" | "heatmap">(() => {
    if (typeof window === "undefined") return "cards";
    return (localStorage.getItem("cockpit.scan-grid.view") === "heatmap")
      ? "heatmap" : "cards";
  });
  useEffect(() => {
    try { localStorage.setItem("cockpit.scan-grid.view", view); } catch { /* noop */ }
  }, [view]);

  const sessionsInScope = useMemo(() => {
    if (scope === "all") return latestSessions;
    const todayUtc = new Date().toISOString().slice(0, 10);
    return latestSessions.filter(
      (s) => (s.completedAtUtc ?? "").slice(0, 10) === todayUtc,
    );
  }, [latestSessions, scope]);

  const cards = useMemo(() => collectCards(sessionsInScope), [sessionsInScope]);
  const [actionFilter, setActionFilter] = useState<"all" | "fire" | "skip">("all");
  const [query, setQuery] = useState("");

  const fireCount = cards.filter((c) => c.action.startsWith("fire-")).length;
  const skipCount = cards.filter((c) => c.action.startsWith("skip-")).length;

  const visible = cards.filter((c) => {
    if (actionFilter === "fire" && !c.action.startsWith("fire-")) return false;
    if (actionFilter === "skip" && !c.action.startsWith("skip-")) return false;
    if (query && !c.symbol.toLowerCase().includes(query.toLowerCase())) return false;
    return true;
  });

  return (
    <CockpitCard
      id="scan-grid"
      title="Symbol scan grid — latest run per strategy"
      badge={cards.length || undefined}
      defaultOpen={cards.length > 0}
      fullWidth
      onHide={onHide}
    >
      {cards.length === 0 ? (
        <span style={{ fontSize: 12, color: "var(--text-muted)" }}>
          No symbol-level decisions in the latest session yet. Trigger a
          universe-wide run from the cockpit Trigger panel to populate.
        </span>
      ) : (
        <>
          <FilterBar
            actionFilter={actionFilter}
            setActionFilter={setActionFilter}
            query={query}
            setQuery={setQuery}
            total={cards.length}
            fires={fireCount}
            skips={skipCount}
            visible={visible.length}
            scope={scope}
            setScope={setScope}
            view={view}
            setView={setView}
            hiddenSessionCount={latestSessions.length - sessionsInScope.length}
          />
          {visible.length === 0 ? (
            <div style={{ fontSize: 12, color: "var(--text-muted)", padding: "12px 0" }}>
              No symbols match the current filter.
            </div>
          ) : view === "heatmap" ? (
            <HeatmapView cards={visible} />
          ) : (
            <div style={{
              display: "grid",
              gridTemplateColumns: "repeat(auto-fill, minmax(160px, 1fr))",
              gap: 6,
              maxHeight: 480,
              overflowY: "auto",
              paddingRight: 4,
            }}>
              {visible.map((c) => (<SymbolCard key={c.key} card={c} />))}
            </div>
          )}
        </>
      )}
    </CockpitCard>
  );
}

function collectCards(latestSessions: LatestSession[]): Card[] {
  const cards: Card[] = [];
  for (const s of latestSessions) {
    for (const d of s.decisions) {
      cards.push({
        key: `${s.strategy}.${d.symbol}.${d.barTs ?? ""}`,
        strategy: s.strategy,
        symbol: d.symbol,
        action: d.action,
        reason: d.reason,
        detail: d.detail,
        sessionId: s.requestId,
      });
    }
  }
  // Fires first then skips; within each, alpha by symbol so the grid
  // is scannable left-to-right top-to-bottom.
  cards.sort((a, b) => {
    const aFire = a.action.startsWith("fire-") ? 0 : 1;
    const bFire = b.action.startsWith("fire-") ? 0 : 1;
    if (aFire !== bFire) return aFire - bFire;
    return a.symbol.localeCompare(b.symbol);
  });
  return cards;
}

function FilterBar({
  actionFilter, setActionFilter, query, setQuery,
  total, fires, skips, visible,
  scope, setScope, view, setView, hiddenSessionCount,
}: {
  actionFilter: "all" | "fire" | "skip";
  setActionFilter: (a: "all" | "fire" | "skip") => void;
  query: string;
  setQuery: (q: string) => void;
  total: number;
  fires: number;
  skips: number;
  visible: number;
  scope: "today" | "all";
  setScope: (s: "today" | "all") => void;
  view: "cards" | "heatmap";
  setView: (v: "cards" | "heatmap") => void;
  hiddenSessionCount: number;
}) {
  return (
    <div style={{ display: "flex", gap: 8, alignItems: "center", marginBottom: 8, flexWrap: "wrap" }}>
      <FilterPill value="all"  current={actionFilter} setter={setActionFilter} label={`ALL ${total}`} color="var(--text)" />
      <FilterPill value="fire" current={actionFilter} setter={setActionFilter} label={`FIRE ${fires}`} color="#1fc16b" />
      <FilterPill value="skip" current={actionFilter} setter={setActionFilter} label={`SKIP ${skips}`} color="#f59e0b" />
      <span style={{ width: 1, height: 14, background: "var(--border)" }} />
      <ScopePill value="today" current={scope} setter={setScope}
        label={hiddenSessionCount > 0
          ? `Today only (${hiddenSessionCount} older hidden)`
          : "Today only"}
      />
      <ScopePill value="all" current={scope} setter={setScope} label="Include past" />
      <span style={{ width: 1, height: 14, background: "var(--border)" }} />
      <ViewPill value="cards"   current={view} setter={setView} label="Cards" />
      <ViewPill value="heatmap" current={view} setter={setView} label="Heatmap" />
      <input
        type="text"
        placeholder="filter symbol…"
        value={query}
        onChange={(e) => setQuery(e.target.value)}
        style={{
          fontSize: 11, padding: "3px 8px",
          background: "transparent", color: "var(--text)",
          border: "1px solid var(--border)", borderRadius: 4,
          fontFamily: "monospace", width: 140,
        }}
      />
      {visible !== total && (
        <span style={{ fontSize: 11, color: "var(--text-muted)" }}>
          showing {visible} of {total}
        </span>
      )}
    </div>
  );
}

function ScopePill({
  value, current, setter, label,
}: {
  value: "today" | "all";
  current: "today" | "all";
  setter: (v: "today" | "all") => void;
  label: string;
}) {
  const active = value === current;
  return (
    <button
      onClick={() => setter(value)}
      style={{
        padding: "3px 10px", fontSize: 11, borderRadius: 999,
        border: `1px solid ${active ? "#4f8cff" : "var(--border)"}`,
        background: active ? "rgba(79,140,255,0.10)" : "transparent",
        color: active ? "#4f8cff" : "var(--text-dim)",
        cursor: "pointer", fontFamily: "monospace", letterSpacing: "0.02em",
      }}
    >
      {label}
    </button>
  );
}

function ViewPill({
  value, current, setter, label,
}: {
  value: "cards" | "heatmap";
  current: "cards" | "heatmap";
  setter: (v: "cards" | "heatmap") => void;
  label: string;
}) {
  const active = value === current;
  return (
    <button
      onClick={() => setter(value)}
      style={{
        padding: "3px 10px", fontSize: 11, borderRadius: 999,
        border: `1px solid ${active ? "#a855f7" : "var(--border)"}`,
        background: active ? "rgba(168,85,247,0.10)" : "transparent",
        color: active ? "#a855f7" : "var(--text-dim)",
        cursor: "pointer", fontFamily: "monospace", letterSpacing: "0.02em",
      }}
    >
      {label}
    </button>
  );
}

/**
 * HeatmapView — universe-scale visualisation. Each symbol = one
 * coloured cell; 500-symbol scans fit on one screen at ~50×24 px
 * per cell. Green = fire-buy, red = fire-sell, amber = skip,
 * gray = unclassified. Hover shows full reason; click drills into
 * Session Detail.
 *
 * Why a separate view from SymbolCard: the cards have room for
 * verbose detail (signal, cloud_position, vol). The heatmap trades
 * detail for density — the trader sees at-a-glance which corners of
 * the universe are firing without scrolling.
 */
function HeatmapView({ cards }: { cards: Card[] }) {
  return (
    <div style={{
      display: "grid",
      gridTemplateColumns: "repeat(auto-fill, minmax(56px, 1fr))",
      gap: 2,
      maxHeight: 520,
      overflowY: "auto",
      paddingRight: 4,
    }}>
      {cards.map((c) => (<HeatmapCell key={c.key} card={c} />))}
    </div>
  );
}

function HeatmapCell({ card }: { card: Card }) {
  const isFire = card.action.startsWith("fire-");
  const isBuy = isFire && (card.action.includes("entry") || card.action.includes("buy"));
  const isSell = isFire && (card.action.includes("exit") || card.action.includes("sell"));
  const isSkip = card.action.startsWith("skip-");
  const tone =
    isBuy  ? { bg: "rgba(31,193,107,0.65)",  fg: "#0a0f1a", border: "rgba(31,193,107,1)" }
  : isSell ? { bg: "rgba(239,68,68,0.65)",   fg: "#fff",    border: "rgba(239,68,68,1)" }
  : isFire ? { bg: "rgba(31,193,107,0.35)",  fg: "#fff",    border: "rgba(31,193,107,0.7)" }
  : isSkip ? { bg: "rgba(245,158,11,0.10)",  fg: "var(--text-dim)", border: "rgba(245,158,11,0.30)" }
           : { bg: "rgba(255,255,255,0.04)", fg: "var(--text-muted)", border: "var(--border)" };
  const tip = [
    card.symbol,
    card.action,
    card.reason || "",
  ].filter(Boolean).join(" · ");
  return (
    <Link
      to={`/paper-live/session/${encodeURIComponent(card.sessionId)}`}
      style={{
        display: "flex", alignItems: "center", justifyContent: "center",
        padding: "5px 4px",
        height: 30,
        background: tone.bg,
        color: tone.fg,
        border: `1px solid ${tone.border}`,
        borderRadius: 3,
        fontFamily: "monospace",
        fontSize: 10,
        fontWeight: 600,
        textDecoration: "none",
        textAlign: "center",
        overflow: "hidden",
      }}
      title={tip}
    >
      {card.symbol}
    </Link>
  );
}

function FilterPill({
  value, current, setter, label, color,
}: {
  value: "all" | "fire" | "skip";
  current: "all" | "fire" | "skip";
  setter: (v: "all" | "fire" | "skip") => void;
  label: string;
  color: string;
}) {
  const active = value === current;
  return (
    <button
      onClick={() => setter(value)}
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

function SymbolCard({ card }: { card: Card }) {
  const isFire = card.action.startsWith("fire-");
  const isSkip = card.action.startsWith("skip-");
  const tone = isFire ? FIRE_TONE : isSkip ? SKIP_TONE : NEUTRAL_TONE;
  const cloudPos = (card.detail.cloud_position as string | undefined) ?? null;
  const signal = (card.detail.signal as number | undefined);
  const vol = (card.detail.vol as number | undefined);

  return (
    <Link
      to={`/paper-live/session/${encodeURIComponent(card.sessionId)}`}
      style={{
        display: "block", textDecoration: "none",
        padding: "6px 8px",
        border: `1px solid ${tone.border}`,
        borderRadius: 4,
        background: tone.bg,
        color: "var(--text)",
      }}
      title={card.reason || card.action}
    >
      <div style={{
        display: "flex", alignItems: "baseline", gap: 6,
      }}>
        <span style={{ fontFamily: "monospace", fontWeight: 700, fontSize: 12 }}>
          {card.symbol}
        </span>
        <span style={{
          marginLeft: "auto",
          fontSize: 9, color: tone.fg, fontFamily: "monospace", fontWeight: 600,
          letterSpacing: "0.04em",
        }}>
          {shortAction(card.action)}
        </span>
      </div>
      <div style={{ fontSize: 10, color: "var(--text-muted)", marginTop: 2 }}>
        {cloudPos && <span>{cloudPos}</span>}
        {signal != null && (
          <span>{cloudPos ? " · " : ""}sig {signal.toFixed(1)}</span>
        )}
        {vol != null && (
          <span>{cloudPos || signal != null ? " · " : ""}vol {(vol * 100).toFixed(1)}%</span>
        )}
        {!cloudPos && signal == null && vol == null && (
          <span style={{ fontStyle: "italic" }}>{card.reason || "—"}</span>
        )}
      </div>
    </Link>
  );
}

// Action labels are verbose (e.g. "fire-moo-entry", "skip-flat-no-signal");
// the card's a tile so we shorten to the salient bit.
function shortAction(action: string): string {
  if (action === "fire-moo-entry") return "BUY";
  if (action === "fire-moo-exit") return "SELL";
  if (action.startsWith("fire-")) return action.replace("fire-", "").toUpperCase();
  if (action === "skip-flat-no-signal") return "wait";
  if (action === "skip-already-long") return "hold";
  if (action.startsWith("skip-")) return action.replace("skip-", "");
  return action;
}

const FIRE_TONE = {
  fg: "#1fc16b", bg: "rgba(31,193,107,0.08)", border: "rgba(31,193,107,0.40)",
};
const SKIP_TONE = {
  fg: "#f59e0b", bg: "rgba(245,158,11,0.04)", border: "rgba(245,158,11,0.20)",
};
const NEUTRAL_TONE = {
  fg: "var(--text-dim)", bg: "transparent", border: "var(--border)",
};

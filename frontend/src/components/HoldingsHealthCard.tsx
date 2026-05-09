import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api/client";
import { config } from "../config";
import type { CompareLatestResponse, CompareRow, CompareUniverseSummary } from "../api/types";
import {
  recommendHolding,
  DEFAULT_HORIZON,
  type Horizon,
  type HoldingLite,
  type HoldingRecommendation,
} from "../lib/recommendHolding";

interface T212Position extends HoldingLite {
  isin: string | null;
  unrealisedAbs: number | null;
  createdAt: string | null;
}

interface T212PositionsResponse {
  enabled: boolean;
  mode?: string;
  message?: string;
  fetchedAtUtc?: string;
  positionCount?: number;
  positions: T212Position[];
  error?: string | null;
  httpStatus?: number | null;
}

const HORIZON_OPTIONS: Horizon[] = ["6mo", "1y", "3y", "5y"];
const HORIZON_STORAGE_KEY = "tradepro.holdings.horizon";

// Action sort priority — TRIM first because trimming a profit-taking
// candidate is the most time-sensitive call (winner can give back gains
// fastest), then BUY_MORE (fresh opportunity worth weighing), HOLD last.
const ACTION_PRIORITY: Record<HoldingRecommendation["action"], number> = {
  TRIM: 0,
  BUY_MORE: 1,
  HOLD: 2,
};

const ACTION_EXPLAINER: Record<HoldingRecommendation["action"], string> = {
  BUY_MORE:
    "Your position is at a meaningful loss (≥3% below cost) AND price is in an oversold zone (RSI ≤ horizon threshold) AND the structural thesis is intact (BUY bucket or strong swing composite). The narrative shows the cost basis if you add an equal-size tranche.",
  HOLD:
    "No fresh edge in either direction — neither the average-down nor take-profit conditions fired. Default for positions in mid-zone P&L or when bucket / swing is mixed.",
  TRIM:
    "Either (a) the structural thesis broke (AVOID bucket or swing ≤ horizon broken-threshold) AND you're in profit, OR (b) you're up ≥15% with RSI overbought. Suggests trimming a partial position to lock gains before the trend gives back.",
};

const HORIZON_EXPLAINER =
  "The horizon profile shifts the RSI / swing thresholds the engine uses:\n" +
  "  6mo — react fastest (tight RSI bands, high intact-swing bar)\n" +
  "  1y  — default balance\n" +
  "  3y  — looser thresholds, tolerate WAIT bucket\n" +
  "  5y  — most patient, ride out short-term noise";

/**
 * Top-of-dashboard holdings health card. Pulls T212 positions and
 * cross-references each one against today's compare verdict (best
 * across all cached universes — same logic the email digest uses)
 * to render a per-position action recommendation: BUY_MORE / HOLD /
 * TRIM with the narrative the engine produced.
 *
 * Stays quiet (renders nothing) when T212 isn't configured or when
 * there are no open positions — the user already gets a setup walk-
 * through on /portfolio. This card is purely advisory on the dashboard.
 */
export function HoldingsHealthCard() {
  const [resp, setResp] = useState<T212PositionsResponse | null>(null);
  const [universes, setUniverses] = useState<CompareUniverseSummary[]>([]);
  const [universeRows, setUniverseRows] = useState<Record<string, CompareRow[]>>({});
  const [horizon, setHorizon] = useState<Horizon>(() => loadHorizon());
  const [loadError, setLoadError] = useState<string | null>(null);
  const [showHolds, setShowHolds] = useState(false);

  useEffect(() => {
    try { localStorage.setItem(HORIZON_STORAGE_KEY, horizon); } catch { /* private mode */ }
  }, [horizon]);

  useEffect(() => {
    let cancelled = false;
    fetch(`${config.apiBaseUrl}/api/integrations/trading212/positions`)
      .then((r) => (r.ok ? r.json() : Promise.reject(new Error(r.statusText))))
      .then((d) => { if (!cancelled) setResp(d as T212PositionsResponse); })
      .catch((e) => { if (!cancelled) setLoadError(String(e)); });
    return () => { cancelled = true; };
  }, []);

  useEffect(() => {
    let cancelled = false;
    api.compareUniverses()
      .then((r) => { if (!cancelled) setUniverses(r.universes); })
      .catch(() => {});
    return () => { cancelled = true; };
  }, []);

  useEffect(() => {
    if (universes.length === 0) return;
    let cancelled = false;
    Promise.all(
      universes.map((u) =>
        api.compareLatest(u.universe).then(
          (r: CompareLatestResponse) =>
            [u.universe, (r.payload?.rows ?? []) as CompareRow[]] as [string, CompareRow[]],
        ).catch(() => [u.universe, [] as CompareRow[]] as [string, CompareRow[]]),
      ),
    ).then((pairs) => {
      if (cancelled) return;
      const next: Record<string, CompareRow[]> = {};
      for (const [name, rows] of pairs) next[name] = rows;
      setUniverseRows(next);
    });
    return () => { cancelled = true; };
  }, [universes]);

  // Best-rank verdict per symbol across all cached universes.
  const verdictBySymbol = useMemo(() => {
    const m = new Map<string, CompareRow>();
    for (const rows of Object.values(universeRows)) {
      for (const r of rows) {
        const sym = (r.symbol ?? "").toUpperCase();
        if (!sym) continue;
        const existing = m.get(sym);
        if (!existing || (r.rank ?? 1e9) < (existing.rank ?? 1e9)) m.set(sym, r);
      }
    }
    return m;
  }, [universeRows]);

  if (loadError) return null;
  if (!resp) return null;
  if (!resp.enabled) return null;
  // Surface T212 errors loudly on the dashboard — the silent "no
  // positions" mode was misleading users with funded demo accounts.
  if (resp.error && (!resp.positions || resp.positions.length === 0)) {
    return (
      <section
        className="card"
        style={{ borderLeft: "3px solid var(--down)", color: "var(--text)" }}
      >
        <h2 style={{ margin: 0, fontSize: 15, color: "var(--down)" }}>
          Trading 212 fetch failed
        </h2>
        <p style={{ margin: "6px 0 0 0", fontSize: 13 }}>{resp.error}</p>
        {resp.httpStatus === 401 && (
          <p style={{ marginTop: 8, fontSize: 12, color: "var(--text-dim)" }}>
            401 = API key rejected. T212 uses a single key (no secret) in
            the Authorization header. Regenerate in T212 app → Settings →
            API (Beta), update <code>TRADEPRO_T212_API_KEY</code>, restart the api.
          </p>
        )}
      </section>
    );
  }
  if (!resp.positions || resp.positions.length === 0) return null;

  const allRecs: { pos: T212Position; rec: HoldingRecommendation }[] = resp.positions.map((p) => {
    const sym = (p.yahooSymbol ?? p.ticker ?? "").toUpperCase();
    const row = sym ? verdictBySymbol.get(sym) ?? null : null;
    return { pos: p, rec: recommendHolding(p, row, horizon) };
  });

  // Sort: TRIM → BUY_MORE → HOLD; within action, biggest |P&L %| first
  // so the loudest movers float to the top.
  allRecs.sort((a, b) => {
    const ap = ACTION_PRIORITY[a.rec.action];
    const bp = ACTION_PRIORITY[b.rec.action];
    if (ap !== bp) return ap - bp;
    const apnl = Math.abs(a.pos.unrealisedPct ?? 0);
    const bpnl = Math.abs(b.pos.unrealisedPct ?? 0);
    return bpnl - apnl;
  });

  const actionable = allRecs.filter((r) => r.rec.action !== "HOLD");
  const holds = allRecs.filter((r) => r.rec.action === "HOLD");

  const counts = {
    BUY_MORE: allRecs.filter((r) => r.rec.action === "BUY_MORE").length,
    HOLD: holds.length,
    TRIM: allRecs.filter((r) => r.rec.action === "TRIM").length,
  };

  // Group P&L totals by currency — summing mixed currencies as one
  // number is meaningless and was actively misleading.
  const totalsByCcy = new Map<string, number>();
  for (const p of resp.positions) {
    const ccy = (p.currency ?? "").toUpperCase() || "—";
    totalsByCcy.set(ccy, (totalsByCcy.get(ccy) ?? 0) + (p.unrealisedAbs ?? 0));
  }

  return (
    <section className="card" style={{ borderLeft: "3px solid var(--accent, #4f8cff)" }}>
      <header
        style={{
          display: "flex",
          alignItems: "baseline",
          justifyContent: "space-between",
          gap: 12,
          flexWrap: "wrap",
          marginBottom: 10,
        }}
      >
        <div>
          <h2 style={{ margin: 0, fontSize: 16 }}>
            Your portfolio · {resp.positionCount} position{resp.positionCount === 1 ? "" : "s"}
          </h2>
          <div style={{ fontSize: 12, color: "var(--text-dim)", marginTop: 2 }}>
            T212 <ModeChip mode={resp.mode} /> · today's advice on what you actually own.{" "}
            <Link to="/portfolio" style={{ color: "var(--text-dim)" }}>full holdings →</Link>
          </div>
        </div>
        <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
          <span
            style={{ fontSize: 11, color: "var(--text-muted)", cursor: "help" }}
            title={HORIZON_EXPLAINER}
          >
            Horizon ⓘ
          </span>
          <select
            value={horizon}
            onChange={(e) => setHorizon(e.target.value as Horizon)}
            style={{ fontSize: 12 }}
            title={HORIZON_EXPLAINER}
          >
            {HORIZON_OPTIONS.map((h) => <option key={h} value={h}>{h}</option>)}
          </select>
          <span style={{ fontSize: 12, color: "var(--text-dim)" }}>
            <UnrealisedTotals totalsByCcy={totalsByCcy} />
          </span>
        </div>
      </header>

      <div style={{ display: "flex", gap: 12, flexWrap: "wrap", marginBottom: 12, fontSize: 12 }}>
        <Pill label="Trim" count={counts.TRIM} colour="var(--down)" explainer={ACTION_EXPLAINER.TRIM} />
        <Pill label="Buy more" count={counts.BUY_MORE} colour="var(--up)" explainer={ACTION_EXPLAINER.BUY_MORE} />
        <Pill label="Hold" count={counts.HOLD} colour="var(--neutral)" explainer={ACTION_EXPLAINER.HOLD} />
      </div>

      {actionable.length === 0 && (
        <div style={{ fontSize: 12, color: "var(--text-dim)", padding: "8px 0" }}>
          Nothing actionable today — every holding is in the HOLD band. Expand below to review.
        </div>
      )}

      {actionable.length > 0 && (
        <ul style={{ margin: 0, padding: 0, listStyle: "none" }}>
          {actionable.map(({ pos, rec }) => (
            <HoldingRow key={keyFor(pos, rec.symbol)} pos={pos} rec={rec} />
          ))}
        </ul>
      )}

      {holds.length > 0 && (
        <details
          style={{ marginTop: 12, fontSize: 12, color: "var(--text-dim)" }}
          open={showHolds}
          onToggle={(e) => setShowHolds((e.target as HTMLDetailsElement).open)}
        >
          <summary style={{ cursor: "pointer", userSelect: "none" }}>
            {holds.length} HOLD position{holds.length === 1 ? "" : "s"} (no fresh edge today)
          </summary>
          <ul style={{ margin: "8px 0 0 0", padding: 0, listStyle: "none" }}>
            {holds.map(({ pos, rec }) => (
              <HoldingRow key={keyFor(pos, rec.symbol)} pos={pos} rec={rec} />
            ))}
          </ul>
        </details>
      )}
    </section>
  );
}

function HoldingRow({ pos, rec }: { pos: T212Position; rec: HoldingRecommendation }) {
  return (
    <li style={{ padding: "10px 0", borderTop: "1px solid var(--border, rgba(37,50,86,0.4))" }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline", gap: 12, flexWrap: "wrap" }}>
        <div style={{ minWidth: 220 }}>
          <span style={{ fontWeight: 600 }}>{pos.instrumentName ?? rec.symbol}</span>
          <span style={{ fontSize: 11, color: "var(--text-muted)", marginLeft: 8 }}>
            {pos.ticker} · {pos.quantity.toFixed(4)} @ {pos.averagePricePaid?.toFixed(2) ?? "—"} {pos.currency ?? ""}
          </span>
        </div>
        <PnL pct={pos.unrealisedPct} abs={pos.unrealisedAbs} ccy={pos.currency ?? ""} />
        <ActionBadge action={rec.action} />
      </div>
      <div style={{ marginTop: 4, fontSize: 12, color: "var(--text-dim)", lineHeight: 1.45 }}>
        {rec.narrative}
      </div>
      {rec.evidence.length > 0 && (
        <div style={{ marginTop: 4, fontSize: 11, color: "var(--text-muted)" }}>
          {rec.evidence.join(" · ")}
        </div>
      )}
    </li>
  );
}

function ActionBadge({ action }: { action: HoldingRecommendation["action"] }) {
  const map = {
    BUY_MORE: { colour: "var(--up)", label: "BUY MORE" },
    HOLD:     { colour: "var(--neutral)", label: "HOLD" },
    TRIM:     { colour: "var(--down)", label: "TRIM" },
  } as const;
  const { colour, label } = map[action];
  return (
    <span
      title={ACTION_EXPLAINER[action]}
      style={{
        color: colour,
        border: `1px solid ${colour}`,
        borderRadius: 4,
        padding: "2px 8px",
        fontSize: 11,
        fontWeight: 700,
        letterSpacing: "0.06em",
        whiteSpace: "nowrap",
        cursor: "help",
      }}
    >
      {label}
    </span>
  );
}

function PnL({ pct, abs, ccy }: { pct: number | null; abs: number | null; ccy: string }) {
  if (pct === null && abs === null) return <span style={{ fontSize: 12, color: "var(--text-muted)" }}>—</span>;
  const v = pct ?? 0;
  const colour = v > 0 ? "var(--up)" : v < 0 ? "var(--down)" : "var(--text-dim)";
  return (
    <span className="num" style={{ fontSize: 12, color: colour, fontWeight: 600, whiteSpace: "nowrap" }}>
      {v >= 0 ? "+" : ""}{v.toFixed(2)}%
      {abs !== null && (
        <span style={{ color: "var(--text-muted)", fontWeight: 400, marginLeft: 6 }}>
          ({abs >= 0 ? "+" : ""}{abs.toFixed(2)} {ccy})
        </span>
      )}
    </span>
  );
}

function Pill({
  label, count, colour, explainer,
}: {
  label: string; count: number; colour: string; explainer: string;
}) {
  return (
    <span
      title={explainer}
      style={{ color: count > 0 ? colour : "var(--text-muted)", fontWeight: 600, cursor: "help" }}
    >
      {count} {label}
    </span>
  );
}

function UnrealisedTotals({ totalsByCcy }: { totalsByCcy: Map<string, number> }) {
  const entries = Array.from(totalsByCcy.entries()).filter(([, v]) => v !== 0);
  if (entries.length === 0) return <span>—</span>;
  return (
    <span title="Unrealised P&L — totals per currency (T212 holdings can be in mixed currencies; not summed across)">
      {entries.map(([ccy, v], i) => (
        <span key={ccy}>
          {i > 0 && <span style={{ color: "var(--text-muted)" }}> · </span>}
          <span style={{ color: v >= 0 ? "var(--up)" : "var(--down)", fontWeight: 600 }}>
            {v >= 0 ? "+" : ""}{v.toFixed(2)} {ccy}
          </span>
        </span>
      ))}
      <span style={{ color: "var(--text-muted)", marginLeft: 6 }}>unrealised</span>
    </span>
  );
}

function ModeChip({ mode }: { mode?: string }) {
  if (!mode) return null;
  const colour = mode === "live" ? "var(--down)" : mode === "demo" ? "var(--neutral)" : "var(--text-muted)";
  return (
    <span style={{ color: colour, fontWeight: 700, textTransform: "uppercase", letterSpacing: "0.06em" }}>
      {mode}
    </span>
  );
}

function loadHorizon(): Horizon {
  try {
    const v = localStorage.getItem(HORIZON_STORAGE_KEY);
    if (v && (HORIZON_OPTIONS as string[]).includes(v)) return v as Horizon;
  } catch { /* private mode */ }
  return DEFAULT_HORIZON;
}

function keyFor(pos: T212Position, sym: string): string {
  return pos.ticker ?? pos.isin ?? sym;
}

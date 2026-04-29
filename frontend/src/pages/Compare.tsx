import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api/client";
import type {
  CompareLatestResponse,
  CompareMarketContext,
  CompareRow,
  CompareUniverseSummary,
  DecisionCheck,
  EntrySignal,
} from "../api/types";
import { Info } from "../components/Info";

/** "Should I invest today, and if yes, in what?" page.
 *
 * Triages the comparator output (5 strategies × N ETFs) down to one card
 * per ETF, bucketed BUY / WAIT / AVOID, with a strategy-consensus vote
 * ("4 of 5 strategies are currently long") and the per-symbol entry
 * verdict from market_state. Click a card to see all 5 strategies'
 * stats and the per-regime stress breakdown.
 *
 * Bucket assignment uses BOTH the price-based market_state and the
 * strategy vote: a confident BUY needs both an entry-friendly price
 * setup and a majority of strategies already in position. */

const PRICE_VERDICTS: EntrySignal[] = ["BUY", "HOLD", "WAIT", "AVOID"];

interface SymbolView {
  symbol: string;
  rows: CompareRow[];           // sorted by rank ascending (best first)
  bestRow: CompareRow;
  marketSignal: EntrySignal;    // from market_state.entry_signal (per-symbol)
  marketReason: string;
  longCount: number;            // # strategies currently in position
  total: number;
  bucket: "BUY" | "WAIT" | "AVOID";
  bucketReason: string;
}

export function Compare() {
  const [universes, setUniverses] = useState<CompareUniverseSummary[]>([]);
  const [universe, setUniverse] = useState<string>("");
  const [data, setData] = useState<CompareLatestResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [openSymbol, setOpenSymbol] = useState<string | null>(null);

  useEffect(() => {
    api.compareUniverses()
      .then((r) => {
        setUniverses(r.universes);
        if (r.universes.length > 0 && !universe) {
          setUniverse(r.universes[0].universe);
        }
      })
      .catch((e) => setError(String(e)));
  }, []);

  useEffect(() => {
    if (!universe) return;
    setLoading(true);
    setError(null);
    setOpenSymbol(null);
    api.compareLatest(universe)
      .then(setData)
      .catch((e) => {
        setData(null);
        setError(String(e));
      })
      .finally(() => setLoading(false));
  }, [universe]);

  const views: SymbolView[] = useMemo(() => buildSymbolViews(data?.payload?.rows ?? []), [data]);
  const buys = views.filter((v) => v.bucket === "BUY");
  const waits = views.filter((v) => v.bucket === "WAIT");
  const avoids = views.filter((v) => v.bucket === "AVOID");
  const rankMetric = data?.rankMetric ?? data?.payload?.rank_metric ?? "sharpe";

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 20 }}>
      <div>
        <h1 style={{ margin: 0, fontSize: 24 }}>Should I invest today?</h1>
        <p style={{ color: "var(--text-dim)", margin: "6px 0 0 0", maxWidth: 820 }}>
          For long-horizon (months-to-years) ETF investing. Each ETF goes
          into <strong style={{ color: "var(--up)" }}>BUY today</strong>,{" "}
          <strong style={{ color: "var(--neutral)" }}>WAIT</strong>, or{" "}
          <strong style={{ color: "var(--down)" }}>AVOID</strong> based on the
          combination of (a) price action — uptrend, RSI, drawdown — and
          (b) how many of the 5 strategies are currently long the asset.
        </p>
      </div>

      <ProvenanceBar data={data} loading={loading} />

      {data?.payload?.market_context && (
        <MarketContextBar ctx={data.payload.market_context} />
      )}

      <section
        className="card"
        style={{
          display: "grid",
          gridTemplateColumns: "repeat(auto-fit, minmax(180px, 1fr))",
          gap: 14,
          alignItems: "end",
        }}
      >
        <label style={{ display: "flex", flexDirection: "column", gap: 5 }}>
          <span className="stat-label">Universe</span>
          <select value={universe} onChange={(e) => setUniverse(e.target.value)}>
            {universes.length === 0 && <option value="">(none yet)</option>}
            {universes.map((u) => (
              <option key={u.universe} value={u.universe}>
                {u.universe} ({u.rowCount} rows)
              </option>
            ))}
          </select>
        </label>
        {data && (
          <>
            <Stat label="Ranked by" value={data.rankMetric ?? "—"} />
            <Stat label="Window" value={`${data.payload.from} → ${data.payload.to}`} />
            <Stat label="ETFs × strategies" value={`${views.length} × ${views[0]?.total ?? 0}`} />
          </>
        )}
      </section>

      {error && <EmptyState error={error} />}

      {data && views.length > 0 && (
        <>
          <VerdictHeadline
            buys={buys}
            waits={waits}
            avoids={avoids}
            rankMetric={rankMetric}
          />

          <div
            style={{
              display: "grid",
              gridTemplateColumns: "repeat(auto-fit, minmax(280px, 1fr))",
              gap: 16,
            }}
          >
            <Bucket
              title="Buy today"
              tone="up"
              items={buys}
              openSymbol={openSymbol}
              setOpen={setOpenSymbol}
              rankMetric={rankMetric}
            />
            <Bucket
              title="Wait"
              tone="neutral"
              items={waits}
              openSymbol={openSymbol}
              setOpen={setOpenSymbol}
              rankMetric={rankMetric}
            />
            <Bucket
              title="Avoid"
              tone="down"
              items={avoids}
              openSymbol={openSymbol}
              setOpen={setOpenSymbol}
              rankMetric={rankMetric}
            />
          </div>
        </>
      )}
    </div>
  );
}

// --------------------------------------------------------------------------
// Bucket assignment + per-symbol aggregation
// --------------------------------------------------------------------------

function buildSymbolViews(rows: CompareRow[]): SymbolView[] {
  if (rows.length === 0) return [];
  const groups = new Map<string, CompareRow[]>();
  for (const row of rows) {
    const arr = groups.get(row.symbol) ?? [];
    arr.push(row);
    groups.set(row.symbol, arr);
  }

  const views: SymbolView[] = [];
  for (const [symbol, rs] of groups) {
    const sorted = [...rs].sort((a, b) => (a.rank ?? 1e9) - (b.rank ?? 1e9));
    const best = sorted[0];
    const ms = best.market_state;
    const longCount = sorted.filter((r) => r.in_position).length;
    const total = sorted.length;
    const majorityLong = longCount > total / 2;
    const priceVerdict: EntrySignal = (PRICE_VERDICTS as string[]).includes(ms?.entry_signal ?? "")
      ? (ms.entry_signal as EntrySignal)
      : "HOLD";

    let bucket: SymbolView["bucket"];
    let reason: string;
    if (priceVerdict === "AVOID") {
      bucket = "AVOID";
      reason = ms?.entry_reason || "Confirmed downtrend.";
    } else if (priceVerdict === "WAIT") {
      bucket = "WAIT";
      reason = ms?.entry_reason || "Better entries likely soon.";
    } else if (majorityLong && (priceVerdict === "BUY" || priceVerdict === "HOLD")) {
      bucket = "BUY";
      reason = ms?.entry_reason ||
        `${longCount} of ${total} strategies currently long; price action supports entry.`;
    } else {
      // Price OK but strategies don't yet agree — wait for confirmation.
      bucket = "WAIT";
      reason = `Only ${longCount} of ${total} strategies are currently long — wait for more confirmation.`;
    }

    views.push({
      symbol, rows: sorted, bestRow: best,
      marketSignal: priceVerdict, marketReason: ms?.entry_reason ?? "",
      longCount, total, bucket, bucketReason: reason,
    });
  }
  views.sort((a, b) => (a.bestRow.rank ?? 1e9) - (b.bestRow.rank ?? 1e9));
  return views;
}

// --------------------------------------------------------------------------
// Realness / provenance banner
// --------------------------------------------------------------------------

function ProvenanceBar({
  data,
  loading,
}: {
  data: CompareLatestResponse | null;
  loading: boolean;
}) {
  if (loading) return <div style={{ color: "var(--text-dim)" }}>Loading…</div>;
  if (!data) return null;
  const generated = new Date(data.generatedAtUtc);
  const received = new Date(data.receivedAtUtc);
  const ageMin = Math.max(1, Math.round((Date.now() - generated.getTime()) / 60000));
  const ageStr = ageMin < 60
    ? `${ageMin} min ago`
    : ageMin < 60 * 24
      ? `${Math.round(ageMin / 60)} h ago`
      : `${Math.round(ageMin / 1440)} d ago`;
  return (
    <div
      className="card"
      style={{
        display: "flex",
        gap: 14,
        flexWrap: "wrap",
        alignItems: "center",
        borderLeft: "3px solid var(--up)",
        padding: "10px 14px",
      }}
    >
      <span
        style={{
          fontSize: 11,
          fontWeight: 700,
          color: "var(--up)",
          letterSpacing: "0.06em",
          textTransform: "uppercase",
        }}
      >
        ● Live
      </span>
      <span style={{ fontSize: 12, color: "var(--text-dim)" }}>
        Real Yahoo Finance prices, computed in Python locally{" "}
        <strong style={{ color: "var(--text)" }}>{ageStr}</strong>.
      </span>
      <span
        style={{
          marginLeft: "auto",
          fontSize: 11,
          color: "var(--text-muted)",
          fontFamily: "ui-monospace, SFMono-Regular, Menlo, monospace",
        }}
      >
        run {data.runId?.slice(0, 8) ?? "—"} ·{" "}
        gen {generated.toLocaleString()} ·{" "}
        recv {received.toLocaleTimeString()} ·{" "}
        {data.rowCount} rows
      </span>
    </div>
  );
}

// --------------------------------------------------------------------------
// Verdict headline + buckets
// --------------------------------------------------------------------------

function VerdictHeadline({
  buys,
  waits,
  avoids,
  rankMetric,
}: {
  buys: SymbolView[];
  waits: SymbolView[];
  avoids: SymbolView[];
  rankMetric: string;
}) {
  const top = buys[0] ?? waits[0] ?? avoids[0];
  const verdict =
    buys.length === 0
      ? "No clear buys today — let the market come to you."
      : `${buys.length} BUY · ${waits.length} WAIT · ${avoids.length} AVOID`;
  return (
    <section
      className="card"
      style={{
        borderTop: `3px solid var(--up)`,
        paddingTop: 14,
        display: "flex",
        gap: 18,
        flexWrap: "wrap",
        alignItems: "center",
      }}
    >
      <div style={{ minWidth: 220 }}>
        <div className="stat-label">Today's verdict</div>
        <div style={{ fontSize: 22, fontWeight: 700, marginTop: 4 }}>{verdict}</div>
      </div>
      {top && (
        <div
          style={{
            flex: 1,
            minWidth: 280,
            padding: "10px 14px",
            borderRadius: 8,
            background: "rgba(255,255,255,0.02)",
            borderLeft: `3px solid ${bucketColour(top.bucket)}`,
          }}
        >
          <div className="stat-label">Top {top.bucket === "BUY" ? "buy" : "candidate"}</div>
          <div style={{ fontSize: 18, fontWeight: 700, marginTop: 4 }}>
            <Link to={`/signals?symbol=${encodeURIComponent(top.symbol)}`} style={{ color: "var(--text)" }}>
              {top.symbol}
            </Link>{" "}
            <span style={{ color: "var(--text-dim)", fontWeight: 400, fontSize: 13 }}>
              · {top.bestRow.strategy_label} ({rankMetric}{" "}
              {fmtNum(top.bestRow.stats?.[rankMetric])})
            </span>
          </div>
          <div style={{ marginTop: 4, color: "var(--text-dim)", fontSize: 13 }}>
            {top.longCount} of {top.total} strategies currently long. {top.bucketReason}
          </div>
        </div>
      )}
    </section>
  );
}

function Bucket({
  title,
  tone,
  items,
  openSymbol,
  setOpen,
  rankMetric,
}: {
  title: string;
  tone: "up" | "down" | "neutral";
  items: SymbolView[];
  openSymbol: string | null;
  setOpen: (s: string | null) => void;
  rankMetric: string;
}) {
  const colour = toneColour(tone);
  return (
    <div className="card" style={{ borderTop: `3px solid ${colour}`, paddingTop: 14 }}>
      <div style={{ display: "flex", alignItems: "baseline", justifyContent: "space-between", marginBottom: 10 }}>
        <h3
          style={{
            margin: 0,
            color: colour,
            textTransform: "uppercase",
            letterSpacing: "0.08em",
            fontSize: 12,
          }}
        >
          {title}
          {tone === "up" && <Info k="entry_signal" />}
        </h3>
        <span className="num" style={{ color: "var(--text-muted)", fontSize: 12 }}>
          {items.length}
        </span>
      </div>
      {items.length === 0 && (
        <div style={{ color: "var(--text-muted)", fontSize: 13 }}>Nothing here.</div>
      )}
      <ul style={{ margin: 0, padding: 0, listStyle: "none" }}>
        {items.map((v) => (
          <SymbolCard
            key={v.symbol}
            view={v}
            colour={colour}
            open={openSymbol === v.symbol}
            onToggle={() => setOpen(openSymbol === v.symbol ? null : v.symbol)}
            rankMetric={rankMetric}
          />
        ))}
      </ul>
    </div>
  );
}

function SymbolCard({
  view,
  colour,
  open,
  onToggle,
  rankMetric,
}: {
  view: SymbolView;
  colour: string;
  open: boolean;
  onToggle: () => void;
  rankMetric: string;
}) {
  const ms = view.bestRow.market_state;
  return (
    <li
      style={{
        padding: "10px 0",
        borderBottom: "1px solid rgba(37, 50, 86, 0.4)",
      }}
    >
      <div
        style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline", gap: 8, cursor: "pointer" }}
        onClick={onToggle}
      >
        <Link
          to={`/signals?symbol=${encodeURIComponent(view.symbol)}`}
          style={{ color: "var(--text)", fontWeight: 600 }}
          onClick={(e) => e.stopPropagation()}
        >
          <span className="num">{view.symbol}</span>
        </Link>
        <VoteBar long={view.longCount} total={view.total} colour={colour} />
      </div>
      <div style={{ fontSize: 11, color: "var(--text-dim)", marginTop: 2 }}>
        {view.bucketReason}
      </div>
      <div style={{ fontSize: 11, color: "var(--text-muted)", marginTop: 4 }}>
        Best historical: {view.bestRow.strategy_label} ·{" "}
        {rankMetric} {fmtNum(view.bestRow.stats?.[rankMetric])} ·{" "}
        CAGR {fmtNum(view.bestRow.stats?.cagr_pct)}% ·{" "}
        max DD {fmtNum(view.bestRow.stats?.max_drawdown_pct)}% ·{" "}
        RSI {fmtNum(ms?.rsi_14, 0)}
      </div>
      {open && <ExpandedDetail view={view} />}
    </li>
  );
}

function VoteBar({ long, total, colour }: { long: number; total: number; colour: string }) {
  const dots = [];
  for (let i = 0; i < total; i++) {
    dots.push(
      <span
        key={i}
        style={{
          display: "inline-block",
          width: 8,
          height: 8,
          borderRadius: 4,
          marginRight: 2,
          background: i < long ? colour : "rgba(255,255,255,0.12)",
        }}
      />
    );
  }
  return (
    <span
      style={{ display: "inline-flex", alignItems: "center", gap: 6 }}
      title={`${long} of ${total} strategies currently long`}
    >
      <span style={{ fontSize: 12, color: colour, fontWeight: 600 }}>
        {long}/{total}
      </span>
      <span style={{ display: "inline-flex" }}>{dots}</span>
    </span>
  );
}

function ExpandedDetail({ view }: { view: SymbolView }) {
  const trace = view.bestRow.market_state?.decision_trace ?? [];
  return (
    <div style={{ marginTop: 8, padding: 10, background: "rgba(0,0,0,0.18)", borderRadius: 6 }}>
      {trace.length > 0 && (
        <div style={{ marginBottom: 12 }}>
          <div className="stat-label" style={{ marginBottom: 4 }}>
            Why the verdict — every check, not just the one that fired
          </div>
          <ul style={{ margin: 0, padding: 0, listStyle: "none" }}>
            {trace.map((c, i) => (
              <DecisionRow key={i} check={c} />
            ))}
          </ul>
        </div>
      )}

      <div style={{ marginBottom: 12 }}>
        <div className="stat-label" style={{ marginBottom: 4 }}>Strategies on {view.symbol}</div>
        <table style={{ width: "100%", fontSize: 11, borderCollapse: "collapse" }}>
          <thead>
            <tr style={{ color: "var(--text-muted)", textAlign: "left" }}>
              <th style={{ padding: "3px 6px" }}>Strategy</th>
              <th style={{ padding: "3px 6px", textAlign: "right" }}>CAGR %</th>
              <th style={{ padding: "3px 6px", textAlign: "right" }}>Sharpe</th>
              <th style={{ padding: "3px 6px", textAlign: "right" }}>Max DD %</th>
              <th style={{ padding: "3px 6px" }}>Now long?</th>
            </tr>
          </thead>
          <tbody>
            {view.rows.map((r) => (
              <tr key={r.strategy} style={{ borderTop: "1px solid var(--border)" }}>
                <td style={{ padding: "3px 6px", color: "var(--text)" }}>{r.strategy_label}</td>
                <td className="num" style={{ padding: "3px 6px", textAlign: "right" }}>{fmtNum(r.stats?.cagr_pct)}</td>
                <td className="num" style={{ padding: "3px 6px", textAlign: "right" }}>{fmtNum(r.stats?.sharpe)}</td>
                <td className="num" style={{ padding: "3px 6px", textAlign: "right" }}>{fmtNum(r.stats?.max_drawdown_pct)}</td>
                <td style={{ padding: "3px 6px", color: r.in_position ? "var(--up)" : "var(--text-muted)" }}>
                  {r.in_position
                    ? `LONG (since ${r.position_since?.slice(0, 10) ?? "—"})`
                    : "flat"}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {view.bestRow.regimes.length > 0 && (
        <div>
          <div className="stat-label" style={{ marginBottom: 4 }}>
            How the best strategy performed in past stress windows{" "}
            <span style={{ color: "var(--text-muted)", fontWeight: 400 }}>
              (historical evidence — not a prediction)
            </span>
          </div>
          <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(170px, 1fr))", gap: 6 }}>
            {view.bestRow.regimes.map((r) => (
              <div key={r.key} style={{ borderLeft: `2px solid ${regimeColour(r.kind)}`, paddingLeft: 6, fontSize: 11 }}>
                <div style={{ color: "var(--text)", fontWeight: 600 }}>{r.name}</div>
                <div className="num" style={{ color: "var(--text-dim)" }}>
                  return during it {fmtNum(r.return_pct)}% · max drop {fmtNum(r.max_drawdown_pct)}%
                </div>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

function DecisionRow({ check }: { check: DecisionCheck }) {
  const colour =
    check.status === "pass"
      ? "var(--up)"
      : check.status === "fail"
      ? "var(--down)"
      : "var(--neutral)";
  const glyph = check.status === "pass" ? "✓" : check.status === "fail" ? "✗" : "•";
  return (
    <li
      style={{
        display: "flex",
        gap: 8,
        padding: "3px 0",
        fontSize: 11,
        color: "var(--text-dim)",
      }}
    >
      <span style={{ color: colour, fontWeight: 700, width: 14, textAlign: "center" }}>{glyph}</span>
      <span style={{ color: "var(--text)", minWidth: 180 }}>{check.name}</span>
      <span>{check.detail}</span>
    </li>
  );
}

function MarketContextBar({ ctx }: { ctx: CompareMarketContext }) {
  const vixColour =
    ctx.vix_regime === "stressed"
      ? "var(--down)"
      : ctx.vix_regime === "calm"
      ? "var(--up)"
      : "var(--neutral)";
  return (
    <section
      className="card"
      style={{
        display: "flex",
        gap: 18,
        flexWrap: "wrap",
        alignItems: "center",
        padding: "10px 14px",
        borderLeft: `3px solid ${vixColour}`,
      }}
    >
      <div style={{ minWidth: 120 }}>
        <div className="stat-label">Market context</div>
        <div style={{ fontSize: 11, color: "var(--text-muted)", marginTop: 2 }}>
          fear / rates / S&P drawdown
        </div>
      </div>
      <ContextStat
        label="VIX"
        value={ctx.vix !== null ? ctx.vix.toFixed(1) : "—"}
        sub={ctx.vix_regime ?? "—"}
        colour={vixColour}
      />
      <ContextStat
        label="10Y yield"
        value={ctx.tnx !== null ? `${ctx.tnx.toFixed(2)}%` : "—"}
        sub={ctx.tnx_trend ?? "—"}
      />
      <ContextStat
        label="S&P off peak"
        value={ctx.spy_drawdown_pct !== null ? `${ctx.spy_drawdown_pct.toFixed(1)}%` : "—"}
        sub={
          ctx.spy_drawdown_pct !== null && ctx.spy_drawdown_pct < -10
            ? "correction"
            : ctx.spy_drawdown_pct !== null && ctx.spy_drawdown_pct < -5
            ? "pullback"
            : "near highs"
        }
      />
      <ContextStat
        label="Active stress regime"
        value={ctx.active_stress_regimes.length ? ctx.active_stress_regimes.join(", ") : "none"}
        sub={ctx.active_stress_regimes.length ? "elevated risk" : "no flag"}
        colour={ctx.active_stress_regimes.length ? "var(--down)" : "var(--text-muted)"}
      />
      <div style={{ marginLeft: "auto", fontSize: 11, color: "var(--text-muted)", maxWidth: 360 }}>
        Informational — affects how you read the buckets, not the bucket assignment itself.
      </div>
    </section>
  );
}

function ContextStat({
  label,
  value,
  sub,
  colour,
}: {
  label: string;
  value: string;
  sub: string;
  colour?: string;
}) {
  return (
    <div style={{ minWidth: 100 }}>
      <div className="stat-label">{label}</div>
      <div className="num" style={{ marginTop: 2, fontSize: 14, fontWeight: 600, color: colour ?? "var(--text)" }}>
        {value}
      </div>
      <div style={{ fontSize: 10, color: "var(--text-muted)", textTransform: "uppercase", letterSpacing: "0.05em" }}>
        {sub}
      </div>
    </div>
  );
}

// --------------------------------------------------------------------------
// Helpers
// --------------------------------------------------------------------------

function EmptyState({ error }: { error: string }) {
  return (
    <div className="card" style={{ borderColor: "var(--down)", color: "var(--text-dim)" }}>
      <div style={{ color: "var(--down)", marginBottom: 6, fontWeight: 600 }}>
        No comparison data yet
      </div>
      <div style={{ fontSize: 13 }}>
        {error.includes("404")
          ? "Nothing has been pushed for this universe yet."
          : error}
      </div>
      <pre
        style={{
          marginTop: 10,
          padding: 10,
          background: "rgba(0,0,0,0.25)",
          borderRadius: 6,
          fontSize: 12,
          overflowX: "auto",
        }}
      >
{`# from the Mac (in /strategies):
uv run tradepro-compare --watchlist etf_us_core --currency USD --stamp-duty 0 --push`}
      </pre>
    </div>
  );
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <div className="stat-label">{label}</div>
      <div className="num" style={{ marginTop: 4, fontSize: 13 }}>{value}</div>
    </div>
  );
}

function bucketColour(b: SymbolView["bucket"]): string {
  if (b === "BUY") return "var(--up)";
  if (b === "AVOID") return "var(--down)";
  return "var(--neutral)";
}

function toneColour(tone: "up" | "down" | "neutral"): string {
  if (tone === "up") return "var(--up)";
  if (tone === "down") return "var(--down)";
  return "var(--neutral)";
}

function regimeColour(kind: string): string {
  switch (kind) {
    case "crash": return "var(--down)";
    case "drawdown": return "var(--neutral)";
    case "recovery": return "var(--up)";
    default: return "var(--text-dim)";
  }
}

function fmtNum(x: unknown, digits: number = 2): string {
  if (x === null || x === undefined) return "—";
  const n = Number(x);
  if (!Number.isFinite(n)) return "—";
  return n.toLocaleString(undefined, { minimumFractionDigits: digits, maximumFractionDigits: digits });
}

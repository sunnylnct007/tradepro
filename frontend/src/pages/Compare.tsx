import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api/client";
import type {
  CompareLatestResponse,
  CompareRow,
  CompareUniverseSummary,
  EntrySignal,
} from "../api/types";

/** "Where should I put money for the long term?" page.
 *
 * Renders the latest ranked-comparison payload pushed in from the local
 * Mac via tradepro-compare. Each row carries (a) backtest stats, (b) a
 * per-row "now or wait" market-state verdict, and (c) per-regime stress
 * breakdowns. The page picks the universe + presents the headline + a
 * compact ranked table; clicking a row reveals the regime evidence. */
export function Compare() {
  const [universes, setUniverses] = useState<CompareUniverseSummary[]>([]);
  const [universe, setUniverse] = useState<string>("");
  const [data, setData] = useState<CompareLatestResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [showAll, setShowAll] = useState(false);
  const [openRow, setOpenRow] = useState<string | null>(null);

  // Initial load — list available universes.
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

  // Fetch the chosen universe's payload.
  useEffect(() => {
    if (!universe) return;
    setLoading(true);
    setError(null);
    setOpenRow(null);
    api.compareLatest(universe)
      .then(setData)
      .catch((e) => {
        setData(null);
        setError(String(e));
      })
      .finally(() => setLoading(false));
  }, [universe]);

  const top = data?.payload?.rows ?? [];
  const visibleRows = useMemo(() => (showAll ? top : top.slice(0, 10)), [top, showAll]);
  const bestRow = top[0];

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 20 }}>
      <div>
        <h1 style={{ margin: 0, fontSize: 24 }}>Best ETF to invest in (long horizon)</h1>
        <p style={{ color: "var(--text-dim)", margin: "6px 0 0 0", maxWidth: 820 }}>
          Heavy comparison runs on a Mac and gets pushed here. For each ETF the
          ranker reports backtest stats (CAGR, Sharpe, max drawdown), a per-row
          <em> now-or-wait</em> verdict, and how each instrument fared through
          historical stress windows (2008 GFC, 2020 COVID, 2022 rate shock, …).
          Use this when you have months-to-years to invest, not days.
        </p>
      </div>

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
            <Stat label="Last computed" value={fmtAge(data.generatedAtUtc)} />
            <Stat label="Ranked by" value={data.rankMetric ?? "—"} />
            <Stat
              label="Window"
              value={`${data.payload.from} → ${data.payload.to}`}
            />
          </>
        )}
      </section>

      {loading && <div style={{ color: "var(--text-dim)" }}>Loading…</div>}

      {error && (
        <EmptyState error={error} />
      )}

      {data && bestRow && (
        <BestPickCard row={bestRow} rankMetric={data.rankMetric ?? data.payload.rank_metric} />
      )}

      {data && top.length > 0 && (
        <section className="card" style={{ padding: 0, overflow: "hidden" }}>
          <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 13 }}>
            <thead>
              <tr style={{ background: "var(--bg-hover)", color: "var(--text-dim)", textAlign: "left" }}>
                <Th>#</Th>
                <Th>Symbol</Th>
                <Th>Strategy</Th>
                <Th align="right">CAGR %</Th>
                <Th align="right">Sharpe</Th>
                <Th align="right">Max DD %</Th>
                <Th align="right">Off 52w</Th>
                <Th align="right">RSI</Th>
                <Th align="right">Now?</Th>
              </tr>
            </thead>
            <tbody>
              {visibleRows.map((row) => {
                const key = `${row.symbol}-${row.strategy}`;
                const open = openRow === key;
                return (
                  <RowGroup
                    key={key}
                    row={row}
                    open={open}
                    onToggle={() => setOpenRow(open ? null : key)}
                  />
                );
              })}
            </tbody>
          </table>
          {top.length > 10 && (
            <div style={{ padding: 12, textAlign: "center", borderTop: "1px solid var(--border)" }}>
              <button onClick={() => setShowAll((v) => !v)}>
                {showAll ? `Show top 10` : `Show all ${top.length} rows`}
              </button>
            </div>
          )}
        </section>
      )}
    </div>
  );
}

function BestPickCard({
  row,
  rankMetric,
}: {
  row: CompareRow;
  rankMetric: string;
}) {
  const ms = row.market_state;
  const verdict = ms?.entry_signal ?? "HOLD";
  const verdictColour = signalColour(verdict);
  const metricValue = row.stats?.[rankMetric];
  return (
    <section
      className="card"
      style={{
        borderTop: `3px solid var(--up)`,
        paddingTop: 14,
        display: "flex",
        gap: 18,
        flexWrap: "wrap",
      }}
    >
      <div style={{ minWidth: 220 }}>
        <div className="stat-label">Top pick</div>
        <div style={{ fontSize: 22, fontWeight: 700, marginTop: 4 }}>
          <Link to={`/signals?symbol=${encodeURIComponent(row.symbol)}`} style={{ color: "var(--text)" }}>
            {row.symbol}
          </Link>
          <span style={{ color: "var(--text-dim)", fontWeight: 400, marginLeft: 8, fontSize: 14 }}>
            via {row.strategy_label}
          </span>
        </div>
        <div style={{ marginTop: 6, color: "var(--text-dim)", fontSize: 13 }}>
          {rankMetric}: <strong style={{ color: "var(--text)" }}>{fmtNum(metricValue)}</strong>
          {" · "}
          CAGR {fmtNum(row.stats?.cagr_pct)}%
          {" · "}
          max DD {fmtNum(row.stats?.max_drawdown_pct)}%
        </div>
      </div>
      <div
        style={{
          flex: 1,
          minWidth: 280,
          padding: "10px 14px",
          borderRadius: 8,
          background: "rgba(255,255,255,0.02)",
          borderLeft: `3px solid ${verdictColour}`,
        }}
      >
        <div className="stat-label">Now or wait?</div>
        <div style={{ fontSize: 18, fontWeight: 700, color: verdictColour, marginTop: 4 }}>
          {verdict}
        </div>
        <div style={{ marginTop: 4, color: "var(--text-dim)", fontSize: 13 }}>
          {ms?.entry_reason}
        </div>
      </div>
    </section>
  );
}

function RowGroup({
  row,
  open,
  onToggle,
}: {
  row: CompareRow;
  open: boolean;
  onToggle: () => void;
}) {
  const ms = row.market_state;
  const colour = signalColour(ms?.entry_signal);
  return (
    <>
      <tr
        style={{ cursor: "pointer", borderTop: "1px solid var(--border)" }}
        onClick={onToggle}
      >
        <Td>{row.rank}</Td>
        <Td><strong>{row.symbol}</strong></Td>
        <Td style={{ color: "var(--text-dim)" }}>{row.strategy_label}</Td>
        <Td align="right" className="num">{fmtNum(row.stats?.cagr_pct)}</Td>
        <Td align="right" className="num">{fmtNum(row.stats?.sharpe)}</Td>
        <Td align="right" className="num">{fmtNum(row.stats?.max_drawdown_pct)}</Td>
        <Td align="right" className="num">{fmtNum(ms?.pct_off_52w_high_pct)}</Td>
        <Td align="right" className="num">{fmtNum(ms?.rsi_14, 0)}</Td>
        <Td align="right" style={{ color: colour, fontWeight: 600 }}>
          {ms?.entry_signal ?? "—"}
        </Td>
      </tr>
      {open && (
        <tr style={{ background: "var(--bg-hover)" }}>
          <td colSpan={9} style={{ padding: "10px 16px", color: "var(--text-dim)", fontSize: 12 }}>
            <div style={{ marginBottom: 6 }}>
              <strong style={{ color: colour }}>{ms?.entry_signal}</strong> — {ms?.entry_reason}
            </div>
            {row.regimes.length > 0 ? (
              <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(160px, 1fr))", gap: 8 }}>
                {row.regimes.map((r) => (
                  <div key={r.key} style={{ borderLeft: `2px solid ${regimeColour(r.kind)}`, paddingLeft: 8 }}>
                    <div style={{ color: "var(--text)", fontSize: 12, fontWeight: 600 }}>{r.name}</div>
                    <div className="num">return {fmtNum(r.return_pct)}% · max DD {fmtNum(r.max_drawdown_pct)}%</div>
                  </div>
                ))}
              </div>
            ) : (
              <div>No historical regime overlap for this row.</div>
            )}
          </td>
        </tr>
      )}
    </>
  );
}

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

function Th({ children, align }: { children: React.ReactNode; align?: "left" | "right" }) {
  return (
    <th
      style={{
        padding: "10px 12px",
        fontWeight: 500,
        fontSize: 11,
        letterSpacing: "0.06em",
        textTransform: "uppercase",
        textAlign: align ?? "left",
      }}
    >
      {children}
    </th>
  );
}

function Td({
  children,
  align,
  style,
  className,
}: {
  children: React.ReactNode;
  align?: "left" | "right";
  style?: React.CSSProperties;
  className?: string;
}) {
  return (
    <td
      className={className}
      style={{ padding: "10px 12px", textAlign: align ?? "left", ...style }}
    >
      {children}
    </td>
  );
}

function signalColour(signal?: EntrySignal | null): string {
  switch (signal) {
    case "BUY": return "var(--up)";
    case "WAIT": return "var(--neutral)";
    case "AVOID": return "var(--down)";
    default: return "var(--text-dim)";
  }
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

function fmtAge(iso: string): string {
  const then = new Date(iso).getTime();
  const now = Date.now();
  const min = Math.max(1, Math.round((now - then) / 60000));
  if (min < 60) return `${min} min ago`;
  const hr = Math.round(min / 60);
  if (hr < 48) return `${hr} h ago`;
  const days = Math.round(hr / 24);
  return `${days} d ago`;
}

import { useEffect, useState } from "react";
import { config } from "../config";
import { getIdToken } from "../firebase";
import { PaperSubNav } from "../components/PaperSubNav";

/** Per-(symbol, strategy) cumulative outcomes across every completed
 * intraday session. Answers the user's "if I'd used strategy X on
 * symbol Y, would it have made money?" question directly.
 *
 * Data source: GET /api/ops/leaderboard — server-side rollup over
 * session_requests.result_summary JSONB. No client-side aggregation
 * (the SQL is the source of truth for this view). */

interface Cell {
  symbol: string;
  sessions: number;
  fills: number;
  realizedPnlUsd: number;
  lastSeenAtUtc: string | null;
}

interface StrategyRow {
  strategy: string;
  bySymbol: Cell[];
  totalSessions: number;
  totalFills: number;
  totalRealizedPnlUsd: number;
}

interface Payload {
  generatedAtUtc: string;
  sessionCount: number;
  lastSessionAtUtc: string | null;
  symbols: string[];
  strategies: StrategyRow[];
}

export function IntradayLeaderboard() {
  const [data, setData] = useState<Payload | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const token = await getIdToken();
        const headers: Record<string, string> = {};
        if (token) headers["authorization"] = `Bearer ${token}`;
        const resp = await fetch(
          new URL("/api/ops/leaderboard", config.apiBaseUrl).toString(),
          { headers },
        );
        if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
        const body = (await resp.json()) as Payload;
        if (!cancelled) {
          setData(body);
          setLoading(false);
        }
      } catch (e) {
        if (!cancelled) {
          setError(String(e));
          setLoading(false);
        }
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 18, maxWidth: 1100 }}>
      <PaperSubNav />
      <div>
        <h1 style={{ margin: 0, fontSize: 24 }}>Intraday strategy leaderboard</h1>
        <p style={{ color: "var(--text-dim)", margin: "6px 0 0 0", maxWidth: 760 }}>
          Per-strategy cumulative P&L across each watchlisted symbol, rolled up
          over every completed intraday session. Cells show realised P&L (USD),
          the number of fills the strategy generated, and how many sessions it
          ran against that symbol. Positive cells are green, negative red.
          <br />
          Zero-fill cells mean the strategy ran but didn't find an entry —
          ambient evidence that the strategy is "quiet" on that symbol.
        </p>
      </div>

      {loading && (
        <div className="card" style={{ padding: "12px 16px" }}>Loading leaderboard…</div>
      )}

      {error && (
        <div
          className="card"
          style={{
            borderColor: "var(--down)",
            color: "var(--down)",
            padding: "10px 14px",
          }}
        >
          {error}
        </div>
      )}

      {data && data.sessionCount === 0 && (
        <div
          className="card"
          style={{
            padding: "14px 16px",
            color: "var(--text-dim)",
            borderLeft: "3px solid var(--neutral)",
          }}
        >
          <strong style={{ color: "var(--text)" }}>No sessions yet.</strong>{" "}
          Queue an intraday run from the Mac (or the operations panel once
          it lands) and the leaderboard fills in as sessions complete.
          {" "}<a href="/settings" style={{ color: "var(--text)" }}>Configure watchlist + strategies →</a>
        </div>
      )}

      {/* Insufficient-data state. A 1×1 grid (or 1 strategy across 1 symbol)
          doesn't tell the trader anything useful — the leaderboard's value
          is comparison, not absolute numbers. Show an actionable empty-
          state instead, mention what's missing + how to grow the matrix. */}
      {data && data.sessionCount > 0 &&
       (data.symbols.length < 2 || data.strategies.length < 2) && (
        <div
          className="card"
          style={{
            padding: "14px 16px",
            color: "var(--text-dim)",
            borderLeft: "3px solid #f59e0b",
            background: "rgba(245,158,11,0.06)",
          }}
        >
          <strong style={{ color: "#f59e0b" }}>Not enough data to compare.</strong>{" "}
          The leaderboard needs ≥2 strategies AND ≥2 symbols to surface
          meaningful per-cell attribution — you currently have{" "}
          <strong style={{ color: "var(--text)" }}>{data.strategies.length}</strong>{" "}
          strateg{data.strategies.length === 1 ? "y" : "ies"} across{" "}
          <strong style={{ color: "var(--text)" }}>{data.symbols.length}</strong>{" "}
          symbol{data.symbols.length === 1 ? "" : "s"} from{" "}
          <strong style={{ color: "var(--text)" }}>{data.sessionCount}</strong>{" "}
          session{data.sessionCount === 1 ? "" : "s"}. Run more strategies
          and/or symbols (try the Trigger panel on{" "}
          <a href="/trader" style={{ color: "var(--text)" }}>/trader</a>) and
          the comparison surface will populate.
          {data.lastSessionAtUtc && (
            <>
              {" · "}Last session:{" "}
              <strong style={{ color: "var(--text)" }}>
                {new Date(data.lastSessionAtUtc).toLocaleString()}
              </strong>
            </>
          )}
        </div>
      )}

      {data && data.sessionCount > 0 &&
       data.symbols.length >= 2 && data.strategies.length >= 2 && (
        <>
          <div
            style={{
              display: "flex",
              gap: 24,
              fontSize: 12,
              color: "var(--text-dim)",
            }}
          >
            <span>
              <strong style={{ color: "var(--text)" }}>{data.sessionCount}</strong>{" "}
              sessions
            </span>
            <span>
              <strong style={{ color: "var(--text)" }}>{data.symbols.length}</strong>{" "}
              symbols
            </span>
            <span>
              <strong style={{ color: "var(--text)" }}>{data.strategies.length}</strong>{" "}
              strategies
            </span>
            {data.lastSessionAtUtc && (
              <span>
                Last session:{" "}
                <strong style={{ color: "var(--text)" }}>
                  {new Date(data.lastSessionAtUtc).toLocaleString()}
                </strong>
              </span>
            )}
          </div>

          <div
            className="card"
            style={{
              padding: 0,
              overflowX: "auto",
            }}
          >
            <table
              style={{
                borderCollapse: "collapse",
                width: "100%",
                fontSize: 12,
              }}
            >
              <thead>
                <tr>
                  <th
                    style={{
                      ...thStyle,
                      position: "sticky",
                      left: 0,
                      background: "var(--bg)",
                      zIndex: 2,
                      minWidth: 180,
                    }}
                  >
                    Strategy
                  </th>
                  {data.symbols.map((s) => (
                    <th key={s} style={thStyle}>
                      {s}
                    </th>
                  ))}
                  <th
                    style={{
                      ...thStyle,
                      background: "rgba(255,255,255,0.04)",
                      borderLeft: "1px solid var(--border)",
                    }}
                  >
                    Total
                  </th>
                </tr>
              </thead>
              <tbody>
                {data.strategies.map((row) => (
                  <tr key={row.strategy}>
                    <td
                      style={{
                        ...tdStyle,
                        position: "sticky",
                        left: 0,
                        background: "var(--bg)",
                        fontFamily: "ui-monospace, SFMono-Regular, Menlo, monospace",
                        fontWeight: 600,
                      }}
                    >
                      {row.strategy}
                    </td>
                    {row.bySymbol.map((cell) => (
                      <td
                        key={cell.symbol}
                        style={{
                          ...tdStyle,
                          color: cellColour(cell.realizedPnlUsd),
                          textAlign: "right",
                          fontVariantNumeric: "tabular-nums",
                        }}
                        title={
                          `${row.strategy} on ${cell.symbol}\n` +
                          `Realised P&L: $${cell.realizedPnlUsd.toFixed(2)}\n` +
                          `Fills: ${cell.fills}\n` +
                          `Sessions: ${cell.sessions}` +
                          (cell.lastSeenAtUtc
                            ? `\nLast seen: ${new Date(cell.lastSeenAtUtc).toLocaleString()}`
                            : "")
                        }
                      >
                        {fmtCell(cell)}
                      </td>
                    ))}
                    <td
                      style={{
                        ...tdStyle,
                        background: "rgba(255,255,255,0.04)",
                        borderLeft: "1px solid var(--border)",
                        textAlign: "right",
                        color: cellColour(row.totalRealizedPnlUsd),
                        fontVariantNumeric: "tabular-nums",
                        fontWeight: 600,
                      }}
                      title={
                        `${row.strategy} totals\n` +
                        `Realised P&L: $${row.totalRealizedPnlUsd.toFixed(2)}\n` +
                        `Fills: ${row.totalFills}\n` +
                        `Sessions: ${row.totalSessions}`
                      }
                    >
                      {fmtUsd(row.totalRealizedPnlUsd)}
                      <br />
                      <span style={{ fontWeight: 400, fontSize: 10, color: "var(--text-muted)" }}>
                        {row.totalFills} fills
                      </span>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          <div
            style={{
              fontSize: 11,
              color: "var(--text-muted)",
              maxWidth: 760,
            }}
          >
            <strong style={{ color: "var(--text-dim)" }}>Reading the cells:</strong>{" "}
            top number = realised P&L for that (strategy, symbol) pair across
            every session. Bottom number (smaller) = number of fills. Hover for
            session count + last seen. Empty cell means the strategy never ran
            on that symbol (toggle it on in Settings → Intraday → Strategies
            to start collecting data).
          </div>
        </>
      )}
    </div>
  );
}

const thStyle: React.CSSProperties = {
  padding: "10px 12px",
  fontSize: 11,
  fontWeight: 600,
  textAlign: "left",
  color: "var(--text-dim)",
  borderBottom: "1px solid var(--border)",
  whiteSpace: "nowrap",
};

const tdStyle: React.CSSProperties = {
  padding: "8px 12px",
  borderBottom: "1px solid var(--border)",
  whiteSpace: "nowrap",
};

function cellColour(pnl: number): string {
  if (pnl > 0.005) return "var(--up)";
  if (pnl < -0.005) return "var(--down)";
  return "var(--text-muted)";
}

function fmtUsd(n: number): string {
  if (Math.abs(n) < 0.005) return "$0.00";
  const sign = n >= 0 ? "" : "-";
  return `${sign}$${Math.abs(n).toFixed(2)}`;
}

function fmtCell(cell: Cell): JSX.Element {
  if (cell.sessions === 0) {
    return <span style={{ color: "var(--text-muted)" }}>—</span>;
  }
  return (
    <>
      {fmtUsd(cell.realizedPnlUsd)}
      <br />
      <span style={{ fontWeight: 400, fontSize: 10, color: "var(--text-muted)" }}>
        {cell.fills} fill{cell.fills === 1 ? "" : "s"}
      </span>
    </>
  );
}

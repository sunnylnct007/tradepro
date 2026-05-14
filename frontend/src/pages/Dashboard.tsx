import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api/client";
import type {
  CompareLatestResponse,
  CompareRow,
  EntrySignal,
} from "../api/types";
import { HorizonPills } from "../components/HorizonPills";

/** Local T212 position shape — mirrors what /api/integrations/trading212/positions
 * actually returns (see Portfolio.tsx for the full schema). Defined inline
 * rather than imported because the dashboard only reads a handful of fields
 * and shouldn't widen the public api/types surface. */
interface T212Position {
  ticker: string | null;
  quantity: number;
  averagePricePaid: number | null;
  currentPrice: number | null;
  /** Absolute unrealised P&L in account currency. Server-computed
   * so the dashboard doesn't have to recompute from price+qty. */
  unrealisedAbs: number | null;
}

export function Dashboard() {
  const [compareData, setCompareData] = useState<CompareLatestResponse | null>(null);
  const [universe, setUniverse] = useState<string>("");
  const [positions, setPositions] = useState<T212Position[]>([]);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    // Resolve a default universe before fetching compare data — when
    // the user has only pushed etf_us_core so far, that's what loads.
    api.compareUniverses()
      .then(r => {
        if (r.universes.length > 0 && !universe) {
          setUniverse(r.universes[0].universe);
        }
      })
      .catch(() => {});

    api.compareLatest(universe)
      .then(r => setCompareData(r))
      .catch(() => setError("Failed to load market data. Please check the worker status."));
  }, [universe]);

  // T212 positions via the same-origin api client so the call goes
  // through Caddy → nginx → api on AWS (the old localhost:5080 URL
  // worked locally but 404'd in production).
  useEffect(() => {
    fetch("/api/integrations/trading212/positions")
      .then(r => r.ok ? r.json() : null)
      .then(data => {
        if (data?.enabled) setPositions(data.positions ?? []);
      })
      .catch(() => {});
  }, []);

  // Filter by SERVER bucket, not raw entry_signal. Same lesson as
  // Compare.tsx (Bug #11/TSLA): entry_signal is the pre-demotion price
  // verdict and ignores horizon/sentiment demotions. The server-side
  // bucket is the final answer; the dashboard's BUY/WAIT/AVOID cards
  // must use it or they'll feature symbols the rest of the app says
  // WAIT on. Fallback to entry_signal only when bucket is absent
  // (older payloads).
  const pickBucket = (row: CompareRow): "BUY" | "WAIT" | "AVOID" | null => {
    if (row.bucket) return row.bucket;
    if (!row.market_state) return null;
    const sig = row.market_state.entry_signal as EntrySignal;
    if (sig === "BUY" || sig === "WAIT" || sig === "AVOID") return sig;
    return null;
  };

  const buys = compareData?.payload?.rows?.filter(row => pickBucket(row) === "BUY")
    .sort((a, b) => (a.rank ?? 1e9) - (b.rank ?? 1e9)).slice(0, 5) ?? [];

  const waits = compareData?.payload?.rows?.filter(row => pickBucket(row) === "WAIT")
    .sort((a, b) => (a.rank ?? 1e9) - (b.rank ?? 1e9)).slice(0, 5) ?? [];

  const avoids = compareData?.payload?.rows?.filter(row => pickBucket(row) === "AVOID")
    .sort((a, b) => (a.rank ?? 1e9) - (b.rank ?? 1e9)).slice(0, 5) ?? [];

  const buyCount = buys.length;
  const waitCount = waits.length;
  const avoidCount = avoids.length;

  // Calculate portfolio P&L
  const totalPnL = positions.reduce((sum, p) => {
    return sum + (p.unrealisedAbs ?? 0);
  }, 0);
  const totalPnLPct = positions.length > 0 && (positions[0].averagePricePaid ?? 0) > 0
    ? positions.reduce((sum, p) => {
        const avg = p.averagePricePaid ?? 0;
        const cur = p.currentPrice ?? 0;
        return sum + ((cur - avg) / avg) * p.quantity;
      }, 0) / positions.reduce((sum, p) => sum + p.quantity, 0)
    : 0;

  const pnlTone = totalPnL > 0 ? "up" : totalPnL < 0 ? "down" : undefined;

  const marketContext = compareData?.payload?.market_context;
  const rankMetric = compareData?.rankMetric ?? compareData?.payload?.rank_metric ?? "sharpe";

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 24, maxWidth: 1400, margin: "0 auto" }}>
      {/* Header with verdict */}
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", flexWrap: "wrap", gap: 12 }}>
        <div>
          <div style={{ display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap" }}>
            <h1 style={{ margin: 0, fontSize: 28 }}>Today's Market Snapshot</h1>
            {/* Horizon pill: every verdict on this page is daily-bar
                medium-to-long-term reasoning, not intraday. */}
            <span
              title="These signals are computed on daily bars across multi-year history. They are NOT intraday or day-trading calls."
              style={{
                fontSize: 11,
                fontWeight: 600,
                padding: "3px 10px",
                borderRadius: 999,
                background: "rgba(155, 110, 255, 0.14)",
                color: "#cbb6ff",
                border: "1px solid rgba(155, 110, 255, 0.35)",
                whiteSpace: "nowrap",
              }}
            >
              HORIZON · MEDIUM TO LONG
            </span>
          </div>
          <p style={{ color: "var(--text-dim)", marginTop: 4 }}>
            {buyCount} BUY · {waitCount} WAIT · {avoidCount} AVOID — for weeks-to-years horizons; not intraday.
          </p>
          <p style={{ color: "var(--text-muted)", fontSize: 13, marginTop: 4 }}>
            Refreshes every 30 minutes · {marketContext ? `VIX: ${marketContext.vix?.toFixed(1) ?? "—"} · 10Y: ${marketContext.tnx?.toFixed(2) ?? "—"}%` : "Loading market context..."}
          </p>
        </div>
        <div style={{ display: "flex", gap: 12, alignItems: "center" }}>
          <Link to="/compare" style={{ color: "var(--accent)", textDecoration: "none" }}>
            View all signals →
          </Link>
          <span style={{ color: "var(--text-dim)", fontSize: 11 }}>
            Universe: {universe ?? "—"}
          </span>
        </div>
      </div>

      {error && (
        <div
          className="card"
          style={{ borderColor: "var(--down)", background: "var(--down-soft)" }}
        >
          <div style={{ color: "var(--down)", fontWeight: 600 }}>Error</div>
          <div style={{ fontSize: 13 }}>{error}</div>
        </div>
      )}

      {/* Portfolio Summary */}
      {positions.length > 0 && (
        <section className="card" style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(200px, 1fr))", gap: 16 }}>
          <div style={{ gridColumn: "1 / -1" }}>
            <h3 style={{ margin: "0 0 12px 0", fontSize: 16 }}>Your Trading 212 Portfolio</h3>
            <p style={{ color: "var(--text-dim)", fontSize: 13 }}>
              {positions.length} position{positions.length === 1 ? "" : "s"} · GBP
            </p>
          </div>
          <div>
            <div className="stat-label">Positions</div>
            <div className="stat-value" style={{ fontWeight: 700 }}>{positions.length}</div>
          </div>
          <div>
            <div className="stat-label">Total Unrealised</div>
            <div className={`stat-value ${pnlTone}`} style={{ fontWeight: 700 }}>
              {totalPnL >= 0 ? "+" : ""}{totalPnL.toFixed(2)} GBP
            </div>
            {totalPnLPct !== 0 && (
              <div style={{ fontSize: 11, color: "var(--text-dim)", marginTop: 2 }}>
                ({totalPnLPct >= 0 ? "+" : ""}{totalPnLPct.toFixed(2)}%)
              </div>
            )}
          </div>
          <div>
            <div className="stat-label">Total Value</div>
            <div className="stat-value" style={{ fontWeight: 600 }}>
              {positions.reduce((sum, p) => sum + (p.currentPrice ?? 0) * p.quantity, 0).toFixed(2)} GBP
            </div>
          </div>
        </section>
      )}

      {/* Top BUY Picks */}
      {buyCount > 0 && (
        <section className="card">
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 12 }}>
            <h3 style={{ margin: 0, fontSize: 18 }}>Top {buyCount} BUY Pick{buyCount === 1 ? "" : "s"} Today</h3>
            <span className="stat-label" style={{ color: "var(--up)" }}>
              {buyCount} strong buys
            </span>
          </div>
          <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(280px, 1fr))", gap: 12 }}>
            {buys.map((pick, idx) => {
              // `pick` is already one CompareRow (one symbol × strategy
              // combo), not a SymbolView aggregation — there's no nested
              // `rows` array to index, and the user-facing label IS the
              // symbol. Strip awkward chars (^ in index tickers, dot
              // suffixes) for the headline.
              const labelSafe = pick.symbol.replace(/[^\w\s-]/g, "");
              const row = pick;
              return (
                <Link
                  key={idx}
                  to={`/signals?symbol=${encodeURIComponent(pick.symbol)}`}
                  style={{
                    display: "block",
                    padding: "16px",
                    background: "rgba(31, 193, 107, 0.04)",
                    border: "2px solid var(--up)",
                    borderRadius: 8,
                    textDecoration: "none",
                    transition: "all 0.2s",
                  }}
                  onMouseEnter={(e) => {
                    e.currentTarget.style.background = "rgba(31, 193, 107, 0.08)";
                    e.currentTarget.style.transform = "translateY(-2px)";
                  }}
                  onMouseLeave={(e) => {
                    e.currentTarget.style.background = "rgba(31, 193, 107, 0.04)";
                    e.currentTarget.style.transform = "translateY(0)";
                  }}
                >
                  <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start" }}>
                    <div>
                      <div style={{ fontSize: 20, fontWeight: 700, marginBottom: 4 }}>{labelSafe}</div>
                      <div className="stat-label" style={{ fontSize: 11, color: "var(--text-dim)" }}>
                        {row?.symbol ?? pick.symbol} · {row?.strategy_label ?? "—"}
                      </div>
                    </div>
                    <div style={{ display: "flex", flexDirection: "column", alignItems: "flex-end" }}>
                      <div className="stat-value" style={{ color: "var(--up)" }}>BUY</div>
                      <div className="stat-label" style={{ fontSize: 10 }}>
                        {row?.stats?.[rankMetric] != null && `Rank: ${Math.round(row.stats[rankMetric] ?? 0)}`}
                      </div>
                    </div>
                  </div>
                  {row && (
                    <div style={{ marginTop: 8, fontSize: 12, color: "var(--text-dim)", lineHeight: 1.5 }}>
                      {/* market_state.entry_reason is the canonical
                          one-line "why" for the verdict — short, already
                          curated, no need for a second reasons array. */}
                      <div style={{ marginTop: 4 }}>
                        {row.market_state?.entry_reason ?? "—"}
                      </div>
                      {/* Three-pill horizon breakdown so the user can
                          tell a "swing BUY" from a "long-term BUY" at
                          a glance without clicking through. */}
                      {row.horizon_classification && (
                        <div style={{ marginTop: 8 }}>
                          <HorizonPills classification={row.horizon_classification} />
                        </div>
                      )}
                    </div>
                  )}
                </Link>
              );
            })}
          </div>
        </section>
      )}

      {/* WAIT List */}
      {waitCount > 0 && (
        <section className="card">
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 12 }}>
            <h3 style={{ margin: 0, fontSize: 18 }}>WAIT List - Better Entries Coming</h3>
            <span className="stat-label" style={{ color: "var(--neutral)" }}>
              {waitCount} waiting
            </span>
          </div>
          <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(280px, 1fr))", gap: 12 }}>
            {waits.map((pick, idx) => {
              // `pick` is already one CompareRow (one symbol × strategy
              // combo), not a SymbolView aggregation — there's no nested
              // `rows` array to index, and the user-facing label IS the
              // symbol. Strip awkward chars (^ in index tickers, dot
              // suffixes) for the headline.
              const labelSafe = pick.symbol.replace(/[^\w\s-]/g, "");
              const row = pick;
              return (
                <Link
                  key={idx}
                  to={`/signals?symbol=${encodeURIComponent(pick.symbol)}`}
                  style={{
                    display: "block",
                    padding: "16px",
                    background: "rgba(180, 180, 180, 0.04)",
                    border: "2px solid var(--neutral)",
                    borderRadius: 8,
                    textDecoration: "none",
                  }}
                >
                  <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start" }}>
                    <div>
                      <div style={{ fontSize: 20, fontWeight: 700 }}>{labelSafe}</div>
                      <div className="stat-label" style={{ fontSize: 11, color: "var(--text-dim)" }}>
                        {row?.symbol ?? pick.symbol} · {row?.strategy_label ?? "—"}
                      </div>
                    </div>
                    <div style={{ display: "flex", flexDirection: "column", alignItems: "flex-end" }}>
                      <div className="stat-value" style={{ color: "var(--neutral)" }}>WAIT</div>
                    </div>
                  </div>
                  {row && (
                    <div style={{ marginTop: 8, fontSize: 12, color: "var(--text-dim)", lineHeight: 1.5 }}>
                      {row.market_state?.entry_reason ?? "—"}
                      {row.horizon_classification && (
                        <div style={{ marginTop: 8 }}>
                          <HorizonPills classification={row.horizon_classification} />
                        </div>
                      )}
                    </div>
                  )}
                </Link>
              );
            })}
          </div>
        </section>
      )}

      {/* AVOID List */}
      {avoidCount > 0 && (
        <section className="card">
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 12 }}>
            <h3 style={{ margin: 0, fontSize: 18 }}>AVOID - Stay Flat</h3>
            <span className="stat-label" style={{ color: "var(--down)" }}>
              {avoidCount} avoid
            </span>
          </div>
          <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(280px, 1fr))", gap: 12 }}>
            {avoids.map((pick, idx) => {
              // `pick` is already one CompareRow (one symbol × strategy
              // combo), not a SymbolView aggregation — there's no nested
              // `rows` array to index, and the user-facing label IS the
              // symbol. Strip awkward chars (^ in index tickers, dot
              // suffixes) for the headline.
              const labelSafe = pick.symbol.replace(/[^\w\s-]/g, "");
              const row = pick;
              return (
                <Link
                  key={idx}
                  to={`/signals?symbol=${encodeURIComponent(pick.symbol)}`}
                  style={{
                    display: "block",
                    padding: "16px",
                    background: "rgba(255, 59, 48, 0.04)",
                    border: "2px solid var(--down)",
                    borderRadius: 8,
                    textDecoration: "none",
                  }}
                >
                  <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start" }}>
                    <div>
                      <div style={{ fontSize: 20, fontWeight: 700, color: "var(--down)" }}>{labelSafe}</div>
                      <div className="stat-label" style={{ fontSize: 11, color: "var(--text-dim)" }}>
                        {row?.symbol ?? pick.symbol} · {row?.strategy_label ?? "—"}
                      </div>
                    </div>
                    <div style={{ display: "flex", flexDirection: "column", alignItems: "flex-end" }}>
                      <div className="stat-value" style={{ color: "var(--down)" }}>AVOID</div>
                    </div>
                  </div>
                  {row && (
                    <div style={{ marginTop: 8, fontSize: 12, color: "var(--text-dim)", lineHeight: 1.5 }}>
                      {row.market_state?.entry_reason ?? "—"}
                      {row.horizon_classification && (
                        <div style={{ marginTop: 8 }}>
                          <HorizonPills classification={row.horizon_classification} />
                        </div>
                      )}
                    </div>
                  )}
                </Link>
              );
            })}
          </div>
        </section>
      )}

      {/* Quick Actions */}
      <section className="card">
        <h3 style={{ margin: "0 0 12px 0", fontSize: 16 }}>Quick Actions</h3>
        <div style={{ display: "flex", gap: 12, flexWrap: "wrap" }}>
          <button
            onClick={() => document.location.href = `/scanner`}
            style={{
              padding: "10px 16px",
              background: "var(--accent-soft)",
              color: "var(--accent)",
              border: "none",
              borderRadius: 6,
              cursor: "pointer",
              fontWeight: 600,
            }}
          >
            📊 Scan new universe
          </button>
          <button
            onClick={() => document.location.href = `/portfolio`}
            style={{
              padding: "10px 16px",
              background: "var(--bg-hover)",
              color: "var(--text)",
              border: "1px solid var(--border)",
              borderRadius: 6,
              cursor: "pointer",
              fontWeight: 600,
            }}
          >
            👜 View portfolio
          </button>
          <button
            onClick={() => document.location.href = `/settings`}
            style={{
              padding: "10px 16px",
              background: "var(--bg-hover)",
              color: "var(--text)",
              border: "1px solid var(--border)",
              borderRadius: 6,
              cursor: "pointer",
              fontWeight: 600,
            }}
          >
            ⚙️ Settings
          </button>
          {positions.length > 0 && (
            <button
              onClick={() => {
                window.open(`https://www.trading212.com/account`, "_blank");
              }}
              style={{
                padding: "10px 16px",
                background: "var(--bg-hover)",
                color: "var(--text)",
                border: "1px solid var(--border)",
                borderRadius: 6,
                cursor: "pointer",
                fontWeight: 600,
              }}
            >
              🏛️ Trading 212
            </button>
          )}
        </div>
      </section>
    </div>
  );
}

/**
 * SymbolDecisionCard — WHY is this symbol held / what does the system think?
 *
 * One job: show the compare engine's current verdict for the selected symbol.
 * Sourced from compareLatest(universe).payload.rows, filtering by row.symbol
 * matching the selected Yahoo symbol.
 *
 * Shows:
 *   - current_action (BUY/HOLD/SELL) + bucket + bucket_reason
 *   - market_state key indicators (trend vs SMA200, RSI, momentum, last-vs-SMA200)
 *   - combined_verdict (fused technical + catalyst + analyst)
 *   - top 1-2 news/catalysts
 *   "No signal data" when the symbol isn't in the compare results.
 *
 * Universe is resolved via compareUniverses() (picks the first available), then
 * compareLatest(name). Both fetch once per symbol selection; no background poll.
 */
import { useEffect, useState } from "react";
import { api } from "../../api/client";
import type { CompareRow } from "../../api/types";
import type { PositionRow } from "./SymbolPositionCard";

type Status = "idle" | "loading" | "ok" | "error" | "nodata";

function actionColour(a: string | null | undefined): string {
  if (!a) return "var(--text-muted)";
  if (a === "BUY")  return "#1fc16b";
  if (a === "SELL") return "#ef4444";
  return "var(--text-muted)";
}

function bucketColour(b: string | null | undefined): string {
  if (b === "BUY")   return "#1fc16b";
  if (b === "AVOID") return "#ef4444";
  return "#f59e0b";
}

function fmt2(n: number | null | undefined, suffix = ""): string {
  if (n == null) return "n/a";
  return `${n.toFixed(2)}${suffix}`;
}

export function SymbolDecisionCard({ symbol, positions }: { symbol: string; positions?: PositionRow[] }) {
  const held = (positions ?? []).find(
    (r) => r.chartSymbol === symbol || r.ticker === symbol,
  ) ?? null;
  const [status, setStatus] = useState<Status>("idle");
  const [row, setRow] = useState<CompareRow | null>(null);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    if (!symbol) return;
    let live = true;
    setStatus("loading");
    setRow(null);
    setErr(null);

    const load = async () => {
      try {
        // Resolve the default universe (first available).
        const univResp = await api.compareUniverses();
        const universes = univResp.universes ?? [];
        if (universes.length === 0) {
          if (live) { setStatus("nodata"); }
          return;
        }
        const universe = universes[0].universe;
        const latest = await api.compareLatest(universe);
        if (!live) return;
        const rows = latest.payload?.rows ?? [];
        // Match by exact symbol or bare-ticker match (strip trailing class e.g. ".L").
        const bare = symbol.replace(/\.[A-Z]+$/, "").toUpperCase();
        const found =
          rows.find((r) => r.symbol.toUpperCase() === symbol.toUpperCase()) ??
          rows.find((r) => r.symbol.toUpperCase() === bare);
        if (!found) {
          setStatus("nodata");
          return;
        }
        setRow(found);
        setStatus("ok");
      } catch (e) {
        if (live) {
          setErr(e instanceof Error ? e.message : String(e));
          setStatus("error");
        }
      }
    };
    void load();
    return () => { live = false; };
  }, [symbol]);

  return (
    <div
      style={{
        border: "1px solid #1b2233",
        borderRadius: 6,
        background: "rgba(255,255,255,0.015)",
        padding: "10px 12px",
      }}
    >
      <div
        style={{
          fontSize: 10,
          color: "var(--text-muted)",
          textTransform: "uppercase",
          letterSpacing: "0.05em",
          marginBottom: 8,
          fontWeight: 600,
        }}
      >
        Signal / Why
      </div>

      {status === "loading" && (
        <div style={{ fontSize: 11, color: "var(--text-muted)" }}>Loading signal…</div>
      )}
      {status === "error" && (
        <div style={{ fontSize: 11, color: "var(--down, #ef4444)" }}>
          Signal unavailable: {err}
        </div>
      )}
      {status === "nodata" && (
        <div style={{ fontSize: 11, color: "var(--text-muted)", fontStyle: "italic" }}>
          No signal data for {symbol} in compare universe.
        </div>
      )}
      {status === "ok" && row && <DecisionDetail row={row} held={held} />}
    </div>
  );
}

function DecisionDetail({ row, held }: { row: CompareRow; held: PositionRow | null }) {
  const ms = row.market_state;
  const cv = row.combined_verdict;

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
      {/* Action + bucket */}
      <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
        <span
          style={{
            fontSize: 15,
            fontWeight: 800,
            color: actionColour(row.current_action),
            fontFamily: "monospace",
          }}
        >
          {row.current_action ?? "—"}
        </span>
        {row.bucket && (
          <span
            style={{
              fontSize: 10, fontWeight: 700,
              color: bucketColour(row.bucket),
              border: `1px solid ${bucketColour(row.bucket)}`,
              borderRadius: 4, padding: "0 5px",
            }}
          >
            {row.bucket}
          </span>
        )}
        {row.strategy_label && (
          <span style={{ fontSize: 10, color: "var(--text-muted)" }}>{row.strategy_label}</span>
        )}
      </div>

      {/* Bucket reason */}
      {row.bucket_reason && (
        <div style={{ fontSize: 11, color: "var(--text-muted)", lineHeight: 1.4 }}>
          {row.bucket_reason}
        </div>
      )}

      {/* Your position vs the verdict — reconciles "trend up but I'm down". */}
      {held && held.qty != null && held.qty !== 0 && (
        <PositionRead row={row} held={held} />
      )}

      {/* Market-state indicators */}
      {ms && (
        <div style={{ display: "flex", flexDirection: "column", gap: 3 }}>
          <MiniStat label="vs SMA200">
            <span style={{ color: ms.above_sma_200 ? "#1fc16b" : "#ef4444" }}>
              {ms.above_sma_200 == null ? "n/a" : ms.above_sma_200 ? "Above" : "Below"}
            </span>
          </MiniStat>
          <MiniStat label="RSI(14)">{fmt2(ms.rsi_14)}</MiniStat>
          <MiniStat label="Mom 3m">{fmt2(ms.momentum_3m_pct, "%")}</MiniStat>
          <MiniStat label="Vol 30d">{fmt2(ms.vol_30d_annual_pct, "%")}</MiniStat>
          {ms.ichimoku_cloud_position && (
            <MiniStat label="Cloud">{ms.ichimoku_cloud_position}</MiniStat>
          )}
        </div>
      )}

      {/* Combined verdict */}
      {cv && (
        <div
          style={{
            borderTop: "1px solid #1b2233",
            paddingTop: 7,
            fontSize: 11,
            color: "var(--text)",
            lineHeight: 1.5,
          }}
        >
          <span style={{ fontWeight: 700, color: "var(--accent, #4f8cff)" }}>
            {cv.combined ?? "—"}
          </span>
          {cv.reasoning && cv.reasoning.length > 0 && (
            <ul style={{ margin: "4px 0 0 14px", padding: 0, fontSize: 10, color: "var(--text-muted)" }}>
              {cv.reasoning.slice(0, 3).map((r, i) => (
                <li key={i}>{r}</li>
              ))}
            </ul>
          )}
        </div>
      )}

      {/* Top catalysts */}
      {row.catalysts && row.catalysts.length > 0 && (
        <div style={{ borderTop: "1px solid #1b2233", paddingTop: 7 }}>
          <div style={{ fontSize: 10, color: "var(--text-muted)", marginBottom: 4, fontWeight: 600 }}>
            CATALYSTS
          </div>
          {row.catalysts.slice(0, 2).map((c, i) => (
            <div
              key={i}
              style={{
                fontSize: 10, color: "var(--text-muted)", marginBottom: 3,
                lineHeight: 1.4,
              }}
            >
              <span
                style={{
                  fontSize: 9, fontWeight: 700,
                  border: "1px solid #1b2233", borderRadius: 3, padding: "0 3px",
                  marginRight: 4, color: "var(--text-dim)",
                }}
              >
                {c.kind}
              </span>
              {c.title}
              {c.occurs_on && (
                <span style={{ marginLeft: 4, color: "#f59e0b" }}>· {c.occurs_on}</span>
              )}
            </div>
          ))}
        </div>
      )}

      {/* Top news */}
      {!row.catalysts?.length && row.news && row.news.length > 0 && (
        <div style={{ borderTop: "1px solid #1b2233", paddingTop: 7 }}>
          <div style={{ fontSize: 10, color: "var(--text-muted)", marginBottom: 4, fontWeight: 600 }}>
            RECENT NEWS
          </div>
          {[...row.news]
            .sort((a, b) => newsTs(b.published_at) - newsTs(a.published_at))
            .slice(0, 2).map((n, i) => (
            <div key={i} style={{ fontSize: 10, color: "var(--text-muted)", marginBottom: 3, lineHeight: 1.4 }}>
              {n.link ? (
                <a
                  href={n.link}
                  target="_blank"
                  rel="noopener noreferrer"
                  style={{ color: "var(--text-muted)", textDecoration: "underline", textUnderlineOffset: 2 }}
                >
                  {n.title}
                </a>
              ) : n.title}
            </div>
          ))}
        </div>
      )}

      <div style={{ fontSize: 9, color: "var(--text-dim)", marginTop: 2 }}>
        From compare universe · signal as of last run
      </div>
    </div>
  );
}

/** Reconciles the held position against the verdict so "30-day trend is up but
 *  I'm at a loss" explains itself: entry→now, an ATR-based reference stop, and a
 *  plain-English read of P&L vs trend vs the strategy's current call. */
function PositionRead({ row, held }: { row: CompareRow; held: PositionRow }) {
  const ms = row.market_state;
  const entry = held.avgPrice ?? null;
  const now = held.last ?? ms?.last_price ?? null;
  const pnlPct = held.pnlPct ??
    (entry != null && now != null && entry > 0 ? ((now - entry) / entry) * 100 : null);
  const action = row.current_action;          // BUY / HOLD / SELL
  const trendUp = ms?.above_sma_200 ?? null;  // long-term trend
  const atr = ms?.atr_14 ?? null;
  // Reference protective stop: 2×ATR below the current price (long book). This is
  // a generic reference, NOT the strategy's own stop (that isn't in compare yet).
  const stop = now != null && atr != null ? now - 2 * atr : null;
  const stopPct = stop != null && now != null && now > 0 ? ((now - stop) / now) * 100 : null;

  const down = pnlPct != null && pnlPct < 0;
  const absPnl = pnlPct != null ? Math.abs(pnlPct).toFixed(1) : "?";
  let tone: string, msg: string;
  if (down && (action === "BUY" || action === "HOLD") && trendUp !== false) {
    tone = "#f59e0b";
    msg = `Down ${absPnl}% but the trend is up and the strategy still says ${action} — you entered into strength, so this is a normal pullback, not a broken thesis. Hold unless price loses the stop.`;
  } else if (down && (action === "SELL" || trendUp === false)) {
    tone = "#ef4444";
    msg = `Down ${absPnl}% AND ${action === "SELL" ? "the strategy now says SELL" : "price is below its long-term trend"} — the thesis is weakening. The stop is your line in the sand.`;
  } else if (!down && pnlPct != null) {
    tone = "#1fc16b";
    msg = `In profit (+${absPnl}%) and the strategy says ${action} — riding the trend.`;
  } else {
    tone = "var(--text-muted)";
    msg = `Strategy currently says ${action}.`;
  }

  return (
    <div style={{ borderTop: "1px solid #1b2233", paddingTop: 7, display: "flex", flexDirection: "column", gap: 4 }}>
      <div style={{ fontSize: 10, color: "var(--text-muted)", textTransform: "uppercase", letterSpacing: "0.05em", fontWeight: 600 }}>
        Your position vs verdict
      </div>
      {entry != null && now != null && (
        <MiniStat label="Entry → now">
          <span>
            {entry.toFixed(2)} → {now.toFixed(2)}{" "}
            <span style={{ color: (pnlPct ?? 0) >= 0 ? "#1fc16b" : "#ef4444" }}>
              ({(pnlPct ?? 0) >= 0 ? "+" : ""}{(pnlPct ?? 0).toFixed(1)}%)
            </span>
          </span>
        </MiniStat>
      )}
      {stop != null && (
        <MiniStat label="Ref stop (2×ATR)">
          <span>
            {stop.toFixed(2)}{" "}
            <span style={{ color: "var(--text-muted)" }}>({stopPct?.toFixed(1)}% below)</span>
          </span>
        </MiniStat>
      )}
      <div style={{ fontSize: 11, color: tone, lineHeight: 1.45, marginTop: 2 }}>{msg}</div>
    </div>
  );
}

/** Parse a news item's published_at to epoch ms; null/unparseable → 0
 *  so undated items sink to the bottom of a newest-first sort. */
function newsTs(iso: string | null | undefined): number {
  if (!iso) return 0;
  const t = new Date(iso).getTime();
  return Number.isFinite(t) ? t : 0;
}

function MiniStat({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div style={{ display: "flex", justifyContent: "space-between", fontSize: 11 }}>
      <span style={{ color: "var(--text-muted)" }}>{label}</span>
      <span style={{ fontFamily: "monospace" }}>{children}</span>
    </div>
  );
}

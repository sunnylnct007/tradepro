import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { api } from "../api/client";
import type { CompareLatestResponse, CompareRow } from "../api/types";

/**
 * Symbol Deep Dive — single page that answers "Should I buy {ticker}?"
 * by stitching together every relevant data source into one linear
 * scroll. Replaces the multi-tab Compare workflow for the specific
 * "I want everything about ONE symbol" question.
 *
 * Spec: strategies/docs/tradepro_claude.pdf (v0.1). Ten sections:
 *   1. Header — symbol identity + price + 52w range + key risk metrics
 *   2. Verdict — BUY/WAIT/AVOID + reason + vote fraction (4/7)
 *   3. Decision trace — pass/fail/warn rules that fed the verdict
 *   4. Strategy vote — per-strategy detail with CONFLICT counter (the moat)
 *   5. News + sentiment
 *   6. Analyst consensus
 *   7. Event risk (earnings countdown)
 *   8. Regime survival
 *   9. Peer comparison
 *  10. Hit rate
 *
 * MVP scope (this commit): page shell + Section 1 (Header) working off
 * cached compare data. Sections 2–10 ship as labelled skeletons so the
 * layout is real but the content is incremental. The on-demand
 * "symbol not in any cached universe" path needs a new backend endpoint
 * (task #66) — for now we surface a clear empty state.
 */
export function SymbolDeepDive() {
  const { ticker } = useParams<{ ticker: string }>();
  const symbol = (ticker || "").toUpperCase();
  const [row, setRow] = useState<CompareRow | null>(null);
  const [universe, setUniverse] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [notInCache, setNotInCache] = useState(false);

  useEffect(() => {
    if (!symbol) return;
    let cancelled = false;
    setLoading(true);
    setError(null);
    setNotInCache(false);

    // MVP search: walk the universe list, pick the first one carrying
    // this symbol, lift the best row (highest Sharpe). Slow per request
    // (one /api/compare/universes call + up to N /api/compare/latest
    // calls) but acceptable for the first cut. The replacement endpoint
    // `GET /api/symbol/{ticker}` lands with task #66.
    (async () => {
      try {
        const { universes } = await api.compareUniverses();
        for (const u of universes) {
          if (cancelled) return;
          const resp: CompareLatestResponse = await api.compareLatest(u.universe);
          const rows = resp.payload?.rows ?? [];
          const match = rows.filter((r) => r.symbol === symbol);
          if (match.length > 0) {
            // Highest-Sharpe row for this symbol = the canonical view.
            const sharpeOf = (r: CompareRow) =>
              (r.stats?.sharpe as number | null | undefined) ?? -Infinity;
            match.sort((a, b) => sharpeOf(b) - sharpeOf(a));
            if (!cancelled) {
              setRow(match[0]);
              setUniverse(u.universe);
              setLoading(false);
            }
            return;
          }
        }
        if (!cancelled) {
          setNotInCache(true);
          setLoading(false);
        }
      } catch (e) {
        if (!cancelled) {
          setError(String(e));
          setLoading(false);
        }
      }
    })();
    return () => { cancelled = true; };
  }, [symbol]);

  // ---- early returns -----------------------------------------------
  if (!symbol) {
    return (
      <div style={{ maxWidth: 960, margin: "32px auto", padding: 20 }}>
        <h2>Symbol required</h2>
        <p>
          Visit <code>/symbol/BABA</code> (or any ticker) to land here.
          Linkable from Decide / Portfolio rows in a follow-up commit.
        </p>
      </div>
    );
  }
  if (loading) {
    return <PageShell symbol={symbol} state="loading" />;
  }
  if (error) {
    return <PageShell symbol={symbol} state="error" detail={error} />;
  }
  if (notInCache) {
    return <PageShell symbol={symbol} state="not-in-cache" />;
  }
  if (!row) {
    return <PageShell symbol={symbol} state="empty" />;
  }
  return (
    <PageShell symbol={symbol} state="ready" row={row} universe={universe} />
  );
}

// ----------------------------------------------------------------------
// PageShell — the linear 10-section layout. State controls whether we
// render the real Section 1 or a skeleton.
// ----------------------------------------------------------------------

type ShellState = "loading" | "error" | "not-in-cache" | "empty" | "ready";
function PageShell(props: {
  symbol: string;
  state: ShellState;
  row?: CompareRow | null;
  universe?: string | null;
  detail?: string;
}) {
  const { symbol, state, row, universe, detail } = props;
  return (
    <div style={{
      maxWidth: 960,
      margin: "32px auto",
      padding: "0 20px",
      display: "flex",
      flexDirection: "column",
      gap: 16,
    }}>
      <header style={{ display: "flex", alignItems: "baseline", gap: 12 }}>
        <Link to="/compare" style={{ fontSize: 12, color: "var(--text-muted)" }}>
          ← Decide
        </Link>
        <span style={{ fontSize: 12, color: "var(--text-muted)" }}>·</span>
        <span style={{ fontSize: 12, color: "var(--text-muted)" }}>
          {state === "ready" && universe
            ? `Sourced from ${universe} cache`
            : `Symbol Deep Dive`}
        </span>
      </header>

      <SectionHeader symbol={symbol} state={state} row={row} detail={detail} />

      <Section title="2. Verdict" todo="evaluate_symbols(symbol) → BUY/WAIT/AVOID badge + 4/7 fraction" />
      <Section title="3. Decision trace" todo="market_state.decision_trace → collapsible pass/fail/warn list" />
      <Section title="4. Strategy vote (CONFLICT surfacing)"
               todo="per-strategy row + conflict counter — THIS IS THE MOAT" />
      <Section title="5. News + sentiment" todo="get_news_with_sentiment(symbol, limit=8)" />
      <Section title="6. Analyst consensus" todo="get_analyst_recommendations(symbol)" />
      <Section title="7. Event risk (earnings)" todo="get_earnings_calendar(symbol, days=90)" />
      <Section title="8. Regime survival" todo="get_regime_history(symbol, strategy=best_long)" />
      <Section title="9. Peer comparison" todo="derive peer set from symbol.tags, get_returns + evaluate_symbols on peers" />
      <Section title="10. Hit rate" todo="get_hitrate(symbol, strategy, horizon_days=20) per strategy" />
    </div>
  );
}

// ----------------------------------------------------------------------
// Section 1 — Header. Real content when row is present, skeleton or
// empty state otherwise.
// ----------------------------------------------------------------------

function SectionHeader(props: {
  symbol: string;
  state: ShellState;
  row?: CompareRow | null;
  detail?: string;
}) {
  const { symbol, state, row, detail } = props;

  if (state === "loading") return <HeaderSkeleton symbol={symbol} message="Loading…" />;
  if (state === "error") return (
    <HeaderSkeleton symbol={symbol}
      message={`Couldn't load. ${detail ?? ""}`} tone="error" />
  );
  if (state === "not-in-cache") return (
    <HeaderSkeleton symbol={symbol}
      message="Not in any cached universe. Live compute path lands with task #66."
      tone="warn" />
  );
  if (state === "empty" || !row) return <HeaderSkeleton symbol={symbol} message="No data." />;

  const ms = row.market_state;
  const lastPrice = ms?.last_price;
  const sma200 = ms?.sma_200;
  const rsi = ms?.rsi_14;
  const range = ms?.range_position_pct ?? ms?.range_pct ?? null;
  const vol = ms?.vol_30d_annual_pct;
  // Day-change isn't on the compare row today; surfaces with task #66's
  // live market_state endpoint when that lands.
  const fmt = (x: number | null | undefined, suffix = "", digits = 2) =>
    x == null ? "—" : `${x.toFixed(digits)}${suffix}`;
  const fmtMoney = (x: number | null | undefined) =>
    x == null ? "—" : `$${x.toFixed(2)}`;

  return (
    <section style={cardStyle}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline" }}>
        <div>
          <div style={{ fontSize: 28, fontWeight: 700, lineHeight: 1.1 }}>{symbol}</div>
          <div style={{ fontSize: 13, color: "var(--text-muted)" }}>
            {/* strategy name as a subtitle until we surface symbol's
                instrument name (no such field on CompareRow yet) */}
            {row.strategy ? `via ${row.strategy}` : ""}
          </div>
        </div>
        <div style={{ textAlign: "right" }}>
          <div style={{ fontSize: 28, fontWeight: 600, fontVariantNumeric: "tabular-nums" }}>
            {fmtMoney(lastPrice)}
          </div>
          <div style={{ fontSize: 11, color: "var(--text-muted)" }}>
            as of {ms?.as_of?.slice(0, 10) ?? "—"}
          </div>
        </div>
      </div>

      {range != null && (
        <RangeBar
          label="52w range"
          rangePct={range}
          low={ms?.low_52w_price ?? null}
          high={lastPrice != null && ms?.pct_off_52w_high_pct != null
            ? lastPrice / (1 + ms.pct_off_52w_high_pct / 100)
            : null}
        />
      )}

      <div style={{ display: "grid", gridTemplateColumns: "repeat(4, 1fr)", gap: 12, marginTop: 4 }}>
        <Stat label="RSI 14" value={fmt(rsi, "", 0)} />
        <Stat label="200d SMA" value={fmtMoney(sma200)}
              detail={ms?.above_sma_200 == null ? undefined : ms.above_sma_200 ? "price above" : "price below"} />
        <Stat label="Vol (30d ann.)" value={fmt(vol, "%", 0)} />
        <Stat label="% off 52w high" value={fmt(ms?.pct_off_52w_high_pct, "%", 1)} />
      </div>
    </section>
  );
}

function HeaderSkeleton(props: {
  symbol: string;
  message: string;
  tone?: "warn" | "error";
}) {
  const { symbol, message, tone } = props;
  const color = tone === "error" ? "var(--down)"
              : tone === "warn"  ? "var(--warn, #c79a2a)"
              : "var(--text-muted)";
  return (
    <section style={{ ...cardStyle, opacity: 0.85 }}>
      <div style={{ fontSize: 28, fontWeight: 700 }}>{symbol}</div>
      <div style={{ fontSize: 13, color, marginTop: 6 }}>{message}</div>
    </section>
  );
}

// ----------------------------------------------------------------------
// Skeleton section placeholder
// ----------------------------------------------------------------------

function Section(props: { title: string; todo: string }) {
  return (
    <section style={{
      ...cardStyle,
      borderStyle: "dashed",
      background: "transparent",
    }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline" }}>
        <strong style={{ fontSize: 14 }}>{props.title}</strong>
        <span style={{ fontSize: 10, color: "var(--text-muted)" }}>TODO</span>
      </div>
      <div style={{ fontSize: 12, color: "var(--text-muted)", marginTop: 6 }}>
        {props.todo}
      </div>
    </section>
  );
}

// ----------------------------------------------------------------------
// Small UI primitives
// ----------------------------------------------------------------------

const cardStyle: React.CSSProperties = {
  padding: 16,
  border: "1px solid var(--border)",
  borderRadius: 10,
  background: "var(--bg-elevated, transparent)",
  display: "flex",
  flexDirection: "column",
  gap: 8,
};

function Stat(props: { label: string; value: string; detail?: string }) {
  return (
    <div>
      <div style={{ fontSize: 10, color: "var(--text-muted)", textTransform: "uppercase", letterSpacing: 0.5 }}>
        {props.label}
      </div>
      <div style={{ fontSize: 16, fontWeight: 600, fontVariantNumeric: "tabular-nums" }}>
        {props.value}
      </div>
      {props.detail && (
        <div style={{ fontSize: 11, color: "var(--text-muted)" }}>{props.detail}</div>
      )}
    </div>
  );
}

function RangeBar(props: {
  label: string;
  rangePct: number;
  low: number | null;
  high: number | null;
}) {
  const pct = Math.max(0, Math.min(100, props.rangePct));
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
      <div style={{ display: "flex", justifyContent: "space-between", fontSize: 10, color: "var(--text-muted)" }}>
        <span>{props.label}</span>
        <span>
          {props.low != null && `$${props.low.toFixed(2)}`}
          {" → "}
          {props.high != null && `$${props.high.toFixed(2)}`}
        </span>
      </div>
      <div style={{
        position: "relative",
        height: 8,
        background: "var(--border)",
        borderRadius: 999,
        overflow: "hidden",
      }}>
        <div style={{
          width: `${pct}%`,
          height: "100%",
          background: "var(--up)",
        }} />
        <div style={{
          position: "absolute",
          top: -2,
          left: `calc(${pct}% - 4px)`,
          width: 8,
          height: 12,
          background: "var(--text)",
          borderRadius: 999,
        }} />
      </div>
      <div style={{ fontSize: 10, color: "var(--text-muted)" }}>
        Current at {pct.toFixed(0)}th percentile
      </div>
    </div>
  );
}

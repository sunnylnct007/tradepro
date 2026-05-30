import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { api } from "../api/client";
import { SymbolAnalysisCard } from "../components/SymbolAnalysisCard";
import { NewsContextPanel } from "../components/cockpit/NewsContextPanel";
import { TrustDot, TrustLegend } from "../components/TrustDot";
import type {
  CompareCatalyst,
  CompareCombinedVerdict,
  CompareLatestResponse,
  CompareNewsItem,
  CompareRow,
  CompareRowRegime,
  CompareSentimentSummary,
  DecisionCheck,
  HitRateResult,
} from "../api/types";

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
  // All rows for this symbol from the matched universe — kept around
  // so Section 2 (verdict) can count `in_position` across strategies
  // and Section 4 can render per-strategy detail.
  const [allRows, setAllRows] = useState<CompareRow[]>([]);
  // One best-Sharpe row per *other* symbol in the same universe, used
  // for Section 9 peer comparison. Populated alongside allRows.
  const [peerRows, setPeerRows] = useState<CompareRow[]>([]);
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
            // Build one best-Sharpe row per OTHER symbol for peers.
            const bySymbol = new Map<string, CompareRow>();
            for (const r of rows) {
              if (r.symbol === symbol) continue;
              const existing = bySymbol.get(r.symbol);
              if (!existing || sharpeOf(r) > sharpeOf(existing)) {
                bySymbol.set(r.symbol, r);
              }
            }
            if (!cancelled) {
              setRow(match[0]);
              setAllRows(match);
              setPeerRows([...bySymbol.values()]);
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
    <PageShell symbol={symbol} state="ready" row={row}
               allRows={allRows} peerRows={peerRows} universe={universe} />
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
  allRows?: CompareRow[];
  peerRows?: CompareRow[];
  universe?: string | null;
  detail?: string;
}) {
  const { symbol, state, row, allRows = [], peerRows = [], universe, detail } = props;
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
        <span style={{ marginLeft: "auto" }}>
          <TrustLegend />
        </span>
      </header>

      <SectionHeader symbol={symbol} state={state} row={row} detail={detail} />

      {state === "ready" && row && (
        <SectionVerdict row={row} allRows={allRows} />
      )}
      {state === "ready" && row && (
        <SymbolAnalysisCard row={row} universe={universe} />
      )}
      {state === "ready" && row?.combined_verdict && (
        <SectionCombinedVerdict cv={row.combined_verdict} />
      )}
      {state === "ready" && row && (
        <SectionDecisionTrace trace={row.market_state?.decision_trace ?? []} />
      )}
      {state === "ready" && row && (
        <SectionStrategyVote row={row} allRows={allRows} />
      )}
      {state === "ready" && row && (
        <section style={cardStyle}>
          <div style={{ fontSize: 12, color: "var(--text-muted)", marginBottom: 8,
            textTransform: "uppercase", letterSpacing: "0.06em" }}>
            News context (LLM-scored)
          </div>
          <NewsContextPanel symbol={symbol} />
        </section>
      )}
      {state === "ready" && row && (
        <SectionNews
          items={row.news ?? []}
          summary={row.sentiment_summary}
          newsVia={row.news_via ?? null}
          catalysts={row.catalysts ?? []}
        />
      )}
      {state === "ready" && row && (
        <SectionAnalyst row={row} />
      )}
      {state === "ready" && row && (
        <SectionEarnings row={row} />
      )}
      {state === "ready" && row ? (
        <SectionRegimeSurvival row={row} />
      ) : (
        <Section title="8. Regime survival" trustId="deepdive.regime_survival"
          todo="Regime-survival data comes from the strategy replay on historical prices. Shown once the symbol loads." />
      )}
      {state === "ready" && row ? (
        <SectionPeerComparison symbol={symbol} row={row} peerRows={peerRows} />
      ) : (
        <Section title="9. Peer comparison" trustId="deepdive.peer_comparison"
          todo="Peers are other symbols from the same trading universe. Shown once the symbol loads." />
      )}
      {state === "ready" && allRows.length > 0 && (
        <SectionHitRate symbol={props.symbol} rows={allRows} />
      )}
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
          <div style={{ fontSize: 28, fontWeight: 700, lineHeight: 1.1 }}>
            {symbol}<TrustDot id="deepdive.header" size={10} />
          </div>
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

// ----------------------------------------------------------------------
// Section 2 — Verdict. The big BUY/WAIT/AVOID badge. Vote rendered as a
// fraction (4/7), per spec: "keeps 'is this unanimous?' obvious".
// ----------------------------------------------------------------------

function SectionVerdict(props: { row: CompareRow; allRows: CompareRow[] }) {
  const { row, allRows } = props;
  const bucket = row.bucket ?? "WAIT";
  const reason = row.bucket_reason ?? "";

  // Compatible (non-fit-excluded) rows are the denominator; the bucket
  // engine excludes structurally-incompatible strategies (e.g. RSI mean
  // reversion on MTUM) from the consensus count. Falls back to
  // row.consensus_compatible_count when it's present.
  const compatible = allRows.filter((r) => !r.excluded_for_fit);
  const total = row.consensus_compatible_count ?? compatible.length;
  const longCount = compatible.filter((r) => r.in_position).length;

  const bucketStyle = bucketChipStyle(bucket);
  return (
    <section style={cardStyle}>
      <div style={{ display: "flex", alignItems: "center", gap: 16 }}>
        <div style={{
          ...bucketStyle,
          fontSize: 32,
          fontWeight: 700,
          padding: "8px 18px",
          borderRadius: 10,
          letterSpacing: 1,
        }}>{bucket}<TrustDot id="deepdive.verdict" size={10} /></div>
        <div style={{ flex: 1 }}>
          <div style={{ fontSize: 18, fontWeight: 600, fontVariantNumeric: "tabular-nums" }}>
            {longCount} of {total} strategies long
          </div>
          {row.consensus_excluded_count != null && row.consensus_excluded_count > 0 && (
            <div style={{ fontSize: 11, color: "var(--text-muted)" }}>
              {row.consensus_excluded_count} strategy{row.consensus_excluded_count === 1 ? "" : "ies"} excluded for fit
              {row.consensus_excluded_strategies?.length
                ? ` (${row.consensus_excluded_strategies.join(", ")})`
                : ""}
            </div>
          )}
        </div>
      </div>
      {reason && (
        <div style={{
          fontSize: 14,
          lineHeight: 1.5,
          color: "var(--text-dim)",
          marginTop: 4,
        }}>
          {/* Verbatim from bucket_reason per spec — do not paraphrase. */}
          {reason}
        </div>
      )}
      {row.sentiment_demoted && (
        <div style={{
          fontSize: 11,
          color: "var(--warn, #c79a2a)",
          marginTop: 4,
        }}>
          ⚠ Sentiment demotion applied — see decision trace below.
        </div>
      )}
    </section>
  );
}

function bucketChipStyle(b: string): React.CSSProperties {
  if (b === "BUY") return { background: "rgba(58, 165, 109, 0.18)", color: "var(--up)" };
  if (b === "AVOID") return { background: "rgba(214, 76, 76, 0.18)", color: "var(--down)" };
  return { background: "rgba(199, 154, 42, 0.18)", color: "var(--warn, #c79a2a)" };
}

// ----------------------------------------------------------------------
// Section 2.5 — Combined verdict. Fuses technical bucket + catalyst
// overlay + analyst flow into a single annotated recommendation. The
// Ecopetrol (EC) case 2026-05-21 was the design driver — technicals
// said WAIT but Colombia election + oil surge made it BUY. Phase 17.5
// of DATA_ROADMAP §13.5. NEVER replaces the technical bucket — the
// section sits ALONGSIDE Section 2 so users see both views.
// ----------------------------------------------------------------------

function SectionCombinedVerdict(props: { cv: CompareCombinedVerdict }) {
  const { cv } = props;
  const accent = combinedAccent(cv.combined_kind);
  return (
    <section
      style={{
        ...cardStyle,
        borderLeft: `3px solid ${accent}`,
      }}
    >
      <div style={{ display: "flex", alignItems: "baseline", justifyContent: "space-between", gap: 12, flexWrap: "wrap" }}>
        <strong style={{ fontSize: 14 }}>
          2.5 Combined verdict
          <span style={{ marginLeft: 6, fontSize: 10, padding: "2px 6px", borderRadius: 10, background: "rgba(155, 110, 255, 0.14)", color: "#cbb6ff", border: "1px solid rgba(155, 110, 255, 0.35)" }}>
            CATALYST-AWARE
          </span>
        </strong>
        <span style={{ fontSize: 11, color: "var(--text-muted)" }}>
          Confidence: <strong style={{ color: "var(--text-dim)" }}>{cv.confidence}</strong>
        </span>
      </div>

      <div style={{ fontSize: 22, fontWeight: 700, marginTop: 8, color: accent }}>
        {cv.combined}
      </div>

      {cv.catalyst.soonest_date && (
        <div style={{ marginTop: 4, fontSize: 12, color: "var(--text-dim)" }}>
          Soonest catalyst:{" "}
          <strong style={{ color: "var(--text)" }}>
            {cv.catalyst.soonest_kind ?? "event"} on {cv.catalyst.soonest_date}
          </strong>
        </div>
      )}

      <div
        style={{
          display: "grid",
          gridTemplateColumns: "auto 1fr",
          columnGap: 12,
          rowGap: 4,
          marginTop: 12,
          fontSize: 12,
        }}
      >
        <span style={{ color: "var(--text-muted)" }}>Technical</span>
        <span>
          <CompactSignal signal={cv.technical.signal} />
          {cv.technical.reason && (
            <span style={{ color: "var(--text-dim)" }}> · {cv.technical.reason}</span>
          )}
        </span>

        <span style={{ color: "var(--text-muted)" }}>Catalyst</span>
        <span>
          <CompactSignal signal={cv.catalyst.signal} />
          {cv.catalyst.reasons.length > 0 && (
            <span style={{ color: "var(--text-dim)" }}>
              {" "}· {cv.catalyst.reasons.slice(0, 2).join(" · ")}
            </span>
          )}
        </span>

        <span style={{ color: "var(--text-muted)" }}>Analyst</span>
        <span>
          <CompactSignal signal={cv.analyst.signal} />
          {cv.analyst.reason && (
            <span style={{ color: "var(--text-dim)" }}> · {cv.analyst.reason}</span>
          )}
        </span>
      </div>

      {cv.reasoning.length > 0 && (
        <div
          style={{
            marginTop: 12,
            paddingTop: 10,
            borderTop: "1px solid var(--border)",
            fontSize: 12,
            color: "var(--text-dim)",
            display: "flex",
            flexDirection: "column",
            gap: 4,
          }}
        >
          <div style={{ fontSize: 10, fontWeight: 700, letterSpacing: "0.08em", textTransform: "uppercase", color: "var(--text-muted)" }}>
            Reasoning
          </div>
          {cv.reasoning.map((line, i) => (
            <div key={i}>• {line}</div>
          ))}
        </div>
      )}

      <div
        style={{
          marginTop: 10,
          fontSize: 10,
          color: "var(--text-muted)",
          fontStyle: "italic",
        }}
      >
        Catalyst-aware verdict is rule-based + experimental. Does NOT
        override the technical bucket above — the two sit alongside so
        you can reason about why they disagree. See DATA_ROADMAP §13.5.
      </div>
    </section>
  );
}

function CompactSignal({ signal }: { signal: string }) {
  const colour = signalColour(signal);
  return (
    <strong style={{ color: colour, fontFamily: "ui-monospace, SFMono-Regular, Menlo, monospace", fontSize: 11 }}>
      {signal}
    </strong>
  );
}

function combinedAccent(kind: string): string {
  switch (kind) {
    case "STRONG_BUY":
    case "BUY":
      return "var(--up)";
    case "BUY_WITH_RISK":
      return "#9b6eff";       // violet — distinct from clean BUY
    case "WAIT":
      return "var(--warn, #c79a2a)";
    case "AVOID":
    case "AVOID_DESPITE_CATALYST":
      return "var(--down)";
    default:
      return "var(--text-dim)";
  }
}

function signalColour(signal: string): string {
  if (signal.startsWith("STRONG_BUY") || signal === "BUY") return "var(--up)";
  if (signal.startsWith("STRONG_AVOID") || signal === "AVOID") return "var(--down)";
  if (signal === "WAIT") return "var(--warn, #c79a2a)";
  if (signal === "MIXED") return "var(--text-dim)";
  return "var(--text-muted)";
}

// ----------------------------------------------------------------------
// Section 3 — Decision trace. Collapsible list of pass/fail/warn rules
// that fed the verdict. Default expanded. Failures first, warnings
// second, passes third so the user sees risks before reassurance (spec).
// ----------------------------------------------------------------------

function SectionDecisionTrace(props: { trace: DecisionCheck[] }) {
  const [open, setOpen] = useState(true);
  const ranked = [...props.trace].sort((a, b) => rankStatus(a.status) - rankStatus(b.status));
  const failCount = ranked.filter((c) => c.status === "fail").length;
  const warnCount = ranked.filter((c) => c.status === "warn").length;

  return (
    <section style={cardStyle}>
      <button
        type="button"
        onClick={() => setOpen(!open)}
        style={{
          display: "flex",
          alignItems: "baseline",
          justifyContent: "space-between",
          background: "transparent",
          border: "none",
          padding: 0,
          cursor: "pointer",
          color: "inherit",
          width: "100%",
        }}
      >
        <strong style={{ fontSize: 14 }}>
          3. Decision trace<TrustDot id="deepdive.decision_trace" />
          {failCount > 0 && (
            <span style={{ marginLeft: 8, fontSize: 11, color: "var(--down)" }}>
              {failCount} fail
            </span>
          )}
          {warnCount > 0 && (
            <span style={{ marginLeft: 8, fontSize: 11, color: "var(--warn, #c79a2a)" }}>
              {warnCount} warn
            </span>
          )}
        </strong>
        <span style={{ fontSize: 11, color: "var(--text-muted)" }}>{open ? "▾" : "▸"}</span>
      </button>
      {open && ranked.length === 0 && (
        <div style={{ fontSize: 12, color: "var(--text-muted)" }}>
          No decision trace on this row.
        </div>
      )}
      {open && ranked.length > 0 && (
        <div style={{ display: "flex", flexDirection: "column", gap: 4, marginTop: 4 }}>
          {ranked.map((c, i) => (
            <div key={i} style={{
              display: "grid",
              gridTemplateColumns: "20px 1fr 1.5fr",
              alignItems: "baseline",
              gap: 8,
              fontSize: 12,
              padding: "4px 0",
              borderTop: i === 0 ? "none" : "1px solid var(--border)",
            }}>
              <span style={{
                color: c.status === "fail" ? "var(--down)"
                     : c.status === "warn" ? "var(--warn, #c79a2a)"
                     : "var(--up)",
                fontSize: 14,
              }}>
                {c.status === "fail" ? "✗" : c.status === "warn" ? "⚠" : "✓"}
              </span>
              <span style={{ color: "var(--text)" }}>{c.name}</span>
              {/* detail text verbatim from API per spec — do not reformat */}
              <span style={{ color: "var(--text-muted)" }}>{c.detail}</span>
            </div>
          ))}
        </div>
      )}
    </section>
  );
}

function rankStatus(s: string): number {
  if (s === "fail") return 0;
  if (s === "warn") return 1;
  return 2;
}

// ----------------------------------------------------------------------
// Section 4 — Strategy vote with EXPLICIT conflict surfacing. This is
// the spec's main new product idea: hiding contradictions produces
// false confidence; making them visible is the moat. Three conflict
// types we surface:
//
//   (a) per-strategy signal contradicts the overall bucket
//   (b) strategy has near-zero historical Sharpe on this symbol
//   (c) position is legacy (held > 1y with deep loss) — informational
//
// Plus excluded-for-fit rows are sunk to the bottom and labelled, since
// the bucket engine already dropped them from the vote (phase 6.5).
// ----------------------------------------------------------------------

interface StrategyRowView {
  strategy: string;
  strategy_label: string;
  in_position: boolean;
  position_since: string | null;
  sharpe: number | null;
  latest_signal: number;
  excluded_for_fit: boolean;
  excluded_reason: string | null;
  // Derived conflict flags
  conflictsWithBucket: boolean;
  isLowEdge: boolean;
  isLegacy: boolean;
}

function SectionStrategyVote(props: { row: CompareRow; allRows: CompareRow[] }) {
  const { row, allRows } = props;
  const bucket = row.bucket ?? "WAIT";
  // Build per-strategy views with derived conflict flags. Sort:
  // compatible first by Sharpe desc, then excluded ones grouped at the
  // bottom (with rank rendered as "—").
  const views: StrategyRowView[] = allRows.map((r) => {
    const sharpe = (r.stats?.sharpe as number | null | undefined) ?? null;
    const conflictsWithBucket =
      (bucket === "BUY" && r.latest_signal === -1) ||
      (bucket === "AVOID" && r.latest_signal === 1);
    const isLowEdge = sharpe != null && sharpe < 0.1 && !r.excluded_for_fit;
    const isLegacy = isStaleEntry(r.position_since) && r.strategy !== "buy_and_hold";
    return {
      strategy: r.strategy,
      strategy_label: r.strategy_label || r.strategy,
      in_position: r.in_position,
      position_since: r.position_since,
      sharpe,
      latest_signal: r.latest_signal,
      excluded_for_fit: !!r.excluded_for_fit,
      excluded_reason: r.excluded_reason ?? null,
      conflictsWithBucket,
      isLowEdge,
      isLegacy,
    };
  });
  // Sort: compatible by Sharpe desc, excluded at bottom by Sharpe desc.
  views.sort((a, b) => {
    if (a.excluded_for_fit !== b.excluded_for_fit) {
      return a.excluded_for_fit ? 1 : -1;
    }
    return (b.sharpe ?? -Infinity) - (a.sharpe ?? -Infinity);
  });

  const conflictCount = views.filter((v) =>
    !v.excluded_for_fit && (v.conflictsWithBucket || v.isLowEdge)
  ).length;

  return (
    <section style={cardStyle}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline" }}>
        <strong style={{ fontSize: 14 }}>4. Strategy vote<TrustDot id="deepdive.conflict_ux" /></strong>
        {conflictCount > 0 ? (
          <span style={{ fontSize: 11, color: "var(--down)", fontWeight: 600 }}>
            ● {conflictCount} conflict{conflictCount === 1 ? "" : "s"} surfaced
          </span>
        ) : (
          <span style={{ fontSize: 11, color: "var(--text-muted)" }}>
            no conflicts
          </span>
        )}
      </div>
      <div style={{ display: "flex", flexDirection: "column", gap: 2, marginTop: 4 }}>
        {views.map((v, i) => (
          <StrategyRow key={v.strategy} view={v} index={i} />
        ))}
      </div>
      <div style={{ fontSize: 11, color: "var(--text-muted)", marginTop: 4 }}>
        Conflict types surfaced: signal-vs-bucket (this strategy disagrees with
        the consensus bucket), low-edge (Sharpe &lt; 0.1 — strategy has no
        historical edge on this symbol), legacy (position held &gt; 1y).
        Excluded-for-fit rows are sorted to the bottom — the bucket vote
        already dropped them.
      </div>
    </section>
  );
}

function StrategyRow(props: { view: StrategyRowView; index: number }) {
  const v = props.view;
  const positionLabel = v.excluded_for_fit ? "EXCLUDED"
                      : v.isLegacy ? "LEGACY"
                      : v.in_position ? "LONG"
                      : "FLAT";
  const positionColor = v.excluded_for_fit ? "var(--text-muted)"
                      : v.isLegacy ? "var(--warn, #c79a2a)"
                      : v.in_position ? "var(--up)"
                      : "var(--text-muted)";
  return (
    <div style={{
      borderTop: props.index === 0 ? "none" : "1px solid var(--border)",
      padding: "6px 0",
      opacity: v.excluded_for_fit ? 0.6 : 1,
    }}>
      <div style={{
        display: "grid",
        gridTemplateColumns: "1.4fr 0.9fr 1.2fr 0.6fr",
        alignItems: "baseline",
        fontSize: 12,
        gap: 8,
      }}>
        <span style={{ color: "var(--text)" }}>{v.strategy_label}</span>
        <span style={{ color: positionColor, fontWeight: 600 }}>● {positionLabel}</span>
        <span style={{ color: "var(--text-muted)", fontVariantNumeric: "tabular-nums" }}>
          {v.position_since ? `since ${v.position_since.slice(0, 10)}` : "—"}
        </span>
        <span style={{
          color: "var(--text-muted)",
          fontVariantNumeric: "tabular-nums",
          textAlign: "right",
        }}>
          Sharpe {v.sharpe == null ? "—" : v.sharpe.toFixed(2)}
        </span>
      </div>
      {v.conflictsWithBucket && (
        <ConflictHint
          text={`signal ${v.latest_signal === -1 ? "SELL" : "BUY"} ← conflicts with bucket — strategy disagrees with consensus`}
        />
      )}
      {v.isLowEdge && (
        <ConflictHint
          text={`Sharpe ${v.sharpe!.toFixed(2)} < 0.1 — strategy has no historical edge on this symbol`}
        />
      )}
      {v.isLegacy && (
        <ConflictHint
          tone="info"
          text={`held > 1y from ${v.position_since?.slice(0, 10)} — informational only, not a fresh entry`}
        />
      )}
      {v.excluded_for_fit && (
        <ConflictHint
          tone="muted"
          text={`excluded for fit${v.excluded_reason ? ` — ${v.excluded_reason}` : ""}`}
        />
      )}
    </div>
  );
}

function ConflictHint(props: { text: string; tone?: "info" | "muted" }) {
  const color = props.tone === "muted" ? "var(--text-muted)"
              : props.tone === "info" ? "var(--warn, #c79a2a)"
              : "var(--down)";
  return (
    <div style={{ fontSize: 11, color, marginLeft: 16, marginTop: 2 }}>
      ↳ {props.text}
    </div>
  );
}

// ----------------------------------------------------------------------
// Section 5 — News + sentiment. Latest headlines with per-item sentiment
// chips + a 7-day rolling summary header. Per spec:
//   • chip colour: green if > +0.3, red if < -0.3, amber between
//   • header turns red when material_negative_count >= 2
//   • show theme tags from API as small chips
//   • click opens publisher link in new tab
// ----------------------------------------------------------------------

function SectionNews(props: {
  items: CompareNewsItem[];
  summary?: CompareSentimentSummary;
  newsVia: string | null;
  catalysts: CompareCatalyst[];
}) {
  const { items, summary, newsVia, catalysts } = props;
  const mean = summary?.mean_sentiment ?? null;
  const matNeg = summary?.material_negative_count ?? 0;
  // Header turns red when material_negative_count >= 2 — explicit
  // signal that sentiment is bad enough to potentially demote the
  // bucket (matches the BUY → WAIT demotion rule in compute_bucket).
  const headerTone = matNeg >= 2 ? "negative"
                  : mean != null && mean >= 0.3 ? "positive"
                  : "neutral";
  const headerColor = headerTone === "negative" ? "var(--down)"
                    : headerTone === "positive" ? "var(--up)"
                    : "var(--text-muted)";

  if (items.length === 0) {
    return (
      <section style={cardStyle}>
        <strong style={{ fontSize: 14 }}>5. News + sentiment<TrustDot id="deepdive.news" /></strong>
        <div style={{ fontSize: 12, color: "var(--text-muted)" }}>
          No headlines in the last 7 days. (This is a state, not an error —
          quiet news is meaningful: no fresh catalyst either way.)
        </div>
      </section>
    );
  }

  return (
    <section style={cardStyle}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline" }}>
        <strong style={{ fontSize: 14 }}>
          5. News + sentiment<TrustDot id="deepdive.news" />
          <span style={{ marginLeft: 8, fontSize: 11, fontWeight: 400, color: "var(--text-muted)" }}>
            ({items.length} item{items.length === 1 ? "" : "s"})
          </span>
        </strong>
        {mean != null && (
          <span style={{ fontSize: 12, color: headerColor, fontWeight: 600, fontVariantNumeric: "tabular-nums" }}>
            7d mean {mean >= 0 ? "+" : ""}{mean.toFixed(2)}
            {matNeg >= 2 && ` · ${matNeg} material negative ↓`}
          </span>
        )}
      </div>
      {newsVia && (
        <div style={{ fontSize: 11, color: "var(--text-muted)" }}>
          via proxy: {newsVia} — these headlines are about the proxy, not the symbol directly
        </div>
      )}
      <CatalystChips catalysts={catalysts} />
      <div style={{ display: "flex", flexDirection: "column", gap: 2, marginTop: 4 }}>
        {items.map((item, i) => (
          <NewsRow key={i} item={item} index={i} />
        ))}
      </div>
    </section>
  );
}

// ----------------------------------------------------------------------
// Catalyst chips — dated events extracted from the same news headlines
// (Phase 17.3 of DATA_ROADMAP §13.5). Renders above the news list with
// kind-coded chips. Each chip shows the catalyst kind, a relative
// countdown to occurs_on when present, and the source title in the
// tooltip. Empty list = no chip row shown (don't waste vertical space).
// ----------------------------------------------------------------------

function CatalystChips(props: { catalysts: CompareCatalyst[] }) {
  if (!props.catalysts || props.catalysts.length === 0) return null;
  return (
    <div
      style={{
        marginTop: 8,
        padding: "8px 10px",
        borderRadius: 6,
        background: "rgba(155, 110, 255, 0.06)",
        border: "1px solid rgba(155, 110, 255, 0.25)",
      }}
    >
      <div
        style={{
          fontSize: 10,
          fontWeight: 700,
          letterSpacing: "0.08em",
          textTransform: "uppercase",
          color: "#cbb6ff",
          marginBottom: 6,
        }}
      >
        Catalysts (extracted from headlines)
      </div>
      <div style={{ display: "flex", flexWrap: "wrap", gap: 6 }}>
        {props.catalysts.map((c, i) => (
          <CatalystChip key={i} catalyst={c} />
        ))}
      </div>
      <div style={{ fontSize: 10, color: "var(--text-muted)", marginTop: 6 }}>
        Pure-keyword extraction (no LLM). Pure-technical bucket above is
        unchanged — these chips annotate why a trade may exist beyond
        the technical signal alone. See DATA_ROADMAP §13.5 catalyst sprint.
      </div>
    </div>
  );
}

function CatalystChip(props: { catalyst: CompareCatalyst }) {
  const { catalyst: c } = props;
  const { label, icon, colour } = catalystVisual(c.kind);
  const daysAway = c.occurs_on ? daysFromNow(c.occurs_on) : null;
  const countdown =
    daysAway == null ? ""
      : daysAway < 0 ? ` · ${Math.abs(daysAway)}d ago`
      : daysAway === 0 ? " · today"
      : daysAway === 1 ? " · tomorrow"
      : ` · ${daysAway}d away`;
  const tooltip =
    `${label}${countdown}\n` +
    `Date: ${c.occurs_on ?? "undated"}\n` +
    `Confidence: ${(c.confidence * 100).toFixed(0)}%\n` +
    `Source: ${c.title}` +
    (c.rationale ? `\nWhy: ${c.rationale}` : "");
  return (
    <span
      title={tooltip}
      style={{
        display: "inline-flex",
        alignItems: "center",
        gap: 4,
        padding: "3px 8px",
        borderRadius: 12,
        fontSize: 11,
        fontWeight: 600,
        background: `rgba(${colour}, 0.12)`,
        border: `1px solid rgba(${colour}, 0.4)`,
        color: `rgb(${colour})`,
        cursor: "help",
      }}
    >
      <span aria-hidden>{icon}</span>
      <span>{label}{countdown}</span>
    </span>
  );
}

function catalystVisual(kind: string): { label: string; icon: string; colour: string } {
  switch (kind) {
    case "election":
      return { label: "Election", icon: "🗳", colour: "111, 169, 255" };
    case "earnings":
      return { label: "Earnings", icon: "📊", colour: "31, 193, 107" };
    case "central_bank":
      return { label: "Central bank", icon: "🏦", colour: "210, 153, 34" };
    case "commodity":
      return { label: "Commodity", icon: "🛢", colour: "248, 81, 73" };
    case "regulatory":
      return { label: "Regulatory", icon: "⚖", colour: "203, 182, 255" };
    default:
      return { label: kind, icon: "•", colour: "180, 180, 180" };
  }
}

function daysFromNow(iso: string): number | null {
  const target = Date.parse(iso);
  if (Number.isNaN(target)) return null;
  const now = Date.now();
  const oneDay = 1000 * 60 * 60 * 24;
  return Math.round((target - now) / oneDay);
}

function NewsRow(props: { item: CompareNewsItem; index: number }) {
  const { item, index } = props;
  const [open, setOpen] = useState(false);
  const score = item.sentiment ?? null;
  const chip = sentimentChip(score);
  const dateStr = item.published_at ? item.published_at.slice(0, 10) : "—";
  return (
    <div style={{
      borderTop: index === 0 ? "none" : "1px solid var(--border)",
      padding: "6px 0",
    }}>
      <div style={{
        display: "grid",
        gridTemplateColumns: "70px 1fr 130px",
        alignItems: "baseline",
        gap: 10,
        fontSize: 12,
      }}>
        <span style={{
          fontSize: 11,
          padding: "2px 6px",
          borderRadius: 4,
          background: chip.bg,
          color: chip.fg,
          textAlign: "center",
          fontWeight: 600,
          fontVariantNumeric: "tabular-nums",
        }}>
          {chip.label}
        </span>
        <span>
          {item.link ? (
            <a href={item.link} target="_blank" rel="noopener noreferrer"
               style={{ color: "var(--text)", textDecoration: "none" }}>
              {item.title}
            </a>
          ) : (
            <span style={{ color: "var(--text)" }}>{item.title}</span>
          )}
          {item.sentiment_themes && item.sentiment_themes.length > 0 && (
            <span style={{ marginLeft: 6 }}>
              {item.sentiment_themes.slice(0, 3).map((t) => (
                <span key={t} style={{
                  display: "inline-block",
                  fontSize: 10,
                  padding: "1px 5px",
                  marginRight: 3,
                  borderRadius: 3,
                  background: "var(--border)",
                  color: "var(--text-muted)",
                }}>{t}</span>
              ))}
            </span>
          )}
        </span>
        <span style={{ color: "var(--text-muted)", fontSize: 11, textAlign: "right" }}>
          {item.publisher || "—"} · {dateStr}
        </span>
      </div>
      {item.sentiment_material && (
        <div style={{ fontSize: 10, color: "var(--down)", marginLeft: 80, marginTop: 2 }}>
          ↳ flagged material — fed the negative-sentiment counter
        </div>
      )}
      {item.sentiment_error && (
        <div style={{ fontSize: 10, color: "var(--warn, #c79a2a)", marginLeft: 80, marginTop: 2 }}>
          ↳ sentiment scoring failed: {item.sentiment_error}
        </div>
      )}
      {open && item.sentiment_model && (
        <div style={{ fontSize: 10, color: "var(--text-muted)", marginLeft: 80, marginTop: 2 }}>
          scored by {item.sentiment_model}
        </div>
      )}
      {item.sentiment_model && (
        <button type="button"
          onClick={() => setOpen(!open)}
          style={{
            marginLeft: 80, marginTop: 2,
            fontSize: 10, color: "var(--text-muted)",
            background: "transparent", border: "none",
            cursor: "pointer", padding: 0,
          }}>
          {open ? "hide details" : "show details"}
        </button>
      )}
    </div>
  );
}

// ----------------------------------------------------------------------
// Section 6 — Analyst consensus. Stacked bar of strongBuy / buy / hold /
// sell / strongSell counts + month-over-month direction arrow. Per spec:
//   • bar widths proportional to counts
//   • StrongBuy=green; Buy=green; Hold=grey; Sell=red; StrongSell=red
//   • momChange arrow: green if positive, red if negative, grey if 0
//   • "% bullish" computed as (StrongBuy + Buy) / total
// ----------------------------------------------------------------------

function SectionAnalyst(props: { row: CompareRow }) {
  const data = props.row.analyst_recommendations;
  if (!data) {
    return (
      <section style={cardStyle}>
        <strong style={{ fontSize: 14 }}>6. Analyst consensus<TrustDot id="deepdive.analyst_static" /></strong>
        <div style={{ fontSize: 12, color: "var(--text-muted)" }}>
          Not available — Finnhub integration disabled, or no coverage for this symbol.
        </div>
      </section>
    );
  }
  const sb = data.strong_buy;
  const b = data.buy;
  const h = data.hold;
  const s = data.sell;
  const ss = data.strong_sell;
  const total = sb + b + h + s + ss;
  const pctBullish = total > 0 ? Math.round(((sb + b) / total) * 100) : 0;
  const momArrow = data.mom_change > 0 ? "▲"
                : data.mom_change < 0 ? "▼"
                : "—";
  const momColor = data.mom_change > 0 ? "var(--up)"
                : data.mom_change < 0 ? "var(--down)"
                : "var(--text-muted)";
  return (
    <section style={cardStyle}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline" }}>
        <strong style={{ fontSize: 14 }}>
          6. Analyst consensus<TrustDot id="deepdive.analyst_static" /><TrustDot id="deepdive.analyst_upgrades" />
          {data.latest_period && (
            <span style={{ marginLeft: 8, fontSize: 11, fontWeight: 400, color: "var(--text-muted)" }}>
              ({data.latest_period.slice(0, 7)})
            </span>
          )}
        </strong>
        <span style={{ fontSize: 12, fontVariantNumeric: "tabular-nums", color: "var(--text-muted)" }}>
          {total} analyst{total === 1 ? "" : "s"}
        </span>
      </div>

      {total > 0 && (
        <div style={{ display: "flex", height: 16, borderRadius: 4, overflow: "hidden", marginTop: 4 }}>
          <BarSegment count={sb} total={total} color="var(--up)" label={`Strong Buy ${sb}`} />
          <BarSegment count={b} total={total} color="rgba(58, 165, 109, 0.6)" label={`Buy ${b}`} />
          <BarSegment count={h} total={total} color="var(--text-muted)" label={`Hold ${h}`} />
          <BarSegment count={s} total={total} color="rgba(214, 76, 76, 0.6)" label={`Sell ${s}`} />
          <BarSegment count={ss} total={total} color="var(--down)" label={`Strong Sell ${ss}`} />
        </div>
      )}

      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 12, marginTop: 4 }}>
        <Stat label="% bullish" value={`${pctBullish}%`} />
        <div>
          <div style={{ fontSize: 10, color: "var(--text-muted)", textTransform: "uppercase", letterSpacing: 0.5 }}>
            Month-over-month
          </div>
          <div style={{ fontSize: 16, fontWeight: 600, color: momColor, fontVariantNumeric: "tabular-nums" }}>
            {momArrow} {data.mom_change >= 0 ? "+" : ""}{data.mom_change}
          </div>
          <div style={{ fontSize: 11, color: "var(--text-muted)" }}>
            net bullish-score change
          </div>
        </div>
      </div>

      {data.periods && data.periods.length > 1 && (
        <div style={{ fontSize: 10, color: "var(--text-muted)", marginTop: 4 }}>
          {data.periods.length} months of history available
        </div>
      )}
    </section>
  );
}

function BarSegment(props: { count: number; total: number; color: string; label: string }) {
  const pct = props.total > 0 ? (props.count / props.total) * 100 : 0;
  if (pct === 0) return null;
  return (
    <div
      title={props.label}
      style={{
        width: `${pct}%`,
        background: props.color,
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        color: "white",
        fontSize: 10,
        fontWeight: 600,
      }}
    >
      {pct >= 8 && props.count}
    </div>
  );
}

// ----------------------------------------------------------------------
// Section 7 — Event risk (earnings). Countdown to next print, or
// "clean window — no event in next 90 days" when there isn't one.
// Per spec: banner is red if T < 7 days, amber if T < 30, grey otherwise.
// Clean window = green/positive, an explicit feature not absence of data.
// ----------------------------------------------------------------------

function SectionEarnings(props: { row: CompareRow }) {
  const events = props.row.historical_earnings ?? [];
  const upcoming = props.row.earnings_signal?.upcoming;

  // Forward countdown — colour / urgency tier.
  const d = upcoming?.days_until ?? null;
  const countdownColour =
    d == null ? "var(--text-muted)"
    : d <= 7  ? "var(--down)"
    : d <= 14 ? "#ef4444"       // red-ish but not full var(--down)
    : d <= 30 ? "var(--warn, #c79a2a)"
    : "var(--text-muted)";
  const countdownBg =
    d == null ? undefined
    : d <= 14 ? "rgba(239,68,68,0.10)"
    : d <= 30 ? "rgba(245,158,11,0.09)"
    : undefined;
  const hourLabel = upcoming?.hour === "bmo" ? " before open"
                  : upcoming?.hour === "amc" ? " after close"
                  : "";

  // Clean-window: upcoming present but further than 30 days.
  const isCleanWindow = upcoming != null && d != null && d > 30;
  const noUpcomingKnown = upcoming == null;

  return (
    <section style={cardStyle}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline" }}>
        <strong style={{ fontSize: 14 }}>7. Event risk (earnings)<TrustDot id="deepdive.earnings" /></strong>
        {upcoming && d != null && d >= 0 && (
          <span style={{
            fontSize: 11, fontWeight: d <= 14 ? 700 : 500,
            padding: "2px 7px", borderRadius: 4,
            color: countdownColour,
            background: countdownBg,
          }}>
            EPS in {d}d{hourLabel}
          </span>
        )}
      </div>

      {/* ---- Forward countdown banner ---- */}
      {upcoming && d != null && d >= 0 && !isCleanWindow && (
        <div style={{
          padding: "10px 14px",
          borderRadius: 8,
          background: countdownBg ?? "var(--bg-elevated, rgba(255,255,255,0.03))",
          border: `1px solid ${countdownColour}`,
          display: "flex",
          flexDirection: "column",
          gap: 4,
        }}>
          <div style={{ fontSize: 14, fontWeight: 700, color: countdownColour }}>
            {d <= 7 ? "⚠ " : ""}Earnings in {d} calendar day{d === 1 ? "" : "s"}
          </div>
          <div style={{ fontSize: 12, color: "var(--text-dim)" }}>
            Scheduled {upcoming.date}{hourLabel}.
            {upcoming.eps_estimate != null
              ? ` Consensus EPS estimate: ${upcoming.eps_estimate >= 0 ? "+" : ""}${upcoming.eps_estimate.toFixed(2)}.`
              : ""}
            {upcoming.revenue_estimate != null
              ? ` Revenue est: $${(upcoming.revenue_estimate / 1e9).toFixed(2)}B.`
              : ""}
          </div>
          {d <= 14 && (
            <div style={{ fontSize: 11, color: countdownColour }}>
              Earnings danger zone — vol spikes are common in the 2 weeks
              before a print. Position sizing should reflect this.
            </div>
          )}
        </div>
      )}

      {isCleanWindow && (
        <div style={{
          padding: "10px 14px",
          borderRadius: 8,
          background: "rgba(58,165,109,0.07)",
          border: "1px solid rgba(58,165,109,0.25)",
        }}>
          <div style={{ fontSize: 13, fontWeight: 600, color: "var(--up)" }}>
            ✓ Clean window — next earnings {upcoming!.date} ({d}d away)
          </div>
          <div style={{ fontSize: 11, color: "var(--text-muted)", marginTop: 2 }}>
            No earnings event in the next 30 days. Reduces event-vol risk on
            any new entry. Source: Finnhub forward calendar.
          </div>
        </div>
      )}

      {noUpcomingKnown && (
        <div style={{ fontSize: 12, color: "var(--text-muted)" }}>
          No upcoming earnings date found. Either Finnhub is disabled,
          this is an ETF/index (no EPS), or no event is scheduled within
          the default calendar window.
        </div>
      )}

      {/* ---- Historical prints ---- */}
      {events.length > 0 && (
        <div>
          <div style={{ fontSize: 10, fontWeight: 700, letterSpacing: "0.07em",
            textTransform: "uppercase", color: "var(--text-muted)", marginBottom: 4 }}>
            Recent prints (last {events.length})
          </div>
          <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
            {[...events].reverse().slice(0, 5).map((e, i) => {
              const surprise = e.surprise_pct ?? null;
              const surpriseColour = surprise == null ? "var(--text-muted)"
                                   : surprise >= 5 ? "var(--up)"
                                   : surprise >= 0 ? "rgba(58,165,109,0.8)"
                                   : surprise >= -5 ? "var(--warn, #c79a2a)"
                                   : "var(--down)";
              return (
                <div key={i} style={{
                  display: "grid",
                  gridTemplateColumns: "90px 1fr 1fr",
                  alignItems: "baseline",
                  gap: 10,
                  fontSize: 12,
                  borderTop: i === 0 ? "1px solid var(--border)" : "none",
                  paddingTop: 4,
                }}>
                  <span style={{ color: "var(--text-muted)" }}>{e.date}</span>
                  <span style={{ fontVariantNumeric: "tabular-nums" }}>
                    EPS: {e.eps_actual != null ? e.eps_actual.toFixed(2) : "—"}
                    {e.eps_estimate != null && (
                      <span style={{ color: "var(--text-muted)", marginLeft: 4 }}>
                        est {e.eps_estimate.toFixed(2)}
                      </span>
                    )}
                  </span>
                  {surprise != null && (
                    <span style={{ color: surpriseColour, fontVariantNumeric: "tabular-nums", fontWeight: 600 }}>
                      {surprise >= 0 ? "+" : ""}{surprise.toFixed(1)}% surprise
                    </span>
                  )}
                </div>
              );
            })}
          </div>
        </div>
      )}

      {events.length === 0 && (
        <div style={{ fontSize: 12, color: "var(--text-muted)" }}>
          No historical earnings on file. ETFs and indices don't report EPS.
        </div>
      )}
    </section>
  );
}

// ----------------------------------------------------------------------
// Section 8 — Regime survival. How the best-fit strategy performed
// during historical stress windows (GFC, COVID, rate-shock, etc.).
// Data is already present on `row.regimes: CompareRowRegime[]` — no
// extra fetch needed. Each card shows: window name + kind (crash /
// drawdown / recovery), total return during that period, max peak-to-
// trough, and duration in trading bars.
//
// Per memory rule: every metric needs an in-tool explainer — the
// footer note and ⓘ tooltip cover "what does return_pct mean here?"
// for newcomers. Empty state also explains *why* it's empty (ETFs /
// low-vol instruments rarely trigger the stress-window filter).
// ----------------------------------------------------------------------

function SectionRegimeSurvival(props: { row: CompareRow }) {
  const regimes: CompareRowRegime[] = props.row.regimes ?? [];

  const helpText =
    "Return % = total return for this symbol during the stress window " +
    "while the strategy was active.\n" +
    "Max drop = worst peak-to-trough drawdown within the same window.\n" +
    "Bar count = duration in trading days.\n" +
    "Source: replay of best-fit strategy on historical price data. " +
    "Historical evidence only — not a prediction.";

  if (regimes.length === 0) {
    return (
      <section style={cardStyle}>
        <strong style={{ fontSize: 14 }}>
          8. Regime survival<TrustDot id="deepdive.regime_survival" />
        </strong>
        <div style={{ fontSize: 12, color: "var(--text-muted)" }}>
          No historical stress windows on file for this symbol's strategy.
          ETFs tracking low-volatility or cash-like indices rarely trigger
          the crash / drawdown / recovery filter. This is a data state,
          not an error.
        </div>
      </section>
    );
  }

  return (
    <section style={cardStyle}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline" }}>
        <strong style={{ fontSize: 14 }}>
          8. Regime survival<TrustDot id="deepdive.regime_survival" />
        </strong>
        <span
          title={helpText}
          style={{ fontSize: 11, color: "var(--text-muted)", cursor: "help" }}
        >
          {regimes.length} window{regimes.length === 1 ? "" : "s"} ⓘ
        </span>
      </div>

      <div style={{ fontSize: 11, color: "var(--text-muted)", fontStyle: "italic" }}>
        How this symbol's best strategy performed during past stress windows.
        Historical evidence — not a prediction of future performance.
      </div>

      <div style={{
        display: "grid",
        gridTemplateColumns: "repeat(auto-fit, minmax(155px, 1fr))",
        gap: 10,
        marginTop: 4,
      }}>
        {regimes.map((r) => {
          const colour = regimeColourDD(r.kind);
          const kindBg = r.kind === "crash" ? "rgba(214,76,76,0.07)"
                       : r.kind === "recovery" ? "rgba(58,165,109,0.07)"
                       : "rgba(199,154,42,0.07)";
          const retColour = r.return_pct == null ? "var(--text-muted)"
                          : r.return_pct >= 0 ? "var(--up)" : "var(--down)";
          const ret = r.return_pct == null ? "—"
                    : `${r.return_pct >= 0 ? "+" : ""}${r.return_pct.toFixed(1)}%`;
          const dd = r.max_drawdown_pct == null ? "—"
                   : `${r.max_drawdown_pct.toFixed(1)}%`;
          return (
            <div
              key={r.key}
              title={helpText}
              style={{
                borderLeft: `3px solid ${colour}`,
                padding: "8px 8px 8px 10px",
                background: kindBg,
                borderRadius: "0 6px 6px 0",
              }}
            >
              <div style={{ fontSize: 12, fontWeight: 700, color: "var(--text)", marginBottom: 4, lineHeight: 1.2 }}>
                {r.name}
              </div>
              <div style={{
                display: "inline-block",
                fontSize: 9,
                padding: "1px 5px",
                borderRadius: 3,
                border: `1px solid ${colour}`,
                color: colour,
                marginBottom: 6,
                textTransform: "uppercase",
                letterSpacing: "0.07em",
                fontWeight: 600,
              }}>
                {r.kind}
              </div>
              <div style={{ display: "flex", flexDirection: "column", gap: 3, fontSize: 11, fontVariantNumeric: "tabular-nums" }}>
                <div>
                  <span style={{ color: "var(--text-muted)" }}>Return </span>
                  <strong style={{ color: retColour }}>{ret}</strong>
                </div>
                <div>
                  <span style={{ color: "var(--text-muted)" }}>Max drop </span>
                  <strong style={{ color: "var(--down)" }}>{dd}</strong>
                </div>
                <div style={{ color: "var(--text-muted)" }}>
                  {r.bars} bar{r.bars === 1 ? "" : "s"}
                </div>
              </div>
            </div>
          );
        })}
      </div>

      <div style={{ fontSize: 10, color: "var(--text-muted)", marginTop: 4 }}>
        Return = total return during the window while strategy was active ·
        Max drop = worst peak-to-trough ·
        Bars = trading-day duration ·
        Source: best-fit strategy replay on historical prices.
      </div>
    </section>
  );
}

/** Kind → border/label colour for regime survival cards. Mirrors the
 *  same logic in Compare.tsx (kept private here to avoid a shared-
 *  colour-utility dependency at this MVP stage). */
function regimeColourDD(kind: string): string {
  switch (kind) {
    case "crash":    return "var(--down)";
    case "drawdown": return "var(--warn, #c79a2a)";
    case "recovery": return "var(--up)";
    default:         return "var(--text-dim)";
  }
}

// ----------------------------------------------------------------------
// Section 9 — Peer comparison. Other symbols from the same trading
// universe, ranked so those agreeing with this symbol's bucket appear
// first (then by Sharpe desc). This surfaces: "BABA is BUY → which
// other symbols in this universe are also BUY, and which are AVOID?"
//
// Design notes:
//   • "Peer" = universe co-membership, NOT analyst-defined sector peer.
//     The two can diverge heavily (GLD shares a universe with AAPL in
//     some multi-asset runs). The explainer note calls this out.
//   • No extra API call: peerRows is computed in SymbolDeepDive from
//     the same compareLatest response used for Sections 1–8.
//   • Shows max 8 cards (grid auto-fit 140px). Large universes (S&P500)
//     surface the most strategy-aligned candidates, not all 500.
//   • Bucket colour chips reuse bucketChipStyle from Section 2.
// ----------------------------------------------------------------------

function SectionPeerComparison(props: {
  symbol: string;
  row: CompareRow;
  peerRows: CompareRow[];
}) {
  const { symbol, row, peerRows } = props;
  const bucket = row.bucket ?? "WAIT";

  if (peerRows.length === 0) {
    return (
      <section style={cardStyle}>
        <strong style={{ fontSize: 14 }}>
          9. Peer comparison<TrustDot id="deepdive.peer_comparison" />
        </strong>
        <div style={{ fontSize: 12, color: "var(--text-muted)" }}>
          {symbol} is the only symbol in this universe, or the universe
          has not yet been computed. Peer comparison requires ≥ 2 symbols
          in the same cached run.
        </div>
      </section>
    );
  }

  // Rank: same-bucket rows first, then by Sharpe desc, cap at 8.
  const sharpeOf = (r: CompareRow) =>
    (r.stats?.sharpe as number | null | undefined) ?? -Infinity;
  const ranked = [...peerRows]
    .sort((a, b) => {
      const aBucket = (a.bucket ?? "WAIT") === bucket ? 1 : 0;
      const bBucket = (b.bucket ?? "WAIT") === bucket ? 1 : 0;
      if (bBucket !== aBucket) return bBucket - aBucket;
      return sharpeOf(b) - sharpeOf(a);
    })
    .slice(0, 8);

  const sameBucketCount = ranked.filter((r) => (r.bucket ?? "WAIT") === bucket).length;

  return (
    <section style={cardStyle}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline" }}>
        <strong style={{ fontSize: 14 }}>
          9. Peer comparison<TrustDot id="deepdive.peer_comparison" />
        </strong>
        <span style={{ fontSize: 11, color: "var(--text-muted)" }}>
          {peerRows.length} in universe · showing top {ranked.length}
        </span>
      </div>

      <div style={{ fontSize: 11, color: "var(--text-muted)", fontStyle: "italic" }}>
        Other symbols in the same trading universe, ranked by bucket agreement
        then Sharpe. Co-membership is not sector similarity — multi-asset
        universes will mix equities, ETFs, and commodities.
      </div>

      {sameBucketCount > 0 && (
        <div style={{ fontSize: 11, color: "var(--text-muted)" }}>
          {sameBucketCount} also {bucket} (agreeing with this symbol's consensus)
        </div>
      )}

      <div style={{
        display: "grid",
        gridTemplateColumns: "repeat(auto-fill, minmax(138px, 1fr))",
        gap: 8,
        marginTop: 4,
      }}>
        {ranked.map((peer) => {
          const peerBucket = peer.bucket ?? "WAIT";
          const chipStyle = bucketChipStyle(peerBucket);
          const pSharpe = sharpeOf(peer);
          const pInPosition = peer.in_position;
          const bucketAgrees = peerBucket === bucket;
          return (
            <Link
              key={peer.symbol}
              to={`/symbol/${peer.symbol}`}
              style={{
                textDecoration: "none",
                color: "inherit",
                padding: 10,
                border: `1px solid ${bucketAgrees ? "rgba(255,255,255,0.15)" : "var(--border)"}`,
                borderRadius: 8,
                display: "flex",
                flexDirection: "column",
                gap: 4,
                background: bucketAgrees
                  ? "var(--bg-elevated, rgba(255,255,255,0.03))"
                  : "transparent",
                transition: "border-color 0.15s",
              }}
              title={`Open ${peer.symbol} deep dive`}
            >
              <div style={{ fontSize: 14, fontWeight: 700, letterSpacing: "0.5px" }}>
                {peer.symbol}
              </div>
              <div style={{
                ...chipStyle,
                fontSize: 10,
                fontWeight: 700,
                padding: "2px 6px",
                borderRadius: 4,
                alignSelf: "flex-start",
              }}>
                {peerBucket}
              </div>
              <div style={{ fontSize: 10, color: "var(--text-muted)", fontVariantNumeric: "tabular-nums" }}>
                Sharpe {pSharpe === -Infinity ? "—" : pSharpe.toFixed(2)}
              </div>
              <div style={{ fontSize: 10, color: pInPosition ? "var(--up)" : "var(--text-muted)" }}>
                {pInPosition ? "● LONG" : "○ flat"}
              </div>
            </Link>
          );
        })}
      </div>

      <div style={{ fontSize: 10, color: "var(--text-muted)", marginTop: 4 }}>
        Click any card to open that symbol's Deep Dive. Peers ranked
        by bucket agreement with {symbol} first, then by Sharpe (best
        historical risk-adjusted return in this universe's backtest).
      </div>
    </section>
  );
}

// ----------------------------------------------------------------------
// Section 10 — Hit rate. Per-strategy historical accuracy on this
// specific symbol: out of N past signal firings, how many were
// profitable. Strategies with hit rate < 50% get a "worse than coin
// flip on this symbol" warning chip — informational, not exclusionary.
//
// Implementation note: get_hitrate is one POST per (symbol, strategy)
// combo. Firing them in parallel via Promise.all on mount. Results
// stream in independently so the section paints incrementally. Each
// row that finishes loading replaces its skeleton with the real bar.
// ----------------------------------------------------------------------

interface HitRateState {
  loading: boolean;
  result?: HitRateResult;
  error?: string;
}

function SectionHitRate(props: { symbol: string; rows: CompareRow[] }) {
  const { symbol, rows } = props;
  const [states, setStates] = useState<Record<string, HitRateState>>({});

  useEffect(() => {
    let cancelled = false;
    // Only fetch for non-excluded strategies — excluded ones already
    // have a "factor-fit mismatch" callout in Section 4; running their
    // historical hit rate adds noise, not signal.
    const targets = rows.filter((r) => !r.excluded_for_fit);
    setStates(Object.fromEntries(
      targets.map((r) => [r.strategy, { loading: true }])
    ));
    Promise.all(targets.map(async (r) => {
      try {
        const result = await api.hitRate({
          symbol,
          strategy: r.strategy,
          lookbackYears: 5,
          params: null,
        });
        if (!cancelled) {
          setStates((s) => ({ ...s, [r.strategy]: { loading: false, result } }));
        }
      } catch (e) {
        if (!cancelled) {
          setStates((s) => ({ ...s, [r.strategy]: { loading: false, error: String(e) } }));
        }
      }
    }));
    return () => { cancelled = true; };
  }, [symbol, rows]);

  const sortedRows = [...rows].filter((r) => !r.excluded_for_fit);
  // Sort by Sharpe desc to align with Section 4.
  sortedRows.sort((a, b) =>
    ((b.stats?.sharpe as number | null) ?? -Infinity)
    - ((a.stats?.sharpe as number | null) ?? -Infinity));

  const completed = Object.values(states).filter((s) => !s.loading).length;
  const total = sortedRows.length;
  const subClipFlip = sortedRows.filter((r) => {
    const s = states[r.strategy]?.result;
    return s && s.winRatePct < 50;
  }).length;

  return (
    <section style={cardStyle}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline" }}>
        <strong style={{ fontSize: 14 }}>
          10. Hit rate<TrustDot id="deepdive.hit_rate" />
          <span style={{ marginLeft: 8, fontSize: 11, fontWeight: 400, color: "var(--text-muted)" }}>
            (5y lookback)
          </span>
        </strong>
        {completed < total ? (
          <span style={{ fontSize: 11, color: "var(--text-muted)" }}>
            loading {completed}/{total}…
          </span>
        ) : subClipFlip > 0 ? (
          <span style={{ fontSize: 11, color: "var(--warn, #c79a2a)" }}>
            ⚠ {subClipFlip} strategy{subClipFlip === 1 ? "" : "ies"} sub-coin-flip on this symbol
          </span>
        ) : (
          <span style={{ fontSize: 11, color: "var(--text-muted)" }}>
            all strategies ≥ 50%
          </span>
        )}
      </div>
      <div style={{ display: "flex", flexDirection: "column", gap: 2, marginTop: 4 }}>
        {sortedRows.map((r, i) => (
          <HitRateRow key={r.strategy}
                      strategy={r.strategy}
                      label={r.strategy_label || r.strategy}
                      state={states[r.strategy] ?? { loading: true }}
                      index={i} />
        ))}
      </div>
      <div style={{ fontSize: 11, color: "var(--text-muted)", marginTop: 4 }}>
        "Profitable" = next signal in the strategy's natural cycle closed at a positive return.
        Sub-50% = the strategy historically loses more often than it wins on this specific
        symbol — informational, NOT a reason to drop the row from the vote.
      </div>
    </section>
  );
}

function HitRateRow(props: {
  strategy: string;
  label: string;
  state: HitRateState;
  index: number;
}) {
  const { label, state, index } = props;
  const res = state.result;
  const subFlip = res != null && res.winRatePct < 50;
  const barColor = res == null ? "var(--border)"
                : res.winRatePct >= 60 ? "var(--up)"
                : res.winRatePct >= 50 ? "rgba(58, 165, 109, 0.6)"
                : res.winRatePct >= 40 ? "var(--warn, #c79a2a)"
                : "var(--down)";
  return (
    <div style={{
      borderTop: index === 0 ? "none" : "1px solid var(--border)",
      padding: "6px 0",
    }}>
      <div style={{
        display: "grid",
        gridTemplateColumns: "1.4fr 1.6fr 0.5fr",
        alignItems: "center",
        gap: 10,
        fontSize: 12,
      }}>
        <span>{label}</span>
        {state.loading && (
          <span style={{ color: "var(--text-muted)", fontSize: 11 }}>computing…</span>
        )}
        {state.error && (
          <span style={{ color: "var(--down)", fontSize: 11 }}>err: {state.error.slice(0, 60)}</span>
        )}
        {res && (
          <span style={{ display: "flex", alignItems: "center", gap: 6 }}>
            <span style={{ color: "var(--text-muted)", fontSize: 11 }}>
              {res.winners} of {res.totalTrades} signals
            </span>
            <span style={{
              flex: 1,
              height: 6,
              background: "var(--border)",
              borderRadius: 999,
              overflow: "hidden",
              minWidth: 40,
            }}>
              <div style={{
                width: `${Math.max(0, Math.min(100, res.winRatePct))}%`,
                height: "100%",
                background: barColor,
              }} />
            </span>
          </span>
        )}
        <span style={{
          textAlign: "right",
          fontVariantNumeric: "tabular-nums",
          color: subFlip ? "var(--warn, #c79a2a)" : "var(--text)",
          fontWeight: 600,
        }}>
          {res ? `${res.winRatePct.toFixed(0)}%` : "—"}
          {subFlip && <span style={{ marginLeft: 4 }}>⚠</span>}
        </span>
      </div>
    </div>
  );
}

function sentimentChip(score: number | null) {
  if (score == null) return { label: "—", bg: "var(--border)", fg: "var(--text-muted)" };
  const label = (score >= 0 ? "+" : "") + score.toFixed(2);
  if (score > 0.3) return { label, bg: "rgba(58, 165, 109, 0.18)", fg: "var(--up)" };
  if (score < -0.3) return { label, bg: "rgba(214, 76, 76, 0.18)", fg: "var(--down)" };
  return { label, bg: "rgba(199, 154, 42, 0.18)", fg: "var(--warn, #c79a2a)" };
}

function isStaleEntry(positionSince: string | null): boolean {
  if (!positionSince) return false;
  const d = new Date(positionSince);
  if (Number.isNaN(d.getTime())) return false;
  const ageMs = Date.now() - d.getTime();
  const oneYearMs = 365 * 24 * 60 * 60 * 1000;
  return ageMs > oneYearMs;
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

function Section(props: { title: string; todo: string; trustId?: string }) {
  return (
    <section style={{
      ...cardStyle,
      borderStyle: "dashed",
      background: "transparent",
    }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline" }}>
        <strong style={{ fontSize: 14 }}>
          {props.title}
          {props.trustId && <TrustDot id={props.trustId} />}
        </strong>
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

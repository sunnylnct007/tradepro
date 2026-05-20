import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { api } from "../api/client";
import type {
  CompareLatestResponse,
  CompareNewsItem,
  CompareRow,
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
              setAllRows(match);
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
               allRows={allRows} universe={universe} />
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
  universe?: string | null;
  detail?: string;
}) {
  const { symbol, state, row, allRows = [], universe, detail } = props;
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

      {state === "ready" && row && (
        <SectionVerdict row={row} allRows={allRows} />
      )}
      {state === "ready" && row && (
        <SectionDecisionTrace trace={row.market_state?.decision_trace ?? []} />
      )}
      {state === "ready" && row && (
        <SectionStrategyVote row={row} allRows={allRows} />
      )}
      {state === "ready" && row && (
        <SectionNews
          items={row.news ?? []}
          summary={row.sentiment_summary}
          newsVia={row.news_via ?? null}
        />
      )}
      {state === "ready" && row && (
        <SectionAnalyst row={row} />
      )}
      {state === "ready" && row && (
        <SectionEarnings row={row} />
      )}
      <Section title="8. Regime survival" todo="get_regime_history(symbol, strategy=best_long) — needs task #66 backend prep" />
      <Section title="9. Peer comparison" todo="derive peer set from symbol.tags — needs task #66 backend prep" />
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
        }}>{bucket}</div>
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
          3. Decision trace
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
        <strong style={{ fontSize: 14 }}>4. Strategy vote</strong>
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
}) {
  const { items, summary, newsVia } = props;
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
        <strong style={{ fontSize: 14 }}>5. News + sentiment</strong>
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
          5. News + sentiment
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
      <div style={{ display: "flex", flexDirection: "column", gap: 2, marginTop: 4 }}>
        {items.map((item, i) => (
          <NewsRow key={i} item={item} index={i} />
        ))}
      </div>
    </section>
  );
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
        <strong style={{ fontSize: 14 }}>6. Analyst consensus</strong>
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
          6. Analyst consensus
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
  // We don't currently surface UPCOMING earnings on the compare row —
  // historical_earnings is the past 5y of reported prints. Calling
  // out the limitation honestly here; the proper data source is the
  // get_earnings_calendar MCP tool / Finnhub forward calendar.
  // TODO(task #66): wire forward-looking earnings into the compare row
  // alongside historical so this section can show "T+23 days to next
  // print" without an extra API call.
  const lastReported = events[events.length - 1];
  return (
    <section style={cardStyle}>
      <strong style={{ fontSize: 14 }}>7. Event risk (earnings)</strong>
      <div style={{ fontSize: 12, color: "var(--text-muted)" }}>
        Forward earnings calendar lives on the get_earnings_calendar MCP tool
        but isn't yet folded into the compare-row payload. Showing latest
        reported below; full forward-looking countdown lands with task #66.
      </div>
      {lastReported && (
        <div style={{ fontSize: 12, marginTop: 4 }}>
          Last reported: <strong>{lastReported.date}</strong>
          {lastReported.surprise_pct != null && (
            <span style={{ marginLeft: 8, color: lastReported.surprise_pct >= 0 ? "var(--up)" : "var(--down)" }}>
              {lastReported.surprise_pct >= 0 ? "+" : ""}{lastReported.surprise_pct.toFixed(1)}% surprise
            </span>
          )}
        </div>
      )}
      {events.length === 0 && (
        <div style={{ fontSize: 12, color: "var(--text-muted)" }}>
          No reported earnings on file. ETFs and indices don't have them.
        </div>
      )}
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
          10. Hit rate
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

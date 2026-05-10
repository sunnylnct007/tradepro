import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api/client";
import type {
  CompareError,
  CompareExternalConsensus,
  CompareFundamentals,
  CompareLatestResponse,
  CompareLlmInfo,
  CompareMarketContext,
  CompareNewsItem,
  CompareRationale,
  CompareRow,
  CompareUniverseSummary,
  DecisionCheck,
  EntrySignal,
} from "../api/types";
import { GemsCard } from "../components/GemsCard";
import { HoldingsHealthCard } from "../components/HoldingsHealthCard";
import { Info } from "../components/Info";
import { PriceHistoryChart } from "../components/PriceHistoryChart";
import { RiskPill } from "../components/RiskPill";
import { WorkerStatusBadge } from "../components/WorkerStatusBadge";

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
  /** True when the bucket would have been BUY by price + strategy
   * consensus, but was demoted to WAIT because of sentiment data. The
   * UI surfaces this explicitly so the user sees the rule fire. */
  sentimentDemoted?: boolean;
  sentimentDemotionReason?: string;
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

  const views: SymbolView[] = useMemo(
    () => buildSymbolViews(
      data?.payload?.rows ?? [],
      data?.payload?.llm?.demotion_rule,
    ),
    [data],
  );
  const buys = views.filter((v) => v.bucket === "BUY");
  const waits = views.filter((v) => v.bucket === "WAIT");
  const avoids = views.filter((v) => v.bucket === "AVOID");
  const rankMetric = data?.rankMetric ?? data?.payload?.rank_metric ?? "sharpe";

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 20 }}>
      <div>
        <h1 style={{ margin: 0, fontSize: 24 }}>Should I invest today?</h1>
        <p style={{ color: "var(--text-dim)", margin: "6px 0 0 0", maxWidth: 820 }}>
          For long-horizon (months-to-years) investing. Each asset in the
          selected universe — ETF (e.g. <code>etf_us_core</code>) or
          single-stock basket (e.g. <code>us_megacap_sample</code>) —
          lands in <strong style={{ color: "var(--up)" }}>BUY today</strong>,{" "}
          <strong style={{ color: "var(--neutral)" }}>WAIT</strong>, or{" "}
          <strong style={{ color: "var(--down)" }}>AVOID</strong> based on the
          combination of (a) price action — uptrend, RSI, drawdown — and
          (b) how many of the 5 strategies are currently long the asset.
          The rule chain is identical for ETFs and stocks; ETF-specific
          fundamentals (expense ratio, AUM, top holdings) only show when
          they apply.
        </p>
      </div>

      <div style={{ display: "flex", gap: 12, alignItems: "center", flexWrap: "wrap" }}>
        <WorkerStatusBadge />
        <span style={{ flex: 1 }} />
      </div>

      <ProvenanceBar data={data} loading={loading} />

      {data?.payload?.market_context && (
        <MarketContextBar ctx={data.payload.market_context} />
      )}

      {data?.payload?.llm && <LlmStatusBar llm={data.payload.llm} />}

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
            <Stat label="Assets × strategies" value={`${views.length} × ${views[0]?.total ?? 0}`} />
          </>
        )}
      </section>

      {error && <EmptyState error={error} />}

      {data?.payload?.currency_mix?.is_mixed && (
        <CurrencyMixWarning
          currencies={data.payload.currency_mix.currencies}
        />
      )}

      {data?.payload?.errors && data.payload.errors.length > 0 && (
        <DataIssuesPanel errors={data.payload.errors} />
      )}

      {data && views.length > 0 && (
        <>
          <VerdictHeadline
            buys={buys}
            waits={waits}
            avoids={avoids}
            rankMetric={rankMetric}
          />
          <HoldingsHealthCard />
          <GemsCard />
          <StrategyMatrix
            views={views}
            strategies={data.payload.strategies}
            rankMetric={rankMetric}
            openSymbol={openSymbol}
            setOpen={setOpenSymbol}
            showCurrency={data.payload.currency_mix?.is_mixed ?? false}
          />
        </>
      )}
    </div>
  );
}

// --------------------------------------------------------------------------
// Bucket assignment + per-symbol aggregation
// --------------------------------------------------------------------------

function buildSymbolViews(
  rows: CompareRow[],
  demotionRule: CompareLlmInfo["demotion_rule"] | undefined,
): SymbolView[] {
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

    // Sentiment demotion. Pure data + thresholds out of the payload —
    // no hidden behaviour. The user can read demotionRule.description
    // in the LLM badge to see the exact rule that ran.
    let demoted = false;
    let demotionReason: string | undefined;
    if (bucket === "BUY" && demotionRule) {
      const s = best.sentiment_summary;
      if (s && s.mean_sentiment !== null
          && s.mean_sentiment <= demotionRule.mean_sentiment_threshold
          && s.material_negative_count >= demotionRule.min_material_negative_count) {
        demoted = true;
        demotionReason =
          `Sentiment demotion: 7d mean ${s.mean_sentiment.toFixed(2)} ` +
          `≤ threshold ${demotionRule.mean_sentiment_threshold} ` +
          `AND ${s.material_negative_count} material-negative headlines ` +
          `(threshold ≥ ${demotionRule.min_material_negative_count}).`;
        bucket = "WAIT";
        reason = demotionReason;
      }
    }

    views.push({
      symbol, rows: sorted, bestRow: best,
      marketSignal: priceVerdict, marketReason: ms?.entry_reason ?? "",
      longCount, total, bucket, bucketReason: reason,
      sentimentDemoted: demoted,
      sentimentDemotionReason: demotionReason,
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

  // Freshness traffic light:
  //   <24h  green ● Live  — fresh, act on it
  //   <72h  amber ● Stale — still recent enough, but refresh
  //   >=72h red   ● Very stale — refresh before deciding
  const ageHr = ageMin / 60;
  const tone =
    ageHr < 24 ? "fresh" : ageHr < 72 ? "stale" : "very_stale";
  const colour =
    tone === "fresh" ? "var(--up)" : tone === "stale" ? "var(--neutral)" : "var(--down)";
  const label =
    tone === "fresh" ? "● Live" : tone === "stale" ? "● Stale" : "● Very stale";
  const message =
    tone === "fresh"
      ? <>Real Yahoo Finance prices, computed in Python locally <strong style={{ color: "var(--text)" }}>{ageStr}</strong>.</>
      : tone === "stale"
        ? <>Last computed <strong style={{ color: "var(--text)" }}>{ageStr}</strong> — recent but a refresh is recommended before acting.</>
        : <>Last computed <strong style={{ color: "var(--text)" }}>{ageStr}</strong> — <strong style={{ color: "var(--down)" }}>refresh before deciding</strong>.</>;

  return (
    <div
      className="card"
      style={{
        display: "flex",
        gap: 14,
        flexWrap: "wrap",
        alignItems: "center",
        borderLeft: `3px solid ${colour}`,
        padding: "10px 14px",
      }}
    >
      <span
        style={{
          fontSize: 11,
          fontWeight: 700,
          color: colour,
          letterSpacing: "0.06em",
          textTransform: "uppercase",
        }}
      >
        {label}
      </span>
      <span style={{ fontSize: 12, color: "var(--text-dim)" }}>{message}</span>
      {tone !== "fresh" && (
        <code
          style={{
            fontSize: 11,
            padding: "3px 6px",
            background: "rgba(0,0,0,0.3)",
            borderRadius: 4,
            color: "var(--text-dim)",
          }}
          title="Run this on the Strategy Engine to push fresh data"
        >
          uv run tradepro-compare --watchlist {data.universe} --push
        </code>
      )}
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

function LlmStatusBar({ llm }: { llm: CompareLlmInfo }) {
  const colour = llm.healthy ? "var(--up)" : "var(--down)";
  return (
    <section
      className="card"
      style={{
        padding: "8px 12px",
        borderLeft: `3px solid ${colour}`,
        fontSize: 12,
        display: "flex",
        gap: 14,
        flexWrap: "wrap",
        alignItems: "center",
      }}
    >
      <span style={{ display: "flex", gap: 6, alignItems: "center" }}>
        <span style={{
          display: "inline-block", width: 8, height: 8, borderRadius: 4,
          background: colour,
        }} />
        <strong style={{ color: "var(--text)", fontSize: 11, letterSpacing: "0.05em", textTransform: "uppercase" }}>
          LLM
        </strong>
      </span>
      <span style={{ color: "var(--text-dim)" }}>
        {llm.healthy ? (
          <>Sentiment scoring by <code>{llm.provider}</code> /{" "}
          <code>{llm.model}</code> · prompt {llm.prompt_version}</>
        ) : (
          <>LLM unavailable — sentiment is <em>not</em> influencing
            today's verdicts. Verdicts ran on price + strategy rules only.</>
        )}
      </span>
      {llm.healthy && llm.telemetry && (
        <span
          style={{
            color: "var(--text-muted)",
            fontSize: 11,
            fontFamily: "ui-monospace, SFMono-Regular, Menlo, monospace",
          }}
          title={
            `attempted ${llm.telemetry.calls_attempted}, ` +
            `cache hits ${llm.telemetry.cache_hits}, ` +
            `failures ${llm.telemetry.calls_failed}, ` +
            `max latency ${llm.telemetry.max_latency_ms}ms`
          }
        >
          {llm.telemetry.total_scored} scored ·{" "}
          {llm.telemetry.cache_hits} cached ·{" "}
          {llm.telemetry.avg_latency_ms ?? 0}ms avg
          {llm.telemetry.calls_failed > 0 && (
            <span style={{ color: "var(--down)" }}>
              {" "}· {llm.telemetry.calls_failed} failed
            </span>
          )}
        </span>
      )}
      {llm.healthy && (
        <span style={{
          marginLeft: "auto", color: "var(--text-muted)", fontSize: 11,
          maxWidth: 360,
        }}>
          {llm.demotion_rule.description}
        </span>
      )}
    </section>
  );
}

function CurrencyMixWarning({ currencies }: { currencies: string[] }) {
  return (
    <div
      className="card"
      style={{
        borderLeft: "3px solid var(--neutral)",
        padding: "8px 12px",
        fontSize: 12,
        color: "var(--text-dim)",
      }}
    >
      <strong style={{ color: "var(--text)" }}>Mixed currency universe.</strong>{" "}
      Rows trade in {currencies.join(" + ")}. Sharpe, CAGR % and max-DD % are
      currency-neutral so the ranking is honest, but absolute fees and
      portfolio sizing differ — read the currency tag on each row before
      comparing positions you'd actually take.
    </div>
  );
}

function DataIssuesPanel({ errors }: { errors: CompareError[] }) {
  return (
    <details
      className="card"
      style={{ borderLeft: "3px solid var(--down)", padding: "8px 12px" }}
    >
      <summary style={{ cursor: "pointer", color: "var(--down)", fontWeight: 600, fontSize: 12 }}>
        {errors.length} symbol{errors.length === 1 ? "" : "s"} couldn't be priced
      </summary>
      <ul style={{ margin: "6px 0 0 0", paddingLeft: 16, fontSize: 12, color: "var(--text-dim)" }}>
        {errors.map((e, i) => (
          <li key={i}>
            <code style={{ color: "var(--text)" }}>{e.symbol}</code>{" "}
            <span style={{ color: "var(--text-muted)" }}>({e.stage})</span> — {e.error}
          </li>
        ))}
      </ul>
    </details>
  );
}

/** One row per ETF, one column per strategy. Cell = is that strategy
 * currently long this ETF? Plus a Vote column (count/total) and a
 * Verdict column (BUY / WAIT / AVOID — the bucket assignment). Rows
 * are sorted: BUY first (best ranked), then WAIT, then AVOID. Click a
 * row to expand the decision trace + regime evidence. */
function StrategyMatrix({
  views,
  strategies,
  rankMetric,
  openSymbol,
  setOpen,
  showCurrency,
}: {
  views: SymbolView[];
  strategies: { name: string; label: string }[];
  rankMetric: string;
  openSymbol: string | null;
  setOpen: (s: string | null) => void;
  showCurrency: boolean;
}) {
  const bucketOrder = (b: SymbolView["bucket"]) =>
    b === "BUY" ? 0 : b === "WAIT" ? 1 : 2;
  const ordered = [...views].sort((a, b) => {
    const ba = bucketOrder(a.bucket);
    const bb = bucketOrder(b.bucket);
    if (ba !== bb) return ba - bb;
    return (a.bestRow.rank ?? 1e9) - (b.bestRow.rank ?? 1e9);
  });

  const stratHeader = (label: string) => {
    const short = label
      .replace(/_/g, " ")
      .replace(/Buy & Hold/i, "B&H")
      .replace(/SMA crossover/i, "SMA")
      .replace(/RSI mean-reversion/i, "RSI")
      .replace(/MACD signal-cross/i, "MACD")
      .replace(/Donchian breakout/i, "Donch");
    return short;
  };

  return (
    <section className="card" style={{ padding: 0, overflow: "hidden" }}>
      <div
        style={{
          padding: "10px 14px",
          borderBottom: "1px solid var(--border)",
          display: "flex",
          gap: 12,
          alignItems: "baseline",
          flexWrap: "wrap",
        }}
      >
        <strong style={{ fontSize: 13 }}>Strategies vote on each asset</strong>
        <span style={{ fontSize: 11, color: "var(--text-muted)" }}>
          Cell = is the strategy currently long this asset (last fired BUY newer than its last SELL)?
          Click a row to see why and the regime history.
        </span>
      </div>
      <div style={{ overflowX: "auto" }}>
        <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 12 }}>
          <thead>
            <tr style={{ background: "var(--bg-hover)", color: "var(--text-dim)", textAlign: "left" }}>
              <Th>Symbol</Th>
              {showCurrency && <Th align="center">Ccy</Th>}
              {strategies.map((s) => (
                <Th key={s.name} align="center" title={s.label}>
                  {stratHeader(s.label)}
                </Th>
              ))}
              <Th align="center" help="strategy_vote">Vote</Th>
              <Th align="center" help="entry_signal">Verdict</Th>
              <Th align="center" help="swing_score">Swing</Th>
              <Th align="right" help={rankMetric === "sharpe" ? "sharpe" : rankMetric === "cagr_pct" ? "cagr" : undefined}>
                Best {rankMetric}
              </Th>
            </tr>
          </thead>
          <tbody>
            {ordered.map((v) => {
              const open = openSymbol === v.symbol;
              return (
                <MatrixRow
                  key={v.symbol}
                  view={v}
                  strategies={strategies}
                  rankMetric={rankMetric}
                  open={open}
                  onToggle={() => setOpen(open ? null : v.symbol)}
                  showCurrency={showCurrency}
                />
              );
            })}
          </tbody>
        </table>
      </div>
    </section>
  );
}

function MatrixRow({
  view,
  strategies,
  rankMetric,
  open,
  onToggle,
  showCurrency,
}: {
  view: SymbolView;
  strategies: { name: string; label: string }[];
  rankMetric: string;
  open: boolean;
  onToggle: () => void;
  showCurrency: boolean;
}) {
  const verdictColour = bucketColour(view.bucket);
  const cellByStrategy = new Map(view.rows.map((r) => [r.strategy, r] as const));
  const ccy = view.bestRow.currency;
  const dataAge = view.bestRow.data_age_days ?? 0;
  const isStale = dataAge >= 7;
  return (
    <>
      <tr
        style={{ cursor: "pointer", borderTop: "1px solid var(--border)" }}
        onClick={onToggle}
      >
        <Td>
          <Link
            to={`/signals?symbol=${encodeURIComponent(view.symbol)}`}
            style={{ color: "var(--text)", fontWeight: 600 }}
            onClick={(e) => e.stopPropagation()}
          >
            {view.symbol}
          </Link>
          {isStale && (
            <span
              title={`Latest price is ${dataAge} days behind — verdict uses possibly stale data`}
              style={{
                marginLeft: 6,
                fontSize: 10,
                color: "var(--down)",
                background: "rgba(255,80,80,0.1)",
                padding: "1px 5px",
                borderRadius: 3,
              }}
            >
              {dataAge}d stale
            </span>
          )}
        </Td>
        {showCurrency && (
          <Td align="center">
            <span style={{ fontSize: 10, color: "var(--text-muted)" }}>{ccy ?? "—"}</span>
          </Td>
        )}
        {strategies.map((s) => {
          const row = cellByStrategy.get(s.name);
          return (
            <Td key={s.name} align="center">
              <StrategyCell row={row} />
            </Td>
          );
        })}
        <Td align="center">
          <span
            style={{ color: verdictColour, fontWeight: 700 }}
            title={`${view.longCount} of ${view.total} strategies currently long`}
          >
            {view.longCount}/{view.total}
          </span>
        </Td>
        <Td align="center" style={{ color: verdictColour, fontWeight: 700 }}>
          <div style={{ display: "flex", flexDirection: "column", alignItems: "center", gap: 3 }}>
            <span>{view.bucket}</span>
            <RiskPill rating={view.bestRow.risk_rating ?? null} />
          </div>
        </Td>
        <Td align="center">
          <SwingBadge swing={view.bestRow.swing_score ?? null} />
        </Td>
        <Td align="right" className="num">
          {fmtNum(view.bestRow.stats?.[rankMetric])}
        </Td>
      </tr>
      {open && (
        <tr style={{ background: "var(--bg-hover)" }}>
          <td colSpan={strategies.length + 5 + (showCurrency ? 1 : 0)} style={{ padding: 12 }}>
            <ExpandedDetail view={view} />
          </td>
        </tr>
      )}
    </>
  );
}

function StrategyCell({ row }: { row?: CompareRow }) {
  if (!row) return <span style={{ color: "var(--text-muted)" }}>—</span>;
  if (row.in_position) {
    return (
      <span
        style={{ color: "var(--up)", fontWeight: 600 }}
        title={`Long since ${row.position_since?.slice(0, 10) ?? "—"}`}
      >
        ● LONG
      </span>
    );
  }
  return (
    <span style={{ color: "var(--text-muted)" }} title="Strategy is currently flat (not holding this asset)">
      ○ flat
    </span>
  );
}

function ExpandedDetail({ view }: { view: SymbolView }) {
  const baseTrace = view.bestRow.market_state?.decision_trace ?? [];
  const consensus = view.bestRow.external_consensus;
  const fundamentals = view.bestRow.fundamentals;
  const news = view.bestRow.news ?? [];
  const rationale = view.bestRow.rationale;
  // Append the sentiment check to the trace so the rules ladder shows
  // the LLM-derived signal alongside the price-based ones.
  const trace = [...baseTrace, sentimentCheck(view)];
  return (
    <div style={{ marginTop: 8, padding: 10, background: "rgba(0,0,0,0.18)", borderRadius: 6 }}>
      {/* Price history first so the user has visual context for
          everything below. Split-adjusted line + SMA(200) + 52w
          high/low reference levels. The numbers in the rationale
          and the rule-chain table all map onto this chart. */}
      <PriceHistoryChart symbol={view.symbol} />
      {rationale && <RationalePanel rationale={rationale} />}
      {view.sentimentDemoted && view.sentimentDemotionReason && (
        <div
          style={{
            marginBottom: 10,
            padding: "8px 10px",
            borderLeft: "3px solid var(--neutral)",
            background: "rgba(255,200,80,0.06)",
            fontSize: 12,
            color: "var(--text)",
          }}
        >
          <strong style={{ color: "var(--neutral)" }}>BUY → WAIT (sentiment demotion)</strong>{" "}
          <span style={{ color: "var(--text-dim)" }}>{view.sentimentDemotionReason}</span>
        </div>
      )}
      <SwingScoreCard view={view} />
      <CrossBasketSignals view={view} />
      {fundamentals && <FundDetails f={fundamentals} />}
      {consensus && <CrossCheck view={view} consensus={consensus} />}
      {news.length > 0 && <NewsList items={news} symbol={view.symbol} />}
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

/** Plain-English summary of why this symbol's verdict is what it is.
 * Source badge tells the user whether the prose came from an LLM
 * (verified against input facts) or a deterministic template (used
 * when the LLM rationale couldn't be verified or the LLM was
 * unavailable). Either way the content is factually safe — every
 * number traces to the input facts. */
function RationalePanel({ rationale }: { rationale: CompareRationale }) {
  // Verification status drives the badge colour + icon. The previous
  // version always showed a green ✓ when `verified=true`, but the
  // template-fallback rationale is `verified=true` even when the LLM
  // was rejected — so the green tick was misleading. Now: any
  // verification_notes flip the badge to amber ⚠️ regardless of source.
  const noteCount = rationale.verification_notes?.length ?? 0;
  const hasNotes = noteCount > 0;
  const sourceColour = hasNotes
    ? "var(--neutral)"
    : rationale.source === "llm"
      ? "var(--up)"
      : rationale.source?.startsWith("template")
        ? "var(--text-muted)"
        : "var(--text-muted)";
  const sourceLabel = sourceLabelFor(rationale.source);
  const badgeIcon = hasNotes ? " ⚠" : rationale.verified ? " ✓" : "";
  const badgeTitle = hasNotes
    ? `${noteCount} verification ${noteCount === 1 ? "note" : "notes"} — see "Verification notes" below for the offending sentence(s).`
    : rationale.source === "llm"
      ? `LLM-generated, verified against input facts. Model: ${rationale.model ?? "—"}`
      : "Built mechanically from the input facts (template fallback) — never from LLM creativity.";
  return (
    <div
      style={{
        marginBottom: 12,
        padding: "10px 12px",
        background: "rgba(31, 193, 107, 0.04)",
        borderLeft: `3px solid ${sourceColour}`,
        borderRadius: 4,
      }}
    >
      <div
        style={{
          display: "flex",
          alignItems: "baseline",
          justifyContent: "space-between",
          gap: 10,
          marginBottom: 6,
        }}
      >
        <span className="stat-label">In plain English</span>
        <span
          style={{ fontSize: 10, color: sourceColour, fontWeight: 600 }}
          title={badgeTitle}
        >
          {sourceLabel}
          {badgeIcon}
        </span>
      </div>
      <p style={{ margin: 0, fontSize: 13, lineHeight: 1.5, color: "var(--text)" }}>
        {rationale.summary}
      </p>
      {rationale.key_factors && rationale.key_factors.length > 0 && (
        <div style={{ marginTop: 8 }}>
          <div className="stat-label" style={{ fontSize: 10, marginBottom: 2 }}>Why</div>
          <ul style={{ margin: 0, padding: "0 0 0 16px", fontSize: 12, color: "var(--text-dim)" }}>
            {rationale.key_factors.map((f, i) => <li key={i}>{f}</li>)}
          </ul>
        </div>
      )}
      {rationale.caveats && rationale.caveats.length > 0 && (
        <div style={{ marginTop: 8 }}>
          <div className="stat-label" style={{ fontSize: 10, marginBottom: 2, color: "var(--down)" }}>
            Caveats
          </div>
          <ul style={{ margin: 0, padding: "0 0 0 16px", fontSize: 12, color: "var(--text-dim)" }}>
            {rationale.caveats.map((c, i) => <li key={i}>{c}</li>)}
          </ul>
        </div>
      )}
      {rationale.verification_notes && rationale.verification_notes.length > 0 && (
        <details style={{ marginTop: 8, fontSize: 11, color: "var(--neutral)" }}>
          <summary style={{ cursor: "pointer", fontWeight: 600 }}>
            ⚠ Verification notes ({rationale.verification_notes.length})
          </summary>
          <ul style={{ margin: "4px 0 0 16px", padding: 0, color: "var(--text-dim)" }}>
            {rationale.verification_notes.map((n, i) => <li key={i} style={{ marginBottom: 4 }}>{n}</li>)}
          </ul>
        </details>
      )}
    </div>
  );
}

function sourceLabelFor(source?: CompareRationale["source"]): string {
  switch (source) {
    case "llm": return "LLM ✓ verified";
    case "template": return "template (deterministic)";
    case "template_no_llm": return "template (LLM unavailable)";
    case "template_llm_failed": return "template (LLM failed)";
    case "template_empty_llm": return "template (LLM empty)";
    case "template_llm_unverified": return "template (LLM hallucinated)";
    default: return "—";
  }
}

/** Compact at-a-glance swing-score badge for the matrix row.
 * Renders "7/8" with a colour matching the verdict; a hover title
 * explains the per-layer breakdown so a user can decide to expand
 * the row for the full SwingScoreCard. */
function SwingBadge({ swing }: { swing: import("../api/types").SwingScore | null }) {
  if (!swing || swing.total === null || swing.total === undefined) {
    return <span style={{ color: "var(--text-muted)" }}>—</span>;
  }
  const colour =
    swing.verdict === "STRONG_BUY" ? "var(--up)"
    : swing.verdict === "BUY" ? "#4f8cff"
    : swing.verdict === "AVOID" ? "var(--down)"
    : "var(--neutral)";
  const layers = swing.layers;
  const title = (
    `Swing composite ${swing.total}/8 → ${swing.verdict}\n` +
    `  Quality   ${layers.quality}/2 — ${swing.reasons.quality ?? "—"}\n` +
    `  Valuation ${layers.valuation}/2 — ${swing.reasons.valuation ?? "—"}\n` +
    `  Event     ${layers.event}/2 — ${swing.reasons.event ?? "—"}\n` +
    `  Price     ${layers.price}/2 — ${swing.reasons.price ?? "—"}`
  );
  return (
    <span
      title={title}
      style={{
        color: colour,
        fontWeight: 700,
        fontSize: 12,
        display: "inline-flex",
        alignItems: "baseline",
        gap: 3,
      }}
    >
      {swing.total}
      <span style={{ fontSize: 10, color: "var(--text-muted)" }}>/8</span>
    </span>
  );
}

/** Phase-X composite swing-trade scorer card. Shows the 0-8 total +
 * verdict, with the per-layer breakdown (quality / valuation / event
 * / price) so the user sees where the points came from. */
function SwingScoreCard({ view }: { view: SymbolView }) {
  const sw = view.bestRow.swing_score;
  if (!sw || sw.total === null || sw.total === undefined) return null;
  const colour =
    sw.verdict === "STRONG_BUY" ? "var(--up)"
    : sw.verdict === "BUY" ? "var(--up-soft, #4f8cff)"
    : sw.verdict === "AVOID" ? "var(--down)"
    : "var(--neutral)";
  return (
    <div style={{ marginBottom: 12 }}>
      <div className="stat-label" style={{ marginBottom: 6, display: "flex", alignItems: "center", gap: 6 }}>
        <span>Swing composite — all four families in one number</span>
        <Info k="swing_score" />
      </div>
      <div
        style={{
          padding: "10px 14px",
          borderLeft: `3px solid ${colour}`,
          background: "rgba(0,0,0,0.18)",
          borderRadius: 4,
          fontSize: 12,
        }}
      >
        <div style={{ display: "flex", alignItems: "baseline", gap: 12, flexWrap: "wrap" }}>
          <span style={{ fontSize: 22, fontWeight: 700, color: colour }}>
            {sw.total}/8
          </span>
          <span style={{ fontSize: 14, fontWeight: 600, color: colour }}>
            {sw.verdict.replace("_", " ")}
          </span>
          <span style={{ color: "var(--text-dim)", fontSize: 11 }}>
            Q{sw.layers.quality} · V{sw.layers.valuation} · E{sw.layers.event} · P{sw.layers.price}
          </span>
        </div>
        <ul style={{ margin: "8px 0 0 0", padding: 0, listStyle: "none", color: "var(--text-dim)" }}>
          {Object.entries(sw.reasons).map(([layer, reason]) => (
            <li key={layer} style={{ padding: "2px 0", fontSize: 11 }}>
              <span style={{ display: "inline-block", width: 70, color: "var(--text-muted)", textTransform: "uppercase", fontSize: 10, letterSpacing: "0.04em" }}>
                {layer}
              </span>
              {reason}
            </li>
          ))}
        </ul>
      </div>
    </div>
  );
}

/** Family-2 (valuation) + Family-3 (cross-sectional momentum)
 * annotations on the best-row. Both are basket-relative — they
 * compare this symbol to its peers in the same universe — so they
 * surface signals the per-symbol Family-1 strategies can't see.
 * Annotation only: NOT a verdict driver yet (see Phase X). */
function CrossBasketSignals({ view }: { view: SymbolView }) {
  const cs = view.bestRow.cross_sectional_momentum;
  const val = view.bestRow.valuation_flag;
  const haveCs = cs && cs.rank !== null;
  const haveVal = val && val.flag !== "n/a";
  if (!haveCs && !haveVal) return null;

  const flagColour = (flag?: string) =>
    flag === "cheap" ? "var(--up)"
      : flag === "expensive" ? "var(--down)"
      : "var(--text-dim)";

  return (
    <div style={{ marginBottom: 12 }}>
      <div className="stat-label" style={{ marginBottom: 6 }}>
        Cross-basket signals — how this symbol stacks vs its peers
      </div>
      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 10 }}>
        {haveCs && cs && (
          <div
            style={{
              padding: "8px 10px",
              borderLeft: `3px solid ${cs.is_top_quartile ? "var(--up)" : "var(--text-dim)"}`,
              background: "rgba(0,0,0,0.18)",
              borderRadius: 4,
              fontSize: 12,
            }}
          >
            <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
              <strong>Momentum rank</strong>
              <Info k="cross_sectional_momentum" />
            </div>
            <div style={{ marginTop: 4 }}>
              <span className="num" style={{ fontWeight: 700, fontSize: 14 }}>
                {cs.rank} of {(cs.peer_count ?? 0) + 1}
              </span>
              {cs.is_top_quartile && (
                <span
                  style={{
                    marginLeft: 8,
                    fontSize: 10,
                    padding: "1px 6px",
                    borderRadius: 3,
                    background: "rgba(31,193,107,0.15)",
                    color: "var(--up)",
                  }}
                >
                  TOP QUARTILE
                </span>
              )}
            </div>
            <div style={{ marginTop: 4, color: "var(--text-dim)", fontSize: 11 }}>
              z-score{" "}
              <span className="num">
                {cs.zscore !== null ? cs.zscore.toFixed(2) : "—"}
              </span>{" "}
              · 12m return{" "}
              <span className="num">
                {cs.value !== null ? `${cs.value.toFixed(1)}%` : "—"}
              </span>{" "}
              · basket median{" "}
              <span className="num">
                {cs.basket_median !== null
                  ? `${cs.basket_median.toFixed(1)}%`
                  : "—"}
              </span>
            </div>
          </div>
        )}
        {haveVal && val && (
          <div
            style={{
              padding: "8px 10px",
              borderLeft: `3px solid ${flagColour(val.flag)}`,
              background: "rgba(0,0,0,0.18)",
              borderRadius: 4,
              fontSize: 12,
            }}
          >
            <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
              <strong>Valuation flag</strong>
              <Info k="valuation_flag" />
            </div>
            <div
              style={{
                marginTop: 4,
                fontSize: 14,
                fontWeight: 700,
                color: flagColour(val.flag),
                textTransform: "uppercase",
                letterSpacing: "0.04em",
              }}
            >
              {val.flag}
            </div>
            <div style={{ marginTop: 4, color: "var(--text-dim)", fontSize: 11 }}>
              {val.basis}
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

/** Fund-level fundamentals: family, expense ratio, AUM, yield, top
 * holdings, sector mix. Hides rows that aren't applicable to the ETF
 * (e.g. duration only shows on bond ETFs). */
function FundDetails({ f }: { f: CompareFundamentals }) {
  const haveCore =
    f.expense_ratio_pct !== null
    || f.aum_usd !== null
    || f.dividend_yield_pct !== null
    || (f.fund_family && f.fund_family.length > 0);
  if (!haveCore && f.top_holdings.length === 0 && Object.keys(f.sector_weights).length === 0) {
    return null;
  }
  return (
    <div style={{ marginBottom: 12 }}>
      <div className="stat-label" style={{ marginBottom: 4 }}>
        Fund details{" "}
        <span style={{ color: "var(--text-muted)", fontWeight: 400 }}>
          (Yahoo Finance, refreshed each run)
        </span>
      </div>
      <div
        style={{
          display: "grid",
          gridTemplateColumns: "repeat(auto-fit, minmax(140px, 1fr))",
          gap: 8,
          padding: "6px 8px",
          background: "rgba(255,255,255,0.02)",
          borderRadius: 4,
          marginBottom: 8,
        }}
      >
        {f.fund_family && <ConsensusStat label="Issuer" value={f.fund_family} />}
        {f.category && <ConsensusStat label="Category" value={f.category} />}
        {f.expense_ratio_pct !== null && (
          <ConsensusStat
            label="Expense ratio"
            value={`${f.expense_ratio_pct.toFixed(2)}%`}
            sub="annual fee"
          />
        )}
        {f.aum_usd !== null && (
          <ConsensusStat
            label="AUM"
            value={fmtAum(f.aum_usd)}
            sub="assets under mgmt"
          />
        )}
        {f.dividend_yield_pct !== null && (
          <ConsensusStat
            label="Dividend yield"
            value={`${f.dividend_yield_pct.toFixed(2)}%`}
            sub="trailing 12m"
          />
        )}
        {f.distribution_yield_pct !== null
          && f.distribution_yield_pct !== f.dividend_yield_pct && (
          <ConsensusStat
            label="Distribution yld"
            value={`${f.distribution_yield_pct.toFixed(2)}%`}
          />
        )}
        {f.yield_to_maturity_pct !== null && (
          <ConsensusStat
            label="Yield to maturity"
            value={`${f.yield_to_maturity_pct.toFixed(2)}%`}
            sub="bond ETF"
          />
        )}
        {f.duration_years !== null && (
          <ConsensusStat
            label="Duration"
            value={`${f.duration_years.toFixed(1)}y`}
            sub="rate sensitivity"
          />
        )}
        {f.inception_date && (
          <ConsensusStat label="Inception" value={f.inception_date} />
        )}
      </div>

      {f.top_holdings.length > 0 && (
        <div style={{ fontSize: 11 }}>
          <div className="stat-label" style={{ marginBottom: 4 }}>
            Top {f.top_holdings.length} holdings
          </div>
          <ul style={{ margin: 0, padding: 0, listStyle: "none", display: "grid",
                       gridTemplateColumns: "repeat(auto-fit, minmax(180px, 1fr))", gap: 4 }}>
            {f.top_holdings.map((h, i) => (
              <li key={i} style={{ display: "flex", justifyContent: "space-between", gap: 8 }}>
                <span style={{ color: "var(--text-dim)", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                  {h.symbol ? `${h.symbol} · ` : ""}{h.name}
                </span>
                {h.weight_pct !== null && (
                  <span className="num" style={{ color: "var(--text)", fontWeight: 600, flexShrink: 0 }}>
                    {h.weight_pct.toFixed(2)}%
                  </span>
                )}
              </li>
            ))}
          </ul>
        </div>
      )}

      {Object.keys(f.sector_weights).length > 0 && (
        <div style={{ fontSize: 11, marginTop: 8 }}>
          <div className="stat-label" style={{ marginBottom: 4 }}>Sector mix</div>
          <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(120px, 1fr))", gap: 4 }}>
            {Object.entries(f.sector_weights)
              .sort((a, b) => b[1] - a[1])
              .slice(0, 12)
              .map(([sector, pct]) => (
                <div key={sector} style={{ display: "flex", justifyContent: "space-between" }}>
                  <span style={{ color: "var(--text-dim)" }}>{sector}</span>
                  <span className="num" style={{ color: "var(--text)" }}>
                    {(pct * (Math.abs(pct) <= 1.5 ? 100 : 1)).toFixed(1)}%
                  </span>
                </div>
              ))}
          </div>
        </div>
      )}
    </div>
  );
}

function NewsList({ items, symbol }: { items: CompareNewsItem[]; symbol: string }) {
  return (
    <div style={{ marginTop: 12 }}>
      <div className="stat-label" style={{ marginBottom: 4 }}>
        Recent headlines on {symbol}{" "}
        <span style={{ color: "var(--text-muted)", fontWeight: 400 }}>
          (sentiment scored by the LLM — fed into the verdict via the trend rule)
        </span>
      </div>
      <ul style={{ margin: 0, padding: 0, listStyle: "none", display: "flex",
                   flexDirection: "column", gap: 4 }}>
        {items.slice(0, 5).map((item, i) => (
          <li key={i} style={{ fontSize: 12, lineHeight: 1.45 }}>
            <SentimentBadge item={item} />
            {item.link ? (
              <a
                href={item.link}
                target="_blank"
                rel="noreferrer"
                style={{ color: "var(--text)", textDecoration: "none", marginLeft: 6 }}
                onClick={(e) => e.stopPropagation()}
              >
                {item.title}
              </a>
            ) : (
              <span style={{ color: "var(--text)", marginLeft: 6 }}>{item.title}</span>
            )}
            <span style={{ color: "var(--text-muted)", fontSize: 11, marginLeft: 6 }}>
              {item.publisher ?? "—"}
              {item.published_at && ` · ${fmtNewsAge(item.published_at)}`}
              {item.sentiment_themes && item.sentiment_themes.length > 0 && (
                <> · <em>{item.sentiment_themes.join(", ")}</em></>
              )}
              {item.sentiment_error && (
                <> · <span style={{ color: "var(--down)" }}>scoring failed: {item.sentiment_error}</span></>
              )}
            </span>
          </li>
        ))}
      </ul>
    </div>
  );
}

function SentimentBadge({ item }: { item: CompareNewsItem }) {
  const s = item.sentiment;
  if (s === null || s === undefined) {
    return (
      <span
        title={item.sentiment_error ?? "not scored"}
        style={{
          display: "inline-block", padding: "0px 4px", borderRadius: 3,
          background: "rgba(255,255,255,0.06)", color: "var(--text-muted)",
          fontSize: 10, minWidth: 30, textAlign: "center",
        }}
      >
        —
      </span>
    );
  }
  const colour = s >= 0.2 ? "var(--up)" : s <= -0.2 ? "var(--down)" : "var(--text-muted)";
  const sign = s > 0 ? "+" : "";
  return (
    <span
      title={`Sentiment ${sign}${s.toFixed(2)}${item.sentiment_material ? " · material" : ""}${item.sentiment_model ? ` · ${item.sentiment_model}` : ""}`}
      style={{
        display: "inline-block", padding: "0px 4px", borderRadius: 3,
        background: "rgba(255,255,255,0.04)",
        color: colour,
        fontSize: 10, minWidth: 30, textAlign: "center", fontWeight: 600,
      }}
    >
      {item.sentiment_material ? "★ " : ""}
      {sign}{s.toFixed(2)}
    </span>
  );
}

/** Build the sentiment trend check entry for the rules ladder. */
function sentimentCheck(view: SymbolView): DecisionCheck {
  const s = view.bestRow.sentiment_summary;
  const status = view.bestRow.sentiment_status;

  if (status === "provider_down") {
    return {
      name: "Sentiment trend (7d)",
      status: "warn",
      detail: "LLM unavailable — sentiment did not factor into the verdict.",
    };
  }
  if (status === "no_news" || !s || s.items_considered === 0) {
    return {
      name: "Sentiment trend (7d)",
      status: "warn",
      detail: "no recent headlines",
    };
  }
  if (status === "all_failed") {
    return {
      name: "Sentiment trend (7d)",
      status: "warn",
      detail: "every headline failed to score — verdict ran without sentiment",
    };
  }

  const mean = s.mean_sentiment ?? 0;
  const meanFmt = `${mean >= 0 ? "+" : ""}${mean.toFixed(2)}`;
  if (view.sentimentDemoted) {
    return {
      name: "Sentiment trend (7d)",
      status: "fail",
      detail: `${meanFmt} mean · ${s.material_negative_count} material-negative — triggered BUY → WAIT demotion`,
    };
  }
  if (mean <= -0.15) {
    return {
      name: "Sentiment trend (7d)",
      status: "warn",
      detail: `${meanFmt} mean over ${s.items_considered} items — slightly negative but below demotion threshold`,
    };
  }
  return {
    name: "Sentiment trend (7d)",
    status: "pass",
    detail: `${meanFmt} mean · ${s.items_considered} items considered${status === "partial" ? " (some failed)" : ""}`,
  };
}

function fmtAum(usd: number): string {
  const abs = Math.abs(usd);
  if (abs >= 1e12) return `$${(usd / 1e12).toFixed(2)}T`;
  if (abs >= 1e9) return `$${(usd / 1e9).toFixed(1)}B`;
  if (abs >= 1e6) return `$${(usd / 1e6).toFixed(0)}M`;
  return `$${usd.toFixed(0)}`;
}

function fmtNewsAge(iso: string): string {
  const t = new Date(iso).getTime();
  if (Number.isNaN(t)) return "";
  const min = Math.max(1, Math.round((Date.now() - t) / 60000));
  if (min < 60) return `${min}m ago`;
  const hr = Math.round(min / 60);
  if (hr < 48) return `${hr}h ago`;
  const days = Math.round(hr / 24);
  return `${days}d ago`;
}

/** Side-by-side: our verdict vs Wall Street's published consensus.
 * Lets a user sanity-check that we're not arguing with the analyst pool. */
function CrossCheck({
  view,
  consensus,
}: {
  view: SymbolView;
  consensus: CompareExternalConsensus;
}) {
  const ourBucket = view.bucket;
  const theirLabel = consensus.rating_label;
  const isRated = !!theirLabel;

  let agreement: "agree" | "disagree" | "neutral" = "neutral";
  let agreementText = "";
  if (isRated && theirLabel) {
    const theirSide =
      theirLabel === "STRONG BUY" || theirLabel === "BUY"
        ? "BUY"
        : theirLabel === "SELL" || theirLabel === "STRONG SELL" || theirLabel === "UNDERPERFORM"
          ? "SELL"
          : "HOLD";
    if (theirSide === "BUY" && ourBucket === "BUY") {
      agreement = "agree"; agreementText = "Both sides say buy.";
    } else if (theirSide === "SELL" && ourBucket === "AVOID") {
      agreement = "agree"; agreementText = "Both sides say avoid.";
    } else if (theirSide === "BUY" && ourBucket === "AVOID") {
      agreement = "disagree";
      agreementText = "We disagree with Wall Street — they say buy, we say avoid.";
    } else if (theirSide === "SELL" && ourBucket === "BUY") {
      agreement = "disagree";
      agreementText = "We disagree with Wall Street — they say sell, we say buy.";
    } else {
      agreement = "neutral";
      agreementText = "Mixed read — analysts and our system are not aligned.";
    }
  }
  const colour =
    agreement === "agree" ? "var(--up)" : agreement === "disagree" ? "var(--down)" : "var(--text-muted)";

  return (
    <div style={{ marginBottom: 12 }}>
      <div className="stat-label" style={{ marginBottom: 4 }}>
        Cross-check — Wall Street analyst consensus{" "}
        <span style={{ color: "var(--text-muted)", fontWeight: 400 }}>
          (Yahoo Finance, free)
        </span>
      </div>
      {!isRated ? (
        <div style={{ fontSize: 12, color: "var(--text-muted)", padding: "6px 0" }}>
          Not rated. ETFs, index trackers, and most baskets don't have
          analyst consensus — analysts rate the underlying companies, not
          the basket.
        </div>
      ) : (
        <div
          style={{
            display: "grid",
            gridTemplateColumns: "repeat(auto-fit, minmax(160px, 1fr))",
            gap: 10,
            padding: "6px 8px",
            borderLeft: `2px solid ${colour}`,
            background: "rgba(255,255,255,0.02)",
            borderRadius: 4,
          }}
        >
          <ConsensusStat label="Wall St rating" value={theirLabel ?? "—"} colour={colour} />
          <ConsensusStat
            label="Mean rating"
            value={consensus.rating_mean !== null ? consensus.rating_mean.toFixed(2) : "—"}
            sub="1 = strong buy · 5 = strong sell"
          />
          <ConsensusStat
            label="Analysts"
            value={consensus.n_analysts !== null ? String(consensus.n_analysts) : "—"}
          />
          <ConsensusStat
            label="Target (mean)"
            value={consensus.target_mean !== null ? `$${consensus.target_mean.toFixed(2)}` : "—"}
            sub={
              consensus.target_vs_current_pct !== null
                ? `${consensus.target_vs_current_pct >= 0 ? "+" : ""}${consensus.target_vs_current_pct.toFixed(1)}% vs current`
                : ""
            }
          />
          <div style={{ gridColumn: "1 / -1", fontSize: 11, color: colour, marginTop: 4 }}>
            <strong>Our verdict:</strong> {ourBucket} · <strong>Wall St:</strong> {theirLabel}.{" "}
            {agreementText}
          </div>
        </div>
      )}
    </div>
  );
}

function ConsensusStat({
  label,
  value,
  sub,
  colour,
}: {
  label: string;
  value: string;
  sub?: string;
  colour?: string;
}) {
  return (
    <div>
      <div className="stat-label" style={{ fontSize: 10 }}>{label}</div>
      <div className="num" style={{ fontSize: 13, fontWeight: 600, marginTop: 2, color: colour ?? "var(--text)" }}>
        {value}
      </div>
      {sub && <div style={{ fontSize: 10, color: "var(--text-muted)", marginTop: 1 }}>{sub}</div>}
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
        help="vix"
        value={ctx.vix !== null ? ctx.vix.toFixed(1) : "—"}
        sub={ctx.vix_regime ?? "—"}
        colour={vixColour}
      />
      <ContextStat
        label="10Y yield"
        help="treasury_yield"
        value={ctx.tnx !== null ? `${ctx.tnx.toFixed(2)}%` : "—"}
        sub={ctx.tnx_trend ?? "—"}
      />
      <ContextStat
        label="S&P off peak"
        help="sp_drawdown"
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
        help="active_stress_regime"
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
  help,
}: {
  label: string;
  value: string;
  sub: string;
  colour?: string;
  help?: string;
}) {
  return (
    <div style={{ minWidth: 100 }}>
      <div className="stat-label">
        {label}
        {help && <Info k={help as Parameters<typeof Info>[0]["k"]} />}
      </div>
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
{`# from the Strategy Engine (in /strategies):
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

function Th({
  children,
  align,
  help,
  title,
}: {
  children: React.ReactNode;
  align?: "left" | "right" | "center";
  help?: string;
  title?: string;
}) {
  return (
    <th
      title={title}
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
      {help && <Info k={help as Parameters<typeof Info>[0]["k"]} />}
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
  align?: "left" | "right" | "center";
  style?: React.CSSProperties;
  className?: string;
}) {
  return (
    <td
      className={className}
      style={{ padding: "8px 12px", textAlign: align ?? "left", ...style }}
    >
      {children}
    </td>
  );
}

function bucketColour(b: SymbolView["bucket"]): string {
  if (b === "BUY") return "var(--up)";
  if (b === "AVOID") return "var(--down)";
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

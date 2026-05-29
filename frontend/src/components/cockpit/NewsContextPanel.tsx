/**
 * NewsContextPanel — the trader's "what's the news picture" for a
 * symbol, in-app. Closes the "had to ask Claude separately about TSLA"
 * gap (#72).
 *
 * Reads /api/sentiment/symbol/{X}/news which returns the rolling
 * sentiment scores + rationales from the LLM sentiment pipeline.
 * Each row is one scoring cycle: classification (positive / negative
 * / neutral), score (-1..+1), n_articles, and the model's rationale.
 *
 * Trader sees how the news picture is evolving over time without
 * leaving the cockpit.
 */
import { useEffect, useState } from "react";
import { config } from "../../config";

type NewsRow = {
  score: number;
  classification: string;
  nArticles: number;
  rationale: string | null;
  source: string;
  scoredAtUtc: string;
};

type NewsResp = {
  symbol: string;
  count: number;
  rows: NewsRow[];
};

export function NewsContextPanel({ symbol }: { symbol: string }) {
  const [data, setData] = useState<NewsResp | null>(null);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    const load = async () => {
      try {
        const resp = await fetch(
          `${config.apiBaseUrl}/api/sentiment/symbol/${encodeURIComponent(symbol)}/news?limit=15`,
        );
        if (!resp.ok) throw new Error(`${resp.status}`);
        const d: NewsResp = await resp.json();
        if (cancelled) return;
        setData(d);
        setErr(null);
      } catch (e) {
        if (cancelled) return;
        setErr(String(e));
      }
    };
    void load();
    return () => { cancelled = true; };
  }, [symbol]);

  if (err) {
    return <div style={{ fontSize: 11, color: "var(--down)" }}>news fetch failed: {err}</div>;
  }
  if (!data) {
    return <div style={{ fontSize: 11, color: "var(--text-muted)" }}>Loading news for {symbol}…</div>;
  }
  if (data.rows.length === 0) {
    return <div style={{ fontSize: 11, color: "var(--text-dim)" }}>
      No news context recorded for {symbol}. Run <code>tradepro-sentiment-score {symbol}</code> to score recent headlines.
    </div>;
  }

  // Aggregate the latest scoring's tone for the header chip.
  const latest = data.rows[0];
  const latestColour =
    latest.score > 0.1 ? "var(--up)"
    : latest.score < -0.1 ? "var(--down)" : "var(--neutral)";

  return (
    <div>
      <div style={{
        display: "flex", alignItems: "center", gap: 10,
        marginBottom: 8, fontSize: 11,
      }}>
        <span style={{
          padding: "2px 8px", borderRadius: 999,
          background: `${latestColour}22`,
          color: latestColour,
          fontWeight: 700, letterSpacing: "0.06em", textTransform: "uppercase",
        }}>
          latest: {latest.classification} ({latest.score >= 0 ? "+" : ""}{latest.score.toFixed(2)})
        </span>
        <span style={{ color: "var(--text-muted)" }}>
          {data.rows.length} score(s) · newest {timeAgo(latest.scoredAtUtc)}
        </span>
      </div>
      <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
        {data.rows.map((r, i) => (
          <NewsRowCard key={`${r.scoredAtUtc}-${i}`} row={r} />
        ))}
      </div>
    </div>
  );
}

function NewsRowCard({ row }: { row: NewsRow }) {
  const colour =
    row.score > 0.1 ? "var(--up)"
    : row.score < -0.1 ? "var(--down)" : "var(--neutral)";
  return (
    <div style={{
      padding: "6px 10px",
      borderLeft: `3px solid ${colour}`,
      border: "1px solid var(--border)",
      borderRadius: 6,
      background: "rgba(0,0,0,0.10)",
      fontSize: 11,
    }}>
      <div style={{ display: "flex", gap: 8, alignItems: "baseline" }}>
        <strong style={{ color: colour }}>
          {row.classification} ({row.score >= 0 ? "+" : ""}{row.score.toFixed(2)})
        </strong>
        <span style={{ color: "var(--text-dim)", fontSize: 10 }}>
          {row.nArticles} article{row.nArticles === 1 ? "" : "s"} · {row.source}
        </span>
        <span style={{ marginLeft: "auto", color: "var(--text-muted)", fontSize: 10 }}>
          {timeAgo(row.scoredAtUtc)}
        </span>
      </div>
      {row.rationale && (
        <div style={{ marginTop: 4, color: "var(--text)", lineHeight: 1.4 }}>
          {row.rationale}
        </div>
      )}
    </div>
  );
}

function timeAgo(iso: string): string {
  const ms = Date.now() - new Date(iso).getTime();
  if (Number.isNaN(ms) || ms < 0) return "now";
  const m = Math.round(ms / 60_000);
  if (m < 60) return `${m}m ago`;
  const h = Math.round(m / 60);
  if (h < 24) return `${h}h ago`;
  const d = Math.round(h / 24);
  return `${d}d ago`;
}

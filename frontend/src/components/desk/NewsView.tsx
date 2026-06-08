/**
 * NewsView — the /desk "News" view (IBKR "Daily Overview" nav).
 *
 * A read-only, dark, dense daily-overview built entirely from real endpoint
 * data — nothing is fabricated; any missing field degrades to a labelled
 * empty state. Four panels:
 *
 *   1. Market Performance — from compareLatest(universe).payload.market_context
 *      (VIX + regime, 10y yield + trend, SPY drawdown, active stress regimes).
 *   2. Upcoming Earnings  — flattened rows[].earnings_signal.upcoming, soonest
 *      first, next ~10.
 *   3. Hot News (Headlines) — flattened rows[].news[], newest first, ~15, with
 *      publisher + sentiment colour + outbound link.
 *   4. Catalysts — api.catalysts({status:'active'}), symbol · kind · date ·
 *      severity colour.
 *
 * Universe is the first available (same default as Compare/Screeners). On wide
 * screens the panels sit in a responsive 2-col grid; they stack on mobile.
 */
import { useEffect, useState } from "react";
import { api } from "../../api/client";
import type { CompareLatestResponse, CompareMarketContext } from "../../api/types";

type Catalyst = Awaited<ReturnType<typeof api.catalysts>>["catalysts"][number];

type Earnings = {
  symbol: string;
  date: string;
  days_until: number;
  eps_estimate?: number | null;
};

type Headline = {
  symbol: string;
  title: string;
  publisher: string | null;
  link: string | null;
  published_at: string | null;
  sentiment?: number | null;
};

const SEVERITY_COLOUR: Record<string, string> = {
  high: "#ef4444",
  medium: "#e0b341",
  low: "#8693b5",
};

export function NewsView({ wide }: { wide: boolean }) {
  const [universes, setUniverses] = useState<string[]>([]);
  const [universe, setUniverse] = useState<string>("");
  const [data, setData] = useState<CompareLatestResponse | null>(null);
  const [cmpErr, setCmpErr] = useState<string | null>(null);
  const [catalysts, setCatalysts] = useState<Catalyst[] | null>(null);
  const [catErr, setCatErr] = useState<string | null>(null);

  useEffect(() => {
    api.compareUniverses()
      .then((r) => {
        const names = r.universes.map((u) => u.universe);
        setUniverses(names);
        if (names.length > 0) setUniverse((u) => u || names[0]);
      })
      .catch((e) => setCmpErr(e instanceof Error ? e.message : String(e)));
  }, []);

  useEffect(() => {
    if (!universe) return;
    let live = true;
    setCmpErr(null);
    api.compareLatest(universe)
      .then((r) => { if (live) setData(r); })
      .catch((e) => { if (live) { setData(null); setCmpErr(e instanceof Error ? e.message : String(e)); } });
    return () => { live = false; };
  }, [universe]);

  useEffect(() => {
    let live = true;
    api.catalysts({ status: "active", lookaheadDays: 90 })
      .then((r) => { if (live) setCatalysts(r.catalysts); })
      .catch((e) => { if (live) setCatErr(e instanceof Error ? e.message : String(e)); });
    return () => { live = false; };
  }, []);

  const rows = data?.payload.rows ?? [];

  // Upcoming earnings, soonest first, next 10.
  const earnings: Earnings[] = rows
    .flatMap((r): Earnings[] => {
      const u = r.earnings_signal?.upcoming;
      return u ? [{ symbol: r.symbol, date: u.date, days_until: u.days_until, eps_estimate: u.eps_estimate }] : [];
    })
    .sort((a, b) => a.days_until - b.days_until)
    .slice(0, 10);

  // Headlines, newest first, top 15. DE-DUPED: a market-wide story is attached
  // to every symbol in the universe, so the raw flat list is ~50× duplicated
  // (826 rows → 56 unique on etf_uk_core). Collapse by title+publisher, keeping
  // the most-recent copy (sort first, first-seen wins).
  const seenHeadline = new Set<string>();
  const headlines: Headline[] = rows
    .flatMap((r) => (r.news ?? []).map((n) => ({
      symbol: r.symbol,
      title: n.title,
      publisher: n.publisher ?? null,
      link: n.link ?? null,
      published_at: n.published_at ?? null,
      sentiment: n.sentiment ?? null,
    })))
    .sort((a, b) => tsOf(b.published_at) - tsOf(a.published_at))
    .filter((h) => {
      const key = `${(h.title ?? "").trim().toLowerCase()}|${(h.publisher ?? "").toLowerCase()}`;
      if (!h.title || seenHeadline.has(key)) return false;
      seenHeadline.add(key);
      return true;
    })
    .slice(0, 15);

  return (
    <div style={{ display: "grid", gridTemplateColumns: wide ? "1fr 1fr" : "1fr", gap: 12, alignItems: "start" }}>
      <Panel title="Market Performance">
        <MarketContextPanel ctx={data?.payload.market_context} err={cmpErr} universe={universe} />
      </Panel>

      <Panel title="Upcoming Earnings">
        {cmpErr ? <Dim tone="down">{cmpErr}</Dim>
          : earnings.length === 0 ? <Dim>No upcoming earnings in this universe.</Dim>
          : (
            <ul style={LIST}>
              {earnings.map((e, i) => (
                <li key={`${e.symbol}:${i}`} style={ROW}>
                  <span style={{ fontWeight: 700, color: "var(--text)" }}>{e.symbol}</span>
                  <span style={{ color: "var(--text-muted)", fontFamily: "monospace" }}>{e.date}</span>
                  <span style={{ color: e.days_until <= 5 ? "#e0b341" : "var(--text-muted)" }}>
                    in {e.days_until}d
                  </span>
                  <span style={{ marginLeft: "auto", color: "var(--text-muted)", fontFamily: "monospace" }}>
                    {e.eps_estimate != null ? `EPS est ${e.eps_estimate}` : ""}
                  </span>
                </li>
              ))}
            </ul>
          )}
      </Panel>

      <Panel title="Hot News">
        {cmpErr ? <Dim tone="down">{cmpErr}</Dim>
          : headlines.length === 0 ? <Dim>No headlines available.</Dim>
          : (
            <ul style={LIST}>
              {headlines.map((h, i) => (
                <li key={`${h.symbol}:${i}`} style={{ ...ROW, alignItems: "flex-start", flexWrap: "wrap" }}>
                  <span title="Headline sentiment" style={{
                    width: 8, height: 8, borderRadius: "50%", marginTop: 4,
                    background: sentimentColour(h.sentiment), flexShrink: 0,
                  }} />
                  <span style={{ fontWeight: 700, color: "var(--text)", flexShrink: 0 }}>{h.symbol}</span>
                  <span style={{ flex: 1, minWidth: 120 }}>
                    {h.link ? (
                      <a href={h.link} target="_blank" rel="noopener noreferrer" style={{ color: "var(--text-dim)", textDecoration: "none" }}>
                        {h.title}
                      </a>
                    ) : (
                      <span style={{ color: "var(--text-dim)" }}>{h.title}</span>
                    )}
                  </span>
                  <span style={{ color: "var(--text-muted)", fontSize: 10, flexShrink: 0 }}>
                    {h.publisher ?? ""}{h.published_at ? ` · ${fmtWhen(h.published_at)}` : ""}
                  </span>
                </li>
              ))}
            </ul>
          )}
      </Panel>

      <Panel title="Active Catalysts">
        {catErr ? <Dim tone="down">{catErr}</Dim>
          : catalysts == null ? <Dim>Loading catalysts…</Dim>
          : catalysts.length === 0 ? <Dim>No active catalysts.</Dim>
          : (
            <ul style={LIST}>
              {catalysts.map((c) => (
                <li key={c.id} style={ROW}>
                  <span style={{
                    width: 8, height: 8, borderRadius: "50%",
                    background: SEVERITY_COLOUR[c.severity] ?? "#8693b5", flexShrink: 0,
                  }} title={`${c.severity} severity`} />
                  <span style={{ fontWeight: 700, color: "var(--text)" }}>{c.symbol}</span>
                  <span style={{ color: "var(--text-muted)", textTransform: "uppercase", fontSize: 10 }}>{c.kind}</span>
                  <span style={{ flex: 1, minWidth: 80, color: "var(--text-dim)" }}>{c.title}</span>
                  <span style={{ color: "var(--text-muted)", fontFamily: "monospace", flexShrink: 0 }}>
                    {c.occurs_on ?? "undated"}
                  </span>
                </li>
              ))}
            </ul>
          )}
      </Panel>

      {universes.length > 1 && (
        <div style={{ gridColumn: "1 / -1", fontSize: 10, color: "var(--text-muted)" }}>
          Market context, earnings + headlines are for universe <strong>{universe}</strong> (the default).
        </div>
      )}
    </div>
  );
}

function MarketContextPanel({
  ctx, err, universe,
}: { ctx?: CompareMarketContext; err: string | null; universe: string }) {
  if (err) return <Dim tone="down">{err}</Dim>;
  if (!universe) return <Dim>Loading…</Dim>;
  if (!ctx) return <Dim>No market context for this universe.</Dim>;
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
      <div style={{ display: "flex", flexWrap: "wrap", gap: 10 }}>
        <Metric label="VIX" value={ctx.vix != null ? ctx.vix.toFixed(1) : "—"} sub={ctx.vix_regime ?? undefined} />
        <Metric label="10Y (TNX)" value={ctx.tnx != null ? `${ctx.tnx.toFixed(2)}%` : "—"} sub={ctx.tnx_trend ?? undefined} />
        <Metric
          label="SPY Drawdown"
          value={ctx.spy_drawdown_pct != null ? `${ctx.spy_drawdown_pct.toFixed(1)}%` : "—"}
          colour={ctx.spy_drawdown_pct != null && ctx.spy_drawdown_pct < 0 ? "#ef4444" : undefined}
        />
      </div>
      {ctx.active_stress_regimes && ctx.active_stress_regimes.length > 0 && (
        <div style={{ display: "flex", flexWrap: "wrap", gap: 6 }}>
          {ctx.active_stress_regimes.map((s) => (
            <span key={s} style={{
              fontSize: 10, color: "#e0b341", border: "1px solid #e0b341",
              borderRadius: 4, padding: "1px 6px",
            }}>
              {s}
            </span>
          ))}
        </div>
      )}
      {ctx.summary && (
        <div style={{ fontSize: 11, color: "var(--text-muted)" }}>{ctx.summary}</div>
      )}
      {ctx.as_of && (
        <div style={{ fontSize: 10, color: "var(--text-muted)" }}>As of {fmtWhen(ctx.as_of)}</div>
      )}
    </div>
  );
}

function Metric({ label, value, sub, colour }: { label: string; value: string; sub?: string; colour?: string }) {
  return (
    <div style={{ minWidth: 80 }}>
      <div style={{ fontSize: 9, color: "var(--text-muted)", textTransform: "uppercase", letterSpacing: "0.05em" }}>{label}</div>
      <div style={{ fontSize: 16, fontWeight: 700, fontFamily: "monospace", color: colour ?? "var(--text)" }}>{value}</div>
      {sub && <div style={{ fontSize: 10, color: "var(--text-muted)", textTransform: "capitalize" }}>{sub}</div>}
    </div>
  );
}

/** sentiment -1..1 → red..green. null → muted grey. */
function sentimentColour(s: number | null | undefined): string {
  if (s == null) return "#8693b5";
  if (s > 0.15) return "#1fc16b";
  if (s < -0.15) return "#ef4444";
  return "#e0b341";
}

function tsOf(iso: string | null): number {
  if (!iso) return 0;
  const t = new Date(iso).getTime();
  return Number.isFinite(t) ? t : 0;
}

function fmtWhen(iso: string | null): string {
  if (!iso) return "";
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? iso : d.toLocaleDateString();
}

function Panel({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section style={{
      border: "1px solid #1b2233", borderRadius: 8,
      background: "rgba(255,255,255,0.015)", padding: 12, minWidth: 0,
    }}>
      <div style={{
        fontSize: 11, color: "var(--text-muted)", textTransform: "uppercase",
        letterSpacing: "0.05em", marginBottom: 8,
      }}>
        {title}
      </div>
      {children}
    </section>
  );
}

function Dim({ children, tone }: { children: React.ReactNode; tone?: "down" }) {
  return (
    <div style={{ fontSize: 11, color: tone === "down" ? "var(--down, #ef4444)" : "var(--text-muted)", fontStyle: "italic" }}>
      {children}
    </div>
  );
}

const LIST: React.CSSProperties = { listStyle: "none", margin: 0, padding: 0, display: "flex", flexDirection: "column", gap: 7 };
const ROW: React.CSSProperties = { display: "flex", gap: 8, alignItems: "center", fontSize: 12 };

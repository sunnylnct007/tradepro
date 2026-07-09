/**
 * HarvestView — the data-harvest maintenance screen.
 *
 * One place to: track every tracked symbol, see its cached coverage + freshness,
 * and surface missing-data / quality issues so a maintainer can answer "is the
 * bar cache healthy, and which symbols need a re-harvest?" at a glance.
 *
 * Reads the existing data-trust API (no new backend): barCacheHealth gives per-
 * symbol coverage (start/end/partitions), missing_days_count, last-fetched
 * provider/result/time, and manifest violations. Honest about the empty state —
 * a symbol never harvested shows "not harvested", not a false red.
 */
import { useEffect, useMemo, useState } from "react";
import { api } from "../../api/client";
import { PriceHistoryChart } from "../PriceHistoryChart";

type Row = Awaited<ReturnType<typeof api.barCacheHealth>>["health"][number];
type Quality = Awaited<ReturnType<typeof api.barCacheQuality>>;
type QRow = Quality["symbols"][number];
type Harvester = Awaited<ReturnType<typeof api.ibkrHarvesterStatus>>;

// Decision-grade tier → colour + glyph. The headline question: "good enough to
// decide on TODAY?" GOOD/BRONZE = yes; PARTIAL/STALE/MISSING = no.
const QTONE: Record<string, string> = {
  GOOD: "var(--up)", BRONZE: "var(--warn)", PARTIAL: "var(--warn)",
  STALE: "var(--down)", MISSING: "var(--text-muted)",
};
const QGLYPH: Record<string, string> = {
  GOOD: "✅", BRONZE: "⚠️", PARTIAL: "◑", STALE: "⏳", MISSING: "✗",
};

const TH: React.CSSProperties = {
  textAlign: "left", padding: "7px 10px", fontSize: 11, fontWeight: 600,
  color: "var(--text-dim)", textTransform: "uppercase", letterSpacing: "0.05em",
  borderBottom: "1px solid var(--border)", whiteSpace: "nowrap",
};
const TH_R: React.CSSProperties = { ...TH, textAlign: "right" };
const TD: React.CSSProperties = { padding: "7px 10px", fontSize: 12, borderBottom: "1px solid #141b2b" };
const TD_R: React.CSSProperties = { ...TD, textAlign: "right", fontFamily: "var(--font-mono)" };

/** Compact relative time — "12s ago", "3m ago", "2h ago". Null-safe. */
function ago(iso: string | null): string {
  if (!iso) return "never";
  const t = Date.parse(iso);
  if (Number.isNaN(t)) return "—";
  const s = Math.max(0, Math.floor((Date.now() - t) / 1000));
  if (s < 60) return `${s}s ago`;
  if (s < 3600) return `${Math.floor(s / 60)}m ago`;
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`;
  return `${Math.floor(s / 86400)}d ago`;
}

function daysSince(iso: string | null): number | null {
  if (!iso) return null;
  const t = Date.parse(iso);
  if (Number.isNaN(t)) return null;
  return Math.floor((Date.now() - t) / 86_400_000);
}

/** Derive a health verdict per symbol from the raw signals. */
function verdict(r: Row): { tone: "ok" | "warn" | "bad" | "none"; label: string } {
  if (!r.last_fetched_at_utc && r.coverage_partitions === 0) return { tone: "none", label: "not harvested" };
  const res = (r.last_fetched_result || "").toLowerCase();
  if (res && res !== "ok" && res !== "partial") return { tone: "bad", label: res };
  const stale = (daysSince(r.last_fetched_at_utc) ?? 99) > 4;
  if (r.manifest_violations_last_30d > 0) return { tone: "bad", label: `${r.manifest_violations_last_30d} violations` };
  if (r.missing_days_count > 5 || stale) return { tone: "warn", label: stale ? "stale" : `${r.missing_days_count} gaps` };
  if (res === "partial") return { tone: "warn", label: "partial" };
  return { tone: "ok", label: "healthy" };
}

const TONE: Record<string, string> = {
  ok: "var(--up)", warn: "var(--warn)", bad: "var(--down)", none: "var(--text-muted)",
};

export function HarvestView() {
  const [rows, setRows] = useState<Row[]>([]);
  const [quality, setQuality] = useState<Quality | null>(null);
  const [harvester, setHarvester] = useState<Harvester | null>(null);
  const [harvesterErr, setHarvesterErr] = useState<string | null>(null);
  const [selected, setSelected] = useState<string | null>(null);  // symbol → show its curve
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState<string | null>(null);
  const [q, setQ] = useState("");
  const [onlyIssues, setOnlyIssues] = useState(false);

  useEffect(() => {
    let live = true;
    const load = () => {
      api.barCacheHealth()
        .then((r) => { if (live) { setRows(r.health || []); setErr(null); } })
        .catch((e) => { if (live) setErr(e instanceof Error ? e.message : String(e)); })
        .finally(() => { if (live) setLoading(false); });
      // Decision-grade quality (good-for-today). Best-effort — its own catch so
      // a quality hiccup never blanks the health table.
      api.barCacheQuality()
        .then((r) => { if (live) setQuality(r); })
        .catch(() => { if (live) setQuality(null); });
      // NEW C# IBKRBarHarvester (IBKR-primary → ibkr_price_bars). Own catch so a
      // harvester hiccup never blanks the legacy bar-cache table.
      api.ibkrHarvesterStatus()
        .then((r) => { if (live) { setHarvester(r); setHarvesterErr(null); } })
        .catch((e) => { if (live) { setHarvester(null); setHarvesterErr(e instanceof Error ? e.message : String(e)); } });
    };
    void load();
    const t = setInterval(load, 60_000);
    return () => { live = false; clearInterval(t); };
  }, []);

  // canonical → decision-grade quality, for the per-row badge.
  const qmap = useMemo(() => {
    const m = new Map<string, QRow>();
    for (const s of quality?.symbols || []) m.set(s.canonical, s);
    return m;
  }, [quality]);

  const summary = useMemo(() => {
    const s = { total: rows.length, healthy: 0, warn: 0, bad: 0, none: 0, missing: 0 };
    for (const r of rows) {
      const v = verdict(r).tone;
      if (v === "ok") s.healthy++; else if (v === "warn") s.warn++;
      else if (v === "bad") s.bad++; else s.none++;
      s.missing += r.missing_days_count || 0;
    }
    return s;
  }, [rows]);

  const shown = useMemo(() => {
    let r = rows;
    if (q.trim()) r = r.filter((x) => x.canonical.toLowerCase().includes(q.trim().toLowerCase()));
    if (onlyIssues) r = r.filter((x) => verdict(x).tone === "warn" || verdict(x).tone === "bad");
    // Issues first, then by symbol.
    const rank = { bad: 0, warn: 1, none: 2, ok: 3 } as Record<string, number>;
    return [...r].sort((a, b) => {
      const d = rank[verdict(a).tone] - rank[verdict(b).tone];
      return d !== 0 ? d : a.canonical.localeCompare(b.canonical);
    });
  }, [rows, q, onlyIssues]);

  if (loading && rows.length === 0) return <div style={{ padding: 20, color: "var(--text-dim)" }}>Loading harvest health…</div>;
  if (err) return <div style={{ padding: 20, color: "var(--down)" }}>Data-trust API unavailable: {err}</div>;

  return (
    <div style={{ padding: "4px 2px" }}>
      <div style={{ display: "flex", alignItems: "baseline", gap: 12, marginBottom: 12, flexWrap: "wrap" }}>
        <h2 style={{ margin: 0, fontSize: 16 }}>Harvest · Data Health</h2>
        <span style={{ fontSize: 12, color: "var(--text-muted)" }}>
          bar-cache coverage, freshness + missing-data issues · auto-refresh 60s
        </span>
      </div>

      {/* NEW: C# IBKR bar harvester — the IBKR-primary intraday feed. This is the
          answer to "is IBKR actually harvesting?" — separate from the legacy
          yfinance bar-cache table below. */}
      <HarvesterPanel h={harvester} err={harvesterErr} />

      {/* Legacy yfinance bar-cache health (daily coverage / quality-for-today). */}
      <div style={{ display: "flex", alignItems: "baseline", gap: 8, margin: "4px 0 8px" }}>
        <span style={{ fontSize: 13, fontWeight: 700, color: "var(--text-dim)" }}>Legacy bar-cache (daily · yfinance)</span>
        <span style={{ fontSize: 11, color: "var(--text-muted)" }}>
          daily coverage for backtests + decision-grade "good for today"
        </span>
      </div>

      {/* Summary strip */}
      <div style={{ display: "flex", gap: 10, flexWrap: "wrap", marginBottom: 14 }}>
        {quality && (
          <Stat
            label="Good for today"
            value={`${quality.summary.good_for_today}/${quality.summary.total}`}
            tone={quality.summary.good_for_today === quality.summary.total ? "ok"
              : quality.summary.good_for_today === 0 ? "bad" : "warn"}
          />
        )}
        <Stat label="Tracked" value={summary.total} tone="none" />
        <Stat label="Healthy" value={summary.healthy} tone="ok" />
        <Stat label="Warnings" value={summary.warn} tone="warn" />
        <Stat label="Errors" value={summary.bad} tone="bad" />
        <Stat label="Not harvested" value={summary.none} tone="none" />
        <Stat label="Σ missing days" value={summary.missing} tone={summary.missing > 0 ? "warn" : "ok"} />
      </div>

      {/* Controls */}
      <div style={{ display: "flex", gap: 10, marginBottom: 10, alignItems: "center" }}>
        <input
          value={q} onChange={(e) => setQ(e.target.value)} placeholder="Filter symbol…"
          style={{ fontSize: 12, padding: "5px 9px", maxWidth: 200 }}
        />
        <label style={{ fontSize: 12, color: "var(--text-dim)", display: "flex", gap: 6, alignItems: "center", cursor: "pointer" }}>
          <input type="checkbox" checked={onlyIssues} onChange={(e) => setOnlyIssues(e.target.checked)} />
          Issues only
        </label>
        <span style={{ fontSize: 12, color: "var(--text-muted)", marginLeft: "auto" }}>{shown.length} shown</span>
      </div>

      <div style={{ overflowX: "auto", maxWidth: "100%" }}>
        <table style={{ width: "100%", borderCollapse: "collapse", minWidth: 760 }}>
          <thead>
            <tr>
              <th style={TH}>Symbol</th>
              <th style={TH}>Good today?</th>
              <th style={TH}>Class</th>
              <th style={TH}>Status</th>
              <th style={TH}>Coverage</th>
              <th style={TH_R}>Months</th>
              <th style={TH_R}>Missing</th>
              <th style={TH}>Last fetch</th>
              <th style={TH}>Provider</th>
              <th style={TH_R}>Violations</th>
            </tr>
          </thead>
          <tbody>
            {shown.map((r) => {
              const v = verdict(r);
              const ds = daysSince(r.last_fetched_at_utc);
              return (
                <tr key={r.canonical}
                  onClick={() => setSelected(selected === r.canonical ? null : r.canonical)}
                  title="Click to show the price curve"
                  style={{
                    borderBottom: "1px solid #141b2b", cursor: "pointer",
                    background: selected === r.canonical ? "var(--surface-2)" : undefined,
                  }}>
                  <td style={{ ...TD, fontWeight: 700, fontFamily: "var(--font-mono)" }}>{r.canonical}</td>
                  <td style={TD}>
                    {(() => {
                      const qrow = qmap.get(r.canonical);
                      if (!qrow) return <span style={{ color: "var(--text-muted)" }}>—</span>;
                      return (
                        <span title={qrow.reason} style={{ color: QTONE[qrow.score], fontWeight: 600, cursor: "help" }}>
                          {QGLYPH[qrow.score]} {qrow.score}
                          {qrow.days_behind != null && qrow.score !== "GOOD" ? ` ${qrow.days_behind}d` : ""}
                        </span>
                      );
                    })()}
                  </td>
                  <td style={{ ...TD, color: "var(--text-dim)" }}>{r.asset_class || "—"}</td>
                  <td style={TD}>
                    <span style={{ color: TONE[v.tone], fontWeight: 600 }}>●</span>{" "}
                    <span style={{ color: TONE[v.tone] }}>{v.label}</span>
                  </td>
                  <td style={{ ...TD, fontFamily: "var(--font-mono)", color: "var(--text-dim)" }}>
                    {r.coverage_start_date ? `${r.coverage_start_date} → ${r.coverage_end_date}` : "—"}
                  </td>
                  <td style={TD_R}>{r.coverage_partitions || 0}</td>
                  <td style={{ ...TD_R, color: r.missing_days_count > 0 ? "var(--warn)" : "var(--text-dim)" }}>
                    {r.missing_days_count || 0}
                  </td>
                  <td style={{ ...TD, color: "var(--text-dim)" }}>
                    {ds === null ? "never" : ds === 0 ? "today" : `${ds}d ago`}
                    {r.last_fetched_result ? ` · ${r.last_fetched_result}` : ""}
                  </td>
                  <td style={{ ...TD, color: "var(--text-dim)" }}>{r.last_fetched_provider || "—"}</td>
                  <td style={{ ...TD_R, color: r.manifest_violations_last_30d > 0 ? "var(--down)" : "var(--text-dim)" }}>
                    {r.manifest_violations_last_30d || 0}
                  </td>
                </tr>
              );
            })}
            {shown.length === 0 && (
              <tr><td colSpan={10} style={{ ...TD, color: "var(--text-muted)", textAlign: "center", padding: 20 }}>
                No symbols match.
              </td></tr>
            )}
          </tbody>
        </table>
      </div>

      {/* Click a row → show that symbol's harvested price curve. */}
      {selected && (
        <div style={{ marginTop: 16, background: "var(--surface-1)", border: "1px solid var(--border)", borderRadius: 8, padding: 16 }}>
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 8 }}>
            <span style={{ fontWeight: 700, fontFamily: "var(--font-mono)", fontSize: 14 }}>{selected} — price history</span>
            <button onClick={() => setSelected(null)}
              style={{ background: "none", border: "none", color: "var(--text-muted)", cursor: "pointer", fontSize: 18, lineHeight: 1 }}
              title="Close">✕</button>
          </div>
          <PriceHistoryChart symbol={selected} height={340} />
        </div>
      )}
    </div>
  );
}

/** IBKR bar-harvester observability panel. Answers, at a glance: is it enabled,
 * is IBKR actually the source (vs Yahoo fallback), how far is the backfill, and
 * did the last sweep error? This is the NEW C# harvester → ibkr_price_bars. */
function HarvesterPanel({ h, err }: { h: Harvester | null; err: string | null }) {
  const enabled = !!h?.enabled;
  const ibkr = h?.lastTickIbkr ?? 0;
  const yahoo = h?.lastTickYahoo ?? 0;
  const failed = h?.lastTickFailed ?? 0;
  const total = ibkr + yahoo + failed;
  // Source verdict: green when IBKR is carrying the feed, amber when it's all
  // Yahoo fallback (cold session), grey when nothing ticked yet.
  const srcTone = ibkr > 0 ? "var(--up)" : total > 0 ? "var(--warn)" : "var(--text-muted)";
  const srcLabel = ibkr > 0
    ? (yahoo > 0 ? `IBKR ${ibkr} · Yahoo ${yahoo}` : `IBKR ${ibkr}`)
    : total > 0 ? `Yahoo-only ${yahoo} (IBKR cold)` : "no tick yet";
  const cfg = h?.configuredSymbolCount ?? 0;
  const bf = h?.backfilledSymbols ?? 0;

  return (
    <div style={{
      background: "var(--surface-1)", border: `1px solid ${enabled ? "var(--border)" : "var(--warn)"}`,
      borderRadius: 8, padding: "12px 16px", marginBottom: 14,
    }}>
      <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 10, flexWrap: "wrap" }}>
        <span style={{ fontSize: 14, fontWeight: 700 }}>IBKR Bar Harvester</span>
        <span style={{
          fontSize: 10, fontWeight: 700, textTransform: "uppercase", letterSpacing: "0.05em",
          padding: "2px 8px", borderRadius: 999,
          background: enabled ? "rgba(34,197,94,0.15)" : "rgba(250,204,21,0.15)",
          color: enabled ? "var(--up)" : "var(--warn)",
        }}>{enabled ? "● live" : "○ disabled"}</span>
        <span style={{ fontSize: 11, color: "var(--text-muted)" }}>
          IBKR-primary intraday → ibkr_price_bars · Yahoo fallback (loud) · {h?.resolution || "1m"}
        </span>
        {h && (
          <span style={{ fontSize: 11, color: "var(--text-muted)", marginLeft: "auto" }}>
            last tick {ago(h.lastTickAtUtc)}{h.nextTickEtaUtc ? ` · next ~${ago(h.nextTickEtaUtc).replace(" ago", "")}` : ""}
          </span>
        )}
      </div>

      {err && (
        <div style={{ fontSize: 12, color: "var(--down)" }}>
          harvester-status unavailable: {err}
        </div>
      )}
      {!err && !h && (
        <div style={{ fontSize: 12, color: "var(--text-muted)" }}>Loading harvester status…</div>
      )}
      {!err && h && (
        <>
          <div style={{ display: "flex", gap: 10, flexWrap: "wrap" }}>
            <MiniStat label="Last-tick source" value={srcLabel} color={srcTone} />
            <MiniStat label="Backfilled" value={`${bf}/${cfg}`}
              color={cfg > 0 && bf >= cfg ? "var(--up)" : bf > 0 ? "var(--warn)" : "var(--text-muted)"} />
            <MiniStat label="Bars written (last)" value={h.lastTickBarsWritten ?? 0}
              color={(h.lastTickBarsWritten ?? 0) > 0 ? "var(--text)" : "var(--text-muted)"} />
            <MiniStat label="Failed (last)" value={failed}
              color={failed > 0 ? "var(--down)" : "var(--text-dim)"} />
            <MiniStat label="Interval" value={`${h.intervalSeconds ?? 0}s`} color="var(--text-dim)" />
          </div>
          {cfg > 0 && (
            <div style={{ marginTop: 10 }}>
              <div style={{ height: 6, borderRadius: 999, background: "var(--surface-2)", overflow: "hidden" }}>
                <div style={{
                  height: "100%", width: `${Math.min(100, (bf / cfg) * 100)}%`,
                  background: bf >= cfg ? "var(--up)" : "var(--warn)", transition: "width .3s",
                }} />
              </div>
              <div style={{ fontSize: 10, color: "var(--text-muted)", marginTop: 4 }}>
                backfill catching up — {bf} of {cfg} symbols have history in ibkr_price_bars
              </div>
            </div>
          )}
          {h.lastError && (
            <div style={{ fontSize: 11, color: "var(--down)", marginTop: 8, fontFamily: "var(--font-mono)" }}>
              last error: {h.lastError}
            </div>
          )}
        </>
      )}
    </div>
  );
}

function MiniStat({ label, value, color }: { label: string; value: number | string; color: string }) {
  return (
    <div style={{ background: "var(--surface-2)", border: "1px solid var(--border)", borderRadius: 6, padding: "6px 12px", minWidth: 96 }}>
      <div style={{ fontSize: 9, textTransform: "uppercase", letterSpacing: "0.06em", color: "var(--text-muted)" }}>{label}</div>
      <div style={{ fontSize: 14, fontWeight: 700, fontFamily: "var(--font-mono)", color }}>{value}</div>
    </div>
  );
}

function Stat({ label, value, tone }: { label: string; value: number | string; tone: keyof typeof TONE }) {
  return (
    <div style={{
      background: "var(--surface-2)", border: "1px solid var(--border)", borderRadius: 8,
      padding: "8px 14px", minWidth: 92,
    }}>
      <div style={{ fontSize: 10, textTransform: "uppercase", letterSpacing: "0.06em", color: "var(--text-muted)" }}>{label}</div>
      <div style={{ fontSize: 20, fontWeight: 700, fontFamily: "var(--font-mono)", color: TONE[tone] }}>{value}</div>
    </div>
  );
}

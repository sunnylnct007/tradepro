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

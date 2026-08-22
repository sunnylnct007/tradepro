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
import { CandleIchimokuChart } from "./CandleIchimokuChart";
import { RunLogCard } from "./RunLogCard";

type Row = Awaited<ReturnType<typeof api.barCacheHealth>>["health"][number];
type Quality = Awaited<ReturnType<typeof api.barCacheQuality>>;
type Coverage = Awaited<ReturnType<typeof api.ibkrBarCoverage>>;
type QRow = Quality["symbols"][number];
type Harvester = Awaited<ReturnType<typeof api.ibkrHarvesterStatus>>;
type Readiness = Awaited<ReturnType<typeof api.dataReadiness>>;

/**
 * DataReadinessBanner — the FIRST thing on this screen, because the question a
 * trader arrives with is "can I act on today's numbers?", not "how did each
 * job do?". Everything below is forensics; this is the answer.
 *
 * Owner, 15 Aug 2026: "I don't need noise of failure. I need to know if data
 * is there or not and since when ... if I go to the data screen I have no
 * proper clue." Per-job panels made a 60-run 5-minute outage and an 11-day
 * daily-bar gap invisible — each looked busy, none summed up.
 */
function DataReadinessBanner() {
  const [r, setR] = useState<Readiness | null>(null);
  const [err, setErr] = useState<string | null>(null);
  // Persisted so a deliberate collapse survives the 60s auto-refresh AND a
  // reload — a panel that springs back open every minute is worse than one
  // that never collapsed.
  const [open, setOpen] = useState<boolean>(() => {
    try { return localStorage.getItem("tp.dataHealth.open") !== "0"; }
    catch { return true; }
  });
  useEffect(() => {
    try { localStorage.setItem("tp.dataHealth.open", open ? "1" : "0"); } catch { /* private mode */ }
  }, [open]);
  useEffect(() => {
    let live = true;
    const load = () =>
      api.dataReadiness()
        .then((d) => { if (live) { setR(d); setErr(null); } })
        .catch((e) => { if (live) setErr(e instanceof Error ? e.message : String(e)); });
    load();
    const t = setInterval(load, 60000);
    return () => { live = false; clearInterval(t); };
  }, []);

  if (err) return (
    <div style={{ padding: 12, marginBottom: 14, border: "1px solid var(--down)", borderRadius: 8, color: "var(--down)" }}>
      Data readiness unavailable: {err} — treat every figure below as unverified.
    </div>
  );
  if (!r) return null;

  const allGood = r.usable === r.total;
  const tone = allGood ? "var(--up)" : r.usable >= r.total - 1 ? "var(--warn)" : "var(--down)";
  const ago = (iso: string | null) => {
    if (!iso) return "never";
    const h = (Date.now() - new Date(iso.replace(" ", "T")).getTime()) / 3.6e6;
    if (h < 1) return `${Math.round(h * 60)}m ago`;
    if (h < 48) return `${Math.round(h)}h ago`;
    return `${Math.round(h / 24)}d ago`;
  };

  // Collapsed by default ONLY when everything is usable: a healthy banner is
  // just noise above the panels you came here to read, but a broken lane must
  // never be something you have to expand to discover. The choice is
  // remembered so a deliberate collapse survives the 60s auto-refresh and a
  // page reload.
  const broken = r.datasets.filter((d) => !d.usable);
  return (
    <div style={{ marginBottom: 16, border: `1px solid ${tone}`, borderRadius: 8, overflow: "hidden" }}>
      <button
        onClick={() => setOpen(!open)}
        aria-expanded={open}
        title={open ? "Collapse data-health detail" : "Expand data-health detail"}
        style={{ display: "flex", alignItems: "baseline", gap: 12, padding: "10px 14px", width: "100%",
                 background: "color-mix(in srgb, var(--panel) 92%, transparent)", flexWrap: "wrap",
                 border: "none", borderRadius: 0, textAlign: "left", cursor: "pointer",
                 font: "inherit", color: "inherit" }}
      >
        <span style={{ color: tone, fontSize: 11, width: 10, flex: "0 0 auto" }}>{open ? "▾" : "▸"}</span>
        <strong style={{ fontSize: 15, color: tone }}>{r.verdict}</strong>
        <span style={{ fontSize: 12, color: "var(--text-muted)" }}>
          {r.usable} of {r.total} datasets usable · checked {ago(r.generatedAtUtc)}
        </span>
        {/* Collapsed must still NAME what is broken — a count you have to open
            the panel to interpret is exactly the silent failure this screen
            exists to prevent. */}
        {!open && broken.length > 0 && (
          <span style={{ fontSize: 12, color: "var(--down)", fontWeight: 600 }}>
            ⛔ {broken.map((d) => d.label).join(", ")}
          </span>
        )}
      </button>
      <div hidden={!open}>
        {r.datasets.map((d) => (
          <div key={d.key} style={{ display: "grid", gridTemplateColumns: "22px 210px 120px 1fr",
                                    gap: 10, padding: "8px 14px", alignItems: "start",
                                    borderTop: "1px solid var(--border)", fontSize: 12 }}>
            <span style={{ color: d.usable ? "var(--up)" : "var(--down)" }}>{d.usable ? "✅" : "⛔"}</span>
            <div>
              <div style={{ fontWeight: 600 }}>{d.label}</div>
              <div style={{ color: "var(--text-muted)", fontSize: 11 }}>{d.purpose}</div>
            </div>
            <div style={{ color: "var(--text-dim)" }}>
              {d.usable ? `current ${ago(d.asOfUtc)}` : (
                <span style={{ color: "var(--down)" }}>
                  broken<br />{d.brokenSince ? ago(d.brokenSince) : "—"}
                </span>
              )}
            </div>
            <div style={{ color: d.usable ? "var(--text-dim)" : "var(--text)" }}>{d.detail}</div>
          </div>
        ))}
      </div>
      <div hidden={!open} style={{ padding: "6px 14px", fontSize: 11, color: "var(--text-muted)",
                    borderTop: "1px solid var(--border)" }}>
        {r.note}
      </div>
    </div>
  );
}

// Decision-grade tier → colour + glyph. The headline question: "good enough to
// decide on TODAY?" GOOD/BRONZE = yes; PARTIAL/STALE/MISSING = no.
// BRONZE answers YES to "good enough to decide on today" — it means the bars
// are COMPLETE but came from the yfinance fallback rather than IBKR. It is a
// PROVENANCE note, not a defect. Painting it amber with a ⚠️ next to a green
// "healthy · 0 missing" is what made this table read as self-contradictory:
// the glyph said broken while every number on the row said fine. Bronze now
// reads as a pass with a provenance mark.
const QTONE: Record<string, string> = {
  GOOD: "var(--up)", BRONZE: "var(--up)", PARTIAL: "var(--warn)",
  STALE: "var(--down)", MISSING: "var(--text-muted)",
};
const QGLYPH: Record<string, string> = {
  GOOD: "✅", BRONZE: "✅", PARTIAL: "◑", STALE: "⏳", MISSING: "✗",
};
const QNOTE: Record<string, string> = {
  GOOD: "complete, from IBKR — the golden source",
  BRONZE: "complete data, but sourced from the yfinance fallback rather than IBKR. Usable today; lower provenance.",
  PARTIAL: "the session is incomplete — fewer bars than the venue's calendar expects",
  STALE: "no recent fetch — this symbol has stopped being harvested",
  MISSING: "no data at all",
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

/** True when this row's numbers are counted in intraday BARS, not in days. */
function isIntraday(r: Row): boolean {
  const res = (r.last_fetched_resolution || "").toLowerCase();
  return res.endsWith("m") || res.endsWith("h");
}

/** Derive a health verdict per symbol from the raw signals. */
function verdict(r: Row): { tone: "ok" | "warn" | "bad" | "none"; label: string } {
  if (!r.last_fetched_at_utc && r.coverage_partitions === 0) return { tone: "none", label: "not harvested" };
  const res = (r.last_fetched_result || "").toLowerCase();
  if (res && res !== "ok" && res !== "partial") return { tone: "bad", label: res };
  const stale = (daysSince(r.last_fetched_at_utc) ?? 99) > 4;
  if (r.manifest_violations_last_30d > 0) return { tone: "bad", label: `${r.manifest_violations_last_30d} violations` };
  // `missing_days_count` is a MISNOMER: the harvester computes it as
  // rows_expected − rows_returned (bar_cache_harvest.py), so on a 1m/5m lane it
  // counts missing BARS WITHIN ONE SESSION, not missing days. WMT showing "70
  // gaps" was 320 of 390 one-minute bars — i.e. 70 minutes in which a
  // moderately-traded name simply didn't print. Comparing that against a
  // threshold written for DAYS is what turned 187 of 251 symbols yellow.
  // Intraday lanes are therefore judged on staleness and violations only; a
  // sparse minute is a fact about the tape, not a harvest failure.
  const gapsMatter = !isIntraday(r);
  if ((gapsMatter && r.missing_days_count > 5) || stale) {
    return { tone: "warn", label: stale ? "stale" : `${r.missing_days_count} missing days` };
  }
  // A "partial" LAST FETCH is almost always just today's INCOMPLETE session
  // during market hours (or ≤5 harmless historical gaps, caught above) — not a
  // data problem for a symbol that is fresh with a complete deep history. Don't
  // flag the whole hub yellow for it; it's healthy, just mid-session.
  if (res === "partial") return { tone: "ok", label: "fresh · today partial" };
  return { tone: "ok", label: "healthy" };
}

const TONE: Record<string, string> = {
  ok: "var(--up)", warn: "var(--warn)", bad: "var(--down)", none: "var(--text-muted)",
};

export function HarvestView() {
  const [rows, setRows] = useState<Row[]>([]);
  // Which resolutions the health table holds at all. Lets the panel say "the
  // daily lane hasn't reported since the re-key" instead of rendering an empty
  // table, which would read as "all your daily data vanished".
  const [availableRes, setAvailableRes] = useState<string[]>([]);
  const [quality, setQuality] = useState<Quality | null>(null);
  const [harvester, setHarvester] = useState<Harvester | null>(null);
  const [harvesterErr, setHarvesterErr] = useState<string | null>(null);
  const [coverage, setCoverage] = useState<Coverage | null>(null);
  const [selected, setSelected] = useState<string | null>(null);  // symbol → show its curve
  const [chartRes, setChartRes] = useState("1d");                  // 1m / 5m / 1d
  const [chartTf, setChartTf] = useState("3M");                    // range window
  // Narrow-screen flag → stack the chart under the table + full-screen pop-out.
  const [isNarrow, setIsNarrow] = useState(
    typeof window !== "undefined" ? window.innerWidth < 900 : false);
  useEffect(() => {
    const onResize = () => setIsNarrow(window.innerWidth < 900);
    window.addEventListener("resize", onResize);
    return () => window.removeEventListener("resize", onResize);
  }, []);
  const [analyzeInput, setAnalyzeInput] = useState("");            // free-text "analyze ANY symbol"
  const [popOut, setPopOut] = useState(false);                     // large chart overlay
  const [maxi, setMaxi] = useState(false);                         // pop-out → full viewport
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState<string | null>(null);
  const [q, setQ] = useState("");
  const [onlyIssues, setOnlyIssues] = useState(false);

  useEffect(() => {
    let live = true;
    const load = () => {
      api.barCacheHealth()
        .then(async (r) => {
          if (!live) return;
          setAvailableRes(r.available || []);
          // FALL BACK RATHER THAN SHOW NOTHING. After migration 064 the health
          // table can legitimately hold no 1d rows until the next 21:30
          // harvest, and rendering an empty table reads as "all the daily data
          // vanished" — a false alarm dressed as a fact. If the default lane is
          // empty but another has rows, show that one and SAY which.
          if ((r.health || []).length === 0 && (r.available || []).length > 0) {
            const alt = (r.available || []).find((x) => x !== r.resolution);
            if (alt) {
              const r2 = await api.barCacheHealth({ resolution: alt });
              if (!live) return;
              setRows(r2.health || []);
              setErr(null);
              return;
            }
          }
          setRows(r.health || []);
          setErr(null);
        })
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
      // How far the central ibkr_price_bars store has been harvested (1m/1d depth
      // per symbol). Own catch so a coverage hiccup never blanks the page.
      api.ibkrBarCoverage()
        .then((r) => { if (live) setCoverage(r); })
        .catch(() => { if (live) setCoverage(null); });
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

  // ONE ROW PER SYMBOL (22 Aug 2026). The health table carries rows for both
  // asset-class trees (us_etf + a retired us_equity twin for ~250 symbols),
  // and the display keyed rows by canonical alone — duplicate React keys made
  // filtering leave stale phantom rows behind (the "1 shown but 13 visible"
  // screenshot). us_etf is the single canonical tree now: prefer its row,
  // fall back to whatever exists for symbols that only ever lived elsewhere.
  const dedupedRows = useMemo(() => {
    const byCanonical = new Map<string, Row>();
    for (const r of rows) {
      const prev = byCanonical.get(r.canonical);
      if (!prev || (r.asset_class === "us_etf" && prev.asset_class !== "us_etf")) {
        byCanonical.set(r.canonical, r);
      }
    }
    return [...byCanonical.values()];
  }, [rows]);

  const summary = useMemo(() => {
    const s = { total: dedupedRows.length, healthy: 0, warn: 0, bad: 0, none: 0, missing: 0 };
    for (const r of dedupedRows) {
      const v = verdict(r).tone;
      if (v === "ok") s.healthy++; else if (v === "warn") s.warn++;
      else if (v === "bad") s.bad++; else s.none++;
      s.missing += r.missing_days_count || 0;
    }
    return s;
  }, [dedupedRows]);

  // Which harvest resolutions actually wrote the rows on screen. More than one
  // means you are looking at the overwrite described above, not at a genuine
  // per-symbol difference.
  const shownRes = useMemo(
    () => [...new Set(dedupedRows.map((r) => r.last_fetched_resolution).filter(Boolean) as string[])].sort(),
    [dedupedRows]);

  const shown = useMemo(() => {
    let r = dedupedRows;
    if (q.trim()) r = r.filter((x) => x.canonical.toLowerCase().includes(q.trim().toLowerCase()));
    if (onlyIssues) r = r.filter((x) => verdict(x).tone === "warn" || verdict(x).tone === "bad");
    // Issues first, then by symbol.
    const rank = { bad: 0, warn: 1, none: 2, ok: 3 } as Record<string, number>;
    return [...r].sort((a, b) => {
      const d = rank[verdict(a).tone] - rank[verdict(b).tone];
      return d !== 0 ? d : a.canonical.localeCompare(b.canonical);
    });
  }, [dedupedRows, q, onlyIssues]);

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

      <DataReadinessBanner />

      {/* Full run-log stream — moved here from the main dashboard (owner,
          22 Aug 2026): the dashboard keeps a one-line indicator; the detail
          lives with the rest of the data-operations story. Identical rows
          are collapsed ×N inside the card. */}
      <div style={{ marginBottom: 12 }}>
        <RunLogCard />
      </div>

      {/* NEW: C# IBKR bar harvester — the IBKR-primary intraday feed. This is the
          answer to "is IBKR actually harvesting?" — separate from the legacy
          yfinance bar-cache table below. */}
      <HarvesterPanel
        h={harvester}
        err={harvesterErr}
        onChanged={() =>
          api.ibkrHarvesterStatus()
            .then((r) => { setHarvester(r); setHarvesterErr(null); })
            .catch((e) => { setHarvester(null); setHarvesterErr(e instanceof Error ? e.message : String(e)); })}
      />

      {/* HOW FAR harvested — the central ibkr_price_bars depth (1m/1d, per symbol). */}
      <CoveragePanel c={coverage} />

      {/* Bar-cache health. NOTE the heading no longer claims "Daily": the
          bar_cache_health table is keyed ON CONFLICT (canonical, asset_class)
          with NO resolution, so the 1m, 5m and daily harvests OVERWRITE each
          other's row. Whichever ran last wins. Calling this panel "Daily
          bar-cache" while it displayed the 1m harvest's numbers is what made
          it unreadable — a 2-week coverage window, 251 symbols instead of the
          daily lane's 179, and "gaps" that were missing MINUTES. Until the
          table gains resolution in its key, the honest thing is to name the
          resolution actually on screen. */}
      <div style={{ display: "flex", alignItems: "baseline", gap: 8, margin: "4px 0 8px", flexWrap: "wrap" }}>
        <span style={{ fontSize: 13, fontWeight: 700, color: "var(--text-dim)" }}>Bar-cache health (IBKR-primary · yfinance fallback)</span>
        <span style={{ fontSize: 11, color: "var(--text-muted)" }}>
          decision-grade "good for today" per symbol
        </span>
        {/* Empty 1d table right after migration 064 is EXPECTED, not an
            outage: pre-migration rows carry whichever lane last overwrote them
            (mostly 1m), so the daily lane repopulates on its next 21:30 run.
            Saying that is the difference between a known wait and a panic. */}
        {rows.length === 0 && availableRes.length > 0 && (
          <span style={{ fontSize: 11, padding: "2px 8px", borderRadius: 999,
                         border: "1px solid var(--warn)", color: "var(--warn)" }}
                title={`The health table currently holds: ${availableRes.join(", ")}. Before migration 064 the 1d/5m/1m harvests shared one row per symbol and overwrote each other, so existing rows carry whichever lane wrote last. Each lane repopulates its own rows on its next run — 1d and 1m nightly at 21:30/21:15, 5m within 30 minutes.`}>
            daily lane not reported since the re-key — holds {availableRes.join(", ")}; repopulates on the next 21:30 harvest
          </span>
        )}
        {shownRes.length > 0 && (
          <span
            title={shownRes.length > 1
              ? "These rows come from DIFFERENT harvest resolutions. bar_cache_health is keyed on (canonical, asset_class) with no resolution, so each harvest overwrites the last one's row for a symbol — the mix below is that overwrite, not a real difference between symbols."
              : `Every row below was written by the ${shownRes[0]} harvest — the most recent one to run. Other resolutions overwrite this same table.`}
            style={{ fontSize: 11, padding: "2px 8px", borderRadius: 999, cursor: "help",
                     border: `1px solid ${shownRes.length > 1 ? "var(--warn)" : "var(--border)"}`,
                     color: shownRes.length > 1 ? "var(--warn)" : "var(--text-muted)" }}
          >
            showing {shownRes.join(" + ")}{shownRes.length > 1 ? " — mixed, see tooltip" : ""}
          </span>
        )}
      </div>

      {/* Summary strip */}
      <div style={{ display: "flex", gap: 10, flexWrap: "wrap", marginBottom: 14 }}>
        {quality && (
          <Stat
            label={quality.last_completed_session
              ? `Good as of ${quality.last_completed_session}`
              : "Good for today"}
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
        {/* Analyze ANY symbol — type a ticker (not just the tracked ones) → chart it. */}
        <form
          onSubmit={(e) => { e.preventDefault(); const s = analyzeInput.trim().toUpperCase(); if (s) setSelected(s); }}
          style={{ display: "flex", gap: 6, alignItems: "center", marginLeft: "auto" }}
        >
          <input
            value={analyzeInput} onChange={(e) => setAnalyzeInput(e.target.value)}
            placeholder="Analyze any symbol…"
            style={{ fontSize: 12, padding: "5px 9px", maxWidth: 180 }}
          />
          <button type="submit" style={{ fontSize: 12, padding: "5px 12px", borderRadius: 6, cursor: "pointer",
            border: "1px solid var(--accent, #4f8cff)", background: "rgba(79,140,255,0.12)", color: "var(--accent, #4f8cff)" }}>
            Chart
          </button>
        </form>
        <span style={{ fontSize: 12, color: "var(--text-muted)" }}>{shown.length} shown</span>
      </div>

      {/* Table LEFT + chart RAIL right (like the cockpit) so the chart is
          visible without scrolling to the bottom. Stacks on narrow screens. */}
      <div style={{ display: "flex", flexDirection: isNarrow ? "column" : "row", gap: 14, alignItems: "flex-start", marginTop: 4 }}>
      <div style={{ flex: 1, minWidth: 0, width: "100%", overflowX: "auto" }}>
        <table style={{ width: "100%", borderCollapse: "collapse", minWidth: 760 }}>
          <thead>
            <tr>
              <th style={TH}>Symbol</th>
              <th style={TH}>Good today?</th>
              {/* "Store", not "Class" (22 Aug 2026). This column shows which
                  CANONICAL TREE the bars live in — and us_etf is the
                  everything-bucket for US listings, single stocks included.
                  Labelled "Class" it read as an instrument classification and
                  flatly contradicted get_instrument_fit, which correctly calls
                  MU a single_stock. Two classifiers appeared to disagree when
                  only one of them was classifying anything. */}
              <th style={TH} title="Which canonical bar-store tree holds this symbol — us_etf (all US listings, stocks included), uk_equity (LSE), index_us / index_uk (context series). This is STORAGE, not an instrument type: for what an instrument IS, see get_instrument_fit.">Store</th>
              <th style={TH}>Status</th>
              <th style={TH}>Coverage</th>
              <th style={TH_R}>Months</th>
              <th style={TH_R} title="rows_expected − rows_returned for the last harvest of this symbol. On a DAILY lane that is missing sessions. On a 1m/5m lane it is missing BARS inside one session — a minute in which the name simply did not print, which is normal for anything but the most liquid names.">
                Missing
              </th>
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
                <tr key={`${r.canonical}|${r.asset_class}`}
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
                        <span title={`${QNOTE[qrow.score] ?? ""}\n\n${qrow.reason ?? ""}`.trim()}
                              style={{ color: QTONE[qrow.score], fontWeight: 600, cursor: "help" }}>
                          {QGLYPH[qrow.score]} {qrow.score}
                          {qrow.days_behind != null && qrow.days_behind > 0
                            ? ` ${qrow.days_behind} session${qrow.days_behind === 1 ? "" : "s"} behind`
                            : ""}
                        </span>
                      );
                    })()}
                  </td>
                  <td style={{ ...TD, color: "var(--text-dim)" }}
                      title="Canonical store tree (not an instrument classification)">{r.asset_class || "—"}</td>
                  <td style={TD}>
                    <span style={{ color: TONE[v.tone], fontWeight: 600 }}>●</span>{" "}
                    <span style={{ color: TONE[v.tone] }}>{v.label}</span>
                  </td>
                  <td style={{ ...TD, fontFamily: "var(--font-mono)", color: "var(--text-dim)" }}>
                    {r.coverage_start_date ? `${r.coverage_start_date} → ${r.coverage_end_date}` : "—"}
                  </td>
                  <td style={TD_R}>{r.coverage_partitions || 0}</td>
                  {/* Amber only when the number MEANS a gap. On an intraday
                      lane it counts unprinted minutes, which is a property of
                      the tape, not a harvest fault — colouring that amber is
                      what turned most of this table yellow. */}
                  <td style={{ ...TD_R, color: (r.missing_days_count > 0 && !isIntraday(r))
                                 ? "var(--warn)" : "var(--text-dim)" }}
                      title={isIntraday(r)
                        ? `${r.missing_days_count || 0} one-minute bars absent from the last ${r.last_fetched_resolution} session — minutes with no print. Normal outside the most liquid names.`
                        : `${r.missing_days_count || 0} missing sessions`}>
                    {r.missing_days_count || 0}
                    <span style={{ color: "var(--text-muted)", fontSize: 10 }}>
                      {isIntraday(r) ? " bars" : " days"}
                    </span>
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
      </div>{/* end table left column */}

      {/* Chart RAIL — sticky on the right (cockpit-style), so it stays in view
          the moment you click a symbol. Stacks full-width under the table on
          narrow / mobile screens. */}
      {selected && (
        <div style={{
          width: isNarrow ? "100%" : 480, flexShrink: 0,
          position: isNarrow ? "static" : "sticky", top: 8, alignSelf: "flex-start",
          maxHeight: isNarrow ? undefined : "calc(100vh - 24px)", overflow: "auto",
          background: "var(--surface-1)", border: "1px solid var(--border)", borderRadius: 8, padding: 14,
        }}>
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 8 }}>
            <span style={{ fontWeight: 700, fontFamily: "var(--font-mono)", fontSize: 14 }}>{selected} — Ichimoku + S/R</span>
            <span style={{ display: "flex", gap: 12, alignItems: "center" }}>
              <button onClick={() => setPopOut(true)}
                style={{ fontSize: 11, fontWeight: 600, padding: "3px 10px", borderRadius: 6, cursor: "pointer",
                  border: "1px solid var(--border)", background: "var(--surface-2)", color: "var(--text-dim)" }}
                title="Pop out to a large view">⤢ Pop out</button>
              <button onClick={() => setSelected(null)}
                style={{ background: "none", border: "none", color: "var(--text-muted)", cursor: "pointer", fontSize: 18, lineHeight: 1 }}
                title="Close">✕</button>
            </span>
          </div>
          <FlexChartControls coverage={coverage} symbol={selected}
            res={chartRes} setRes={setChartRes} tf={chartTf} setTf={setChartTf} />
          <CandleIchimokuChart symbol={selected} timeframe={chartTf} resolution={chartRes} height={360} />
        </div>
      )}
      </div>{/* end flex row */}

      {/* Pop-out: the same chart in a large overlay. ⛶ maximizes to the full
          viewport (mobile is always full-screen — a padded modal on a phone
          wastes the pixels the trader came for). */}
      {selected && popOut && (
        <div
          onClick={() => setPopOut(false)}
          style={{ position: "fixed", inset: 0, background: "rgba(0,0,0,0.6)", zIndex: 1000,
            display: "flex", alignItems: "center", justifyContent: "center",
            padding: isNarrow || maxi ? 0 : 24 }}
        >
          <div onClick={(e) => e.stopPropagation()}
            style={{ background: "var(--surface-1)",
              border: isNarrow || maxi ? "none" : "1px solid var(--border)",
              borderRadius: isNarrow || maxi ? 0 : 10, padding: isNarrow ? 12 : 18,
              width: isNarrow || maxi ? "100vw" : "min(1200px, 94vw)",
              height: isNarrow || maxi ? "100vh" : undefined,
              maxHeight: isNarrow || maxi ? "100vh" : "92vh", overflow: "auto" }}>
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 10 }}>
              <span style={{ fontWeight: 700, fontFamily: "var(--font-mono)", fontSize: 16 }}>{selected} — Ichimoku cloud + S/R</span>
              <span style={{ display: "flex", alignItems: "center", gap: 4 }}>
                {!isNarrow && (
                  <button onClick={() => setMaxi((v) => !v)}
                    style={{ background: "none", border: "none", color: "var(--text-muted)", cursor: "pointer", fontSize: 18, lineHeight: 1, padding: "0 6px" }}
                    title={maxi ? "Restore windowed view" : "Maximize to full screen"}>
                    {maxi ? "🗗" : "⛶"}
                  </button>
                )}
                <button onClick={() => setPopOut(false)}
                  style={{ background: "none", border: "none", color: "var(--text-muted)", cursor: "pointer", fontSize: 26, lineHeight: 1, padding: "0 6px" }}
                  title="Close">✕</button>
              </span>
            </div>
            <FlexChartControls coverage={coverage} symbol={selected}
              res={chartRes} setRes={setChartRes} tf={chartTf} setTf={setChartTf} />
            <CandleIchimokuChart symbol={selected} timeframe={chartTf} resolution={chartRes}
              height={isNarrow || maxi
                ? Math.max(440, (typeof window !== "undefined" ? window.innerHeight : 800) - 190)
                : 640} />
          </div>
        </div>
      )}
    </div>
  );
}

// Range windows offered per resolution — intraday resolutions get short
// windows, daily gets the long ones. (Resolution "1m"/"5m" pull from the deep
// IBKR store; "1d" pulls Yahoo daily.)
const TF_FOR_RES: Record<string, string[]> = {
  "1m": ["1D", "5D", "1M"],
  "5m": ["5D", "1M", "3M"],
  "1d": ["3M", "6M", "1Y", "5Y"],
};
const RES_OPTS = [
  { k: "1m", label: "1-min" },
  { k: "5m", label: "5-min" },
  { k: "1d", label: "Daily" },
];

/** Resolution + range pills for the flexible chart, plus a "what data exists"
 * strip for the selected symbol (from the harvest coverage) so the user can see
 * exactly what's available before/while charting it. */
function FlexChartControls({ coverage, symbol, res, setRes, tf, setTf }: {
  coverage: Coverage | null; symbol: string;
  res: string; setRes: (r: string) => void; tf: string; setTf: (t: string) => void;
}) {
  const tfs = TF_FOR_RES[res] ?? TF_FOR_RES["1d"];
  const rows = (coverage?.coverage ?? []).filter((c) => c.symbol === symbol);
  const pill = (active: boolean): React.CSSProperties => ({
    fontSize: 11, fontWeight: 600, padding: "3px 9px", borderRadius: 6, cursor: "pointer",
    border: `1px solid ${active ? "var(--accent, #3b82f6)" : "var(--border)"}`,
    background: active ? "var(--accent, #3b82f6)" : "var(--surface-2)",
    color: active ? "#fff" : "var(--text-dim)",
  });
  return (
    <div style={{ marginBottom: 10 }}>
      <div style={{ display: "flex", gap: 6, alignItems: "center", flexWrap: "wrap" }}>
        <span style={{ fontSize: 10, color: "var(--text-muted)", textTransform: "uppercase", marginRight: 2 }}>Resolution</span>
        {RES_OPTS.map((o) => (
          <button key={o.k} style={pill(res === o.k)}
            onClick={() => { setRes(o.k); if (!(TF_FOR_RES[o.k] ?? []).includes(tf)) setTf((TF_FOR_RES[o.k] ?? ["3M"])[0]); }}>
            {o.label}
          </button>
        ))}
        <span style={{ fontSize: 10, color: "var(--text-muted)", textTransform: "uppercase", margin: "0 2px 0 10px" }}>Range</span>
        {tfs.map((t) => (
          <button key={t} style={pill(tf === t)} onClick={() => setTf(t)}>{t}</button>
        ))}
      </div>
      <div style={{ fontSize: 10.5, color: "var(--text-muted)", marginTop: 6, fontFamily: "var(--font-mono)" }}>
        {/* THE NOTICE MUST MATCH THE STORE THE CHART ACTUALLY READS.
            `rows` describes ibkr_price_bars — the DEEP INTRADAY store. The
            daily chart does not read it (the component fetches daily candles
            from the bar cache; only 1m/5m come from ibkr_price_bars). So on
            Daily this printed "no harvested data for this symbol yet"
            directly above a full chart — MRVL, 16 Aug 2026, showing
            LATEST 2026-08-14 O 221.40 H 223.52 L 217.10 C 222.02. The label
            contradicted the picture underneath it. Scope it to the intraday
            resolutions, and on Daily say which store the notice is about
            rather than implying the symbol has nothing. */}
        {res !== "1d" && <span style={{ color: "var(--warn)" }}>deep IBKR store · </span>}
        {rows.length === 0
          ? (res === "1d"
              ? "daily candles come from the bar cache — the deep intraday store holds nothing for this symbol"
              : "no harvested data for this symbol yet")
          : rows.map((r) => {
              const ib = r.bars > 0 ? Math.round((100 * r.ibkrBars) / r.bars) : 0;
              return `${r.resolution}: ${(r.firstTs ?? "?").slice(0, 10)}→${(r.lastTs ?? "?").slice(0, 10)} · ${r.bars.toLocaleString()} bars · ${ib}% IBKR`;
            }).join("   |   ")}
      </div>
    </div>
  );
}

/** "How far has data been harvested" — the central ibkr_price_bars store depth.
 * Per-resolution totals (IBKR vs Yahoo split + earliest bar) and, on expand, the
 * per-symbol first→last window + bar counts. Answers "do we have enough 1m data,
 * and how far back does it go?" directly on screen. */
function CoveragePanel({ c }: { c: Coverage | null }) {
  const [open, setOpen] = useState(false);
  const [q, setQ] = useState("");
  if (!c || c.byResolution.length === 0) return null;
  const fmtBars = (n: number) => (n >= 1000 ? `${(n / 1000).toFixed(0)}k` : String(n));
  const earliest = (res: string) => {
    const ts = c.coverage.filter((x) => x.resolution === res && x.firstTs).map((x) => x.firstTs!);
    return ts.length ? [...ts].sort()[0].slice(0, 10) : "—";
  };
  const allRows = [...c.coverage].sort(
    (a, b) => a.symbol.localeCompare(b.symbol) || a.resolution.localeCompare(b.resolution));
  const rows = q.trim()
    ? allRows.filter((r) => r.symbol.toLowerCase().includes(q.trim().toLowerCase()))
    : allRows;
  // Clear per-symbol indication: which symbols have 1m (intraday-chartable) vs
  // only 1d, so a gap is obvious at a glance.
  const has1m = new Set(c.coverage.filter((x) => x.resolution === "1m" && x.bars > 0).map((x) => x.symbol));
  const symCount = new Set(c.coverage.map((x) => x.symbol)).size;
  return (
    <div style={{ marginBottom: 16, border: "1px solid #1b2233", borderRadius: 8, padding: "10px 12px", background: "#0d1320" }}>
      <div style={{ display: "flex", alignItems: "baseline", gap: 8, flexWrap: "wrap" }}>
        <span style={{ fontSize: 13, fontWeight: 700 }}>Central store — how far harvested</span>
        <span style={{ fontSize: 11, color: "var(--text-muted)" }}>
          ibkr_price_bars · IBKR-primary, Yahoo fallback · as of {ago(c.generatedAtUtc)}
        </span>
      </div>
      <div style={{ display: "flex", gap: 10, flexWrap: "wrap", margin: "10px 0 6px" }}>
        {c.byResolution.map((r) => (
          <div key={r.resolution} style={{ border: "1px solid #1b2233", borderRadius: 6, padding: "6px 12px", minWidth: 190 }}>
            <div style={{ fontSize: 12, fontWeight: 700 }}>{r.resolution} · {r.symbols} symbols</div>
            <div style={{ fontSize: 11, color: "var(--text-dim)", fontFamily: "var(--font-mono)" }}>
              {fmtBars(r.totalBars)} bars · {fmtBars(r.ibkrBars)} IBKR + {fmtBars(r.yahooBars)} Yahoo
            </div>
            <div style={{ fontSize: 11, color: "var(--text-muted)" }}>back to {earliest(r.resolution)}</div>
          </div>
        ))}
      </div>
      <div style={{ display: "flex", gap: 10, alignItems: "center", flexWrap: "wrap" }}>
        <button
          onClick={() => setOpen((o) => !o)}
          style={{ fontSize: 11, background: "none", border: "1px solid #1b2233", borderRadius: 5, color: "var(--text-dim)", padding: "3px 8px", cursor: "pointer" }}>
          {open ? "Hide" : "Show"} per-symbol depth ({symCount} symbols)
        </button>
        {/* Clear indication: how many symbols actually have intraday 1m data. */}
        <span style={{ fontSize: 10.5, color: has1m.size === symCount ? "var(--up)" : "var(--warn)" }}>
          {has1m.size}/{symCount} have 1-min data
        </span>
        {open && (
          <input
            value={q} onChange={(e) => setQ(e.target.value)} placeholder="search symbol…"
            style={{ fontSize: 11, padding: "3px 8px", borderRadius: 5, border: "1px solid #1b2233",
              background: "var(--surface-1)", color: "var(--text)", width: 160 }} />
        )}
      </div>
      {open && (
        <div style={{ maxHeight: 360, overflow: "auto", marginTop: 8 }}>
          {rows.length === 0 && (
            <div style={{ fontSize: 11, color: "var(--text-muted)", padding: "8px 2px" }}>
              No harvested rows match “{q}”. {!has1m.has(q.trim().toUpperCase()) && q.trim() ? "(this symbol has no 1-min harvest yet)" : ""}
            </div>
          )}
          <table style={{ borderCollapse: "collapse", width: "100%" }}>
            <thead><tr>
              <th style={TH}>Symbol</th><th style={TH}>Res</th><th style={TH}>First → Last</th>
              <th style={TH_R}>Bars</th><th style={TH_R}>IBKR</th><th style={TH_R}>Yahoo</th><th style={TH}>Last capture</th>
            </tr></thead>
            <tbody>
              {rows.map((r, i) => (
                <tr key={`${r.symbol}:${r.resolution}:${i}`}>
                  <td style={{ ...TD, fontWeight: 700 }}>{r.symbol}</td>
                  <td style={TD}>{r.resolution}</td>
                  <td style={{ ...TD, fontFamily: "var(--font-mono)", fontSize: 11 }}>
                    {(r.firstTs?.slice(0, 10) ?? "—")} → {(r.lastTs?.slice(0, 10) ?? "—")}
                  </td>
                  <td style={TD_R}>{r.bars}</td>
                  <td style={TD_R}>{r.ibkrBars}</td>
                  <td style={{ ...TD_R, color: r.yahooBars > 0 ? "var(--warn)" : "var(--text-muted)" }}>{r.yahooBars}</td>
                  <td style={{ ...TD, color: "var(--text-muted)" }}>{ago(r.lastCapturedUtc)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

/** IBKR bar-harvester observability panel. Answers, at a glance: is it enabled,
 * is IBKR actually the source (vs Yahoo fallback), how far is the backfill, and
 * did the last sweep error? This is the NEW C# harvester → ibkr_price_bars. */
function HarvesterPanel({ h, err, onChanged }: { h: Harvester | null; err: string | null; onChanged: () => void }) {
  const [busy, setBusy] = useState(false);
  const paused = !!h?.paused;
  // Pause = hand the single IBKR Web-API session back so the user can log into
  // the IBKR portal (only one session per account). Resume = take it back.
  const toggle = async () => {
    setBusy(true);
    try {
      if (paused) await api.ibkrResume();
      else await api.ibkrPause("portal login");
      onChanged();
    } catch { /* the status refresh will show the real state */ }
    finally { setBusy(false); }
  };
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
        {paused && (
          <span style={{
            fontSize: 10, fontWeight: 700, textTransform: "uppercase", letterSpacing: "0.05em",
            padding: "2px 8px", borderRadius: 999, background: "rgba(250,204,21,0.18)", color: "var(--warn)",
          }}>⏸ paused · portal free</span>
        )}
        <span style={{ fontSize: 11, color: "var(--text-muted)" }}>
          IBKR-primary intraday → ibkr_price_bars · Yahoo fallback (loud) · {h?.resolution || "1m"}
        </span>
        <span style={{ marginLeft: "auto", display: "flex", alignItems: "center", gap: 10 }}>
          {h && (
            <span style={{ fontSize: 11, color: "var(--text-muted)" }}>
              last tick {ago(h.lastTickAtUtc)}{!paused && h.nextTickEtaUtc ? ` · next ~${ago(h.nextTickEtaUtc).replace(" ago", "")}` : ""}
            </span>
          )}
          <button
            onClick={toggle} disabled={busy}
            title={paused ? "Resume IBKR harvesting (take the session back)" : "Pause IBKR + log the session out so you can use the IBKR portal"}
            style={{
              fontSize: 11, fontWeight: 700, padding: "4px 12px", borderRadius: 6, cursor: busy ? "wait" : "pointer",
              border: `1px solid ${paused ? "var(--up)" : "var(--warn)"}`,
              background: paused ? "rgba(34,197,94,0.12)" : "rgba(250,204,21,0.12)",
              color: paused ? "var(--up)" : "var(--warn)", opacity: busy ? 0.6 : 1,
            }}>
            {busy ? "…" : paused ? "▶ Resume IBKR" : "⏸ Pause for portal"}
          </button>
        </span>
      </div>

      {paused && (
        <div style={{ fontSize: 11, color: "var(--warn)", marginBottom: 8, lineHeight: 1.4 }}>
          IBKR session released — <b>log into the IBKR Client Portal now</b>. Harvesting + account-state are paused
          until you press Resume{h?.pausedAtUtc ? ` (paused ${ago(h.pausedAtUtc)})` : ""}.
        </div>
      )}

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
          {h.lastError && (() => {
            // "idle — outside US market hours" and "paused — session released"
            // are STATES, not errors — painting them red made a healthy
            // weekend look broken (owner, 22 Aug). Neutral ink + honest label.
            const isState = /^(idle|paused)\b/i.test(h.lastError);
            return (
              <div style={{ fontSize: 11, color: isState ? "var(--text-muted)" : "var(--down)", marginTop: 8, fontFamily: "var(--font-mono)" }}>
                {isState ? "status" : "last error"}: {h.lastError}
              </div>
            );
          })()}
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

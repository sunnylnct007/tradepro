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
type QRow = Quality["symbols"][number];
type Readiness = Awaited<ReturnType<typeof api.dataReadiness>>;
type Fundamentals = Awaited<ReturnType<typeof api.fundamentals>>;

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
  // CURRENT fundamentals per symbol. Owner, 29 Aug 2026: "this figure shd be
  // visible on our data harvesting screen as well" — this screen already lists
  // every symbol we hold data for, and could not say whether any of them makes
  // money. Own catch: a fundamentals hiccup must never blank the health table.
  const [fund, setFund] = useState<Fundamentals | null>(null);
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
      api.fundamentals()
        .then((r) => { if (live) setFund(r); })
        .catch(() => { if (live) setFund(null); });
    };
    void load();
    const t = setInterval(load, 60_000);
    return () => { live = false; clearInterval(t); };
  }, []);

  // canonical → decision-grade quality, for the per-row badge.
  // symbol -> current fundamentals, built once rather than scanned per row.
  const fmap = useMemo(
    () => new Map((fund?.fundamentals || []).map((f) => [f.symbol, f])),
    [fund],
  );
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
              {/* FUNDAMENTALS. A CURRENT snapshot, never a backtest input —
                  today's P/E on a past date is look-ahead. A dash means the
                  store has no figure, which for an ETF is the truth and for a
                  single name means the harvest has not reached it. */}
              <th style={TH_R} title="Trailing P/E — CURRENT snapshot from the last fundamentals harvest, not a point-in-time figure. Blank for ETFs, which have none by construction.">P/E</th>
              <th style={TH_R} title="Return on equity, current snapshot. Negative means the company is losing money on its equity base.">ROE</th>
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
                  {(() => {
                    // A dash is a real answer here, not a gap in the UI: ETFs
                    // have no P/E by construction. Loss-making names show a
                    // negative ROE in the down colour, because "-55%" scanned
                    // past in grey is how an unprofitable company ends up on a
                    // shortlist next to a profitable one.
                    const f = fmap.get(r.canonical);
                    const pe = f?.trailingpe;
                    const roe = f?.returnonequity;
                    return (
                      <>
                        <td style={{ ...TD_R, color: "var(--text-dim)" }}>
                          {pe == null ? "—" : pe.toFixed(1)}
                        </td>
                        <td style={{ ...TD_R, color: roe == null ? "var(--text-dim)"
                              : roe < 0 ? "var(--down)" : "var(--text-dim)" }}>
                          {roe == null ? "—" : `${(roe * 100).toFixed(1)}%`}
                        </td>
                      </>
                    );
                  })()}
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
          <FlexChartControls symbol={selected}
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
            <FlexChartControls symbol={selected}
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
function FlexChartControls({ res, setRes, tf, setTf }: {
  symbol: string;
  res: string; setRes: (r: string) => void; tf: string; setTf: (t: string) => void;
}) {
  const tfs = TF_FOR_RES[res] ?? TF_FOR_RES["1d"];
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
        {/* Every resolution reads ONE store (docs/DATA_ARCHITECTURE.md); the chart
            itself shows the series' provenance and newest bar. */}
        golden bar store · /marketdata/candles
      </div>
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

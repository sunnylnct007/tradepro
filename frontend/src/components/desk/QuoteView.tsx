/**
 * QuoteView — the /desk "Quote" view (IBKR-Desktop "Quote/Chart" nav).
 *
 * A READ-ONLY emulation of IBKR's Quote/Chart screen, three zones:
 *
 *   - LEFT  · instrument list — the union of (a) current position symbols
 *             (T212 + IG + IBKR, the same sources PositionsTable merges) and
 *             (b) the curated UK watchlist (api.ukWatchlist). Each row shows
 *             ticker · last close · change% computed from REAL candles (with a
 *             position's currentPrice as a fallback for the last). Click a row
 *             to select; the first holding is default-selected.
 *
 *   - CENTER · candlestick/price chart for the selected symbol via the EXISTING
 *             <PriceHistoryChart/> component (which itself fetches api.candles —
 *             we reuse it rather than add a charting lib). Timeframe PILLS
 *             (house rule: no dropdowns) — 1W · 1M · 3M · 1Y · 5Y — drive the
 *             chart's lookback window (interval stays "1d", the only interval
 *             the marketdata endpoint is exercised with elsewhere). The symbol +
 *             last close + change sit above the chart.
 *
 *   - RIGHT · instrument-detail rail. Every stat is derived from the REAL candle
 *             series we fetch here (Last, Change/Change%, Day O/H/L, 52w H/L,
 *             Volume) — see deriveStats(). We have NO live bid/ask feed, so we
 *             OMIT bid/ask rather than fabricate it. Below it, a Position panel
 *             (Shares · Avg · Mkt value · Unrealised P&L · %) shown ONLY when the
 *             selected symbol is actually held; otherwise "No position".
 *
 * READ-ONLY: no Buy/Sell buttons are rendered at all (IBKR shows them; we don't).
 *
 * Honesty rule (user: never fabricate): when candles return nothing we show
 * "No data" — we never invent a series or a price.
 *
 * Mobile: below MOBILE_BREAKPOINT the three zones stack (list → chart → rail)
 * in a single scrolling column, consistent with the other desk views.
 */
import { useEffect, useMemo, useState } from "react";
import { api } from "../../api/client";
import { config } from "../../config";
import type { Candle, CandleSeries, Watchlist } from "../../api/types";
import { CandleIchimokuChart } from "./CandleIchimokuChart";
import { chartSymbolFor } from "../../util/brokerSymbols";
import { fmtMoney, fmtNum, fmtPct, signColour } from "./deskFormat";
import { absoluteVolumeCaveat } from "../../lib/volumeUnits";

const MOBILE_BREAKPOINT = 760;
const SEP = "#1b2233";

/** Timeframe pills → lookback window in calendar days. Interval stays "1d"
 * (the only interval the marketdata candles endpoint is exercised with across
 * the app — PriceHistoryChart, sparklineCache — so we don't risk an empty
 * intraday response); the window is what each pill changes. */
const TIMEFRAMES: { label: string; days: number }[] = [
  { label: "1M", days: 31 },
  { label: "3M", days: 93 },
  { label: "6M", days: 186 },
  { label: "1Y", days: 365 },
  { label: "5Y", days: 365 * 5 },
];

/** A held position, normalised across the three broker sources. */
type Held = {
  broker: "T212" | "IG" | "IBKR";
  ticker: string;            // broker ticker (display)
  chartSymbol: string | null; // Yahoo symbol for candles (null = no honest source, e.g. IG FX)
  qty: number;
  avg: number | null;
  currentPrice: number | null;
  unrealisedAbs: number | null;
  unrealisedPct: number | null;
  ccy: string | null;
  company: string | null;
};

/** A selectable instrument in the left list (position and/or watchlist). */
type Instrument = {
  symbol: string;            // the symbol we fetch candles for + key by
  label: string;             // display label (ticker)
  company: string | null;
  held: Held | null;         // position row when held, else null
  fromWatchlist: boolean;
};

function useIsMobile(): boolean {
  const [mobile, setMobile] = useState(
    typeof window !== "undefined" ? window.innerWidth <= MOBILE_BREAKPOINT : false,
  );
  useEffect(() => {
    const onResize = () => setMobile(window.innerWidth <= MOBILE_BREAKPOINT);
    window.addEventListener("resize", onResize);
    return () => window.removeEventListener("resize", onResize);
  }, []);
  return mobile;
}

/** `initialSymbol` (optional): when /desk drills in from a clicked position or
 * order row, the candle symbol to default-select. Matched against an
 * instrument's `symbol`, else its broker ticker `label`; if it isn't in the
 * list yet (e.g. a watchlist-less name) we still seed `selected` with it so the
 * chart attempts candles for it. Falls back to the first holding when absent. */
export function QuoteView({ initialSymbol }: { initialSymbol?: string | null }) {
  const mobile = useIsMobile();
  const [instruments, setInstruments] = useState<Instrument[]>([]);
  const [notes, setNotes] = useState<string[]>([]);
  const [loading, setLoading] = useState(true);
  const [selected, setSelected] = useState<string | null>(initialSymbol ?? null);
  const [tf, setTf] = useState<string>("1Y");

  // Build the instrument list: held positions (T212+IG+IBKR) ∪ watchlist.
  useEffect(() => {
    let live = true;
    const load = async () => {
      const msgs: string[] = [];
      const held: Held[] = [];

      const t212 = await api.t212Positions("demo").catch((e) => {
        msgs.push(`T212: ${e instanceof Error ? e.message : e}`);
        return null;
      });
      if (t212?.enabled && t212.positions) {
        for (const p of t212.positions) {
          held.push({
            broker: "T212",
            ticker: p.ticker,
            chartSymbol: p.yahooSymbol ?? chartSymbolFor(p.ticker, "T212"),
            qty: p.quantity,
            avg: p.averagePricePaid,
            currentPrice: p.currentPrice,
            unrealisedAbs: p.unrealisedAbs,
            unrealisedPct: p.unrealisedPct,
            ccy: p.currency,
            company: p.yahooSymbol ?? null,
          });
        }
      } else if (t212 && !t212.enabled) {
        msgs.push("T212 disabled");
      }

      const ig = await api.igPositions().catch((e) => {
        msgs.push(`IG: ${e instanceof Error ? e.message : e}`);
        return null;
      });
      if (ig?.enabled && ig.positions) {
        for (const p of ig.positions) {
          held.push({
            broker: "IG",
            ticker: p.ticker,
            // IG FX/CFD epics resolve to the underlying Yahoo series (FX pair →
            // "<PAIR>=X", share CFD → underlying ticker) so the chart loads.
            chartSymbol: chartSymbolFor(p.ticker, "IG"),
            qty: p.quantity,
            avg: p.averagePricePaid,
            currentPrice: p.currentPrice,
            unrealisedAbs: p.unrealisedAbs,
            unrealisedPct: p.unrealisedPct,
            ccy: null,
            company: p.instrumentName ?? null,
          });
        }
      } else if (ig && !ig.enabled) {
        msgs.push("IG disabled");
      }

      const ibkr = await api.ibkrPositions().catch((e) => {
        msgs.push(`IBKR: ${e instanceof Error ? e.message : e}`);
        return null;
      });
      if (ibkr?.enabled && ibkr.positions && !ibkr.error) {
        for (const p of ibkr.positions) {
          held.push({
            broker: "IBKR",
            ticker: p.ticker ?? "—",
            // IBKR US-equity tickers ARE the Yahoo symbol (EC/BABA/MRVL/APLD) →
            // resolve so the chart loads; unmappable names stay null.
            chartSymbol: chartSymbolFor(p.ticker, "IBKR"),
            qty: p.quantity,
            avg: p.averagePricePaid,
            currentPrice: p.currentPrice,
            unrealisedAbs: p.unrealisedAbs,
            unrealisedPct: p.unrealisedPct,
            ccy: p.currency,
            company: p.instrumentName ?? null,
          });
        }
      } else if (ibkr && !ibkr.enabled) {
        msgs.push("IBKR disabled");
      } else if (ibkr?.error) {
        msgs.push(`IBKR: ${ibkr.error}`);
      }

      const wl = await api.ukWatchlist().catch((e) => {
        msgs.push(`Watchlist: ${e instanceof Error ? e.message : e}`);
        return null as Watchlist | null;
      });

      // Merge by symbol. A position's candle symbol (when present) is the key
      // so a held name lines up with its watchlist entry; otherwise the broker
      // ticker keys it. Watchlist-only names key by their symbol.
      const byKey = new Map<string, Instrument>();
      for (const h of held) {
        const key = (h.chartSymbol ?? h.ticker).trim();
        if (!key) continue;
        const existing = byKey.get(key);
        if (existing) {
          // Prefer the row with an honest chart symbol / keep first held.
          if (!existing.held) existing.held = h;
        } else {
          byKey.set(key, {
            symbol: h.chartSymbol ?? h.ticker,
            label: h.ticker,
            company: h.company,
            held: h,
            fromWatchlist: false,
          });
        }
      }
      if (wl?.items) {
        for (const it of wl.items) {
          const key = it.symbol.trim();
          if (!key) continue;
          const existing = byKey.get(key);
          if (existing) {
            existing.fromWatchlist = true;
            if (!existing.company) existing.company = it.label || null;
          } else {
            byKey.set(key, {
              symbol: it.symbol,
              label: it.symbol,
              company: it.label || null,
              held: null,
              fromWatchlist: true,
            });
          }
        }
      }

      // Held names first (so the default selection is a holding), then watchlist.
      const list = [...byKey.values()].sort((a, b) => {
        if (!!a.held !== !!b.held) return a.held ? -1 : 1;
        return a.label.localeCompare(b.label);
      });

      if (!live) return;
      setInstruments(list);
      setNotes(msgs);
      setLoading(false);
      // Default-select: keep the current selection if it resolves to a listed
      // instrument (by candle symbol OR broker ticker — a drill-in from a
      // position row may pass either); otherwise the first holding with an
      // honest chart symbol, else the first instrument.
      setSelected((cur) => {
        if (cur) {
          const match = list.find((i) => i.symbol === cur || i.label === cur);
          if (match) return match.symbol;
          // Not in the list (e.g. a name with no position/watchlist row): still
          // honour it so the chart attempts candles for it.
          return cur;
        }
        const firstChartable = list.find((i) => i.held && hasChart(i));
        return (firstChartable ?? list[0])?.symbol ?? null;
      });
    };
    load().catch((e) => {
      if (live) { setNotes([e instanceof Error ? e.message : String(e)]); setLoading(false); }
    });
    return () => { live = false; };
  }, []);

  const selectedInstrument = useMemo(() => {
    if (!selected) return null;
    const found = instruments.find((i) => i.symbol === selected || i.label === selected);
    if (found) return found;
    // A drilled-in symbol that isn't a held/watchlist row: synthesise a
    // minimal instrument so the chart still attempts candles for it rather
    // than showing "Select an instrument".
    return {
      symbol: selected, label: selected, company: null,
      held: null, fromWatchlist: false,
    } satisfies Instrument;
  }, [instruments, selected]);

  if (loading && instruments.length === 0) {
    return <Panel><Note>Loading instruments…</Note></Panel>;
  }
  if (instruments.length === 0) {
    return (
      <Panel>
        <Note>No instruments — no open positions and no watchlist names.</Note>
        {notes.length > 0 && <SubNote>{notes.join(" · ")}</SubNote>}
      </Panel>
    );
  }

  const list = (
    <InstrumentList
      instruments={instruments}
      selected={selected}
      onSelect={setSelected}
      mobile={mobile}
    />
  );

  const center = (
    <ChartZone
      instrument={selectedInstrument}
      tf={tf}
      onTf={setTf}
    />
  );

  const rail = (
    <DetailRail instrument={selectedInstrument} tf={tf} />
  );

  return (
    <div>
      <div
        style={{
          display: "grid",
          gridTemplateColumns: mobile ? "1fr" : "240px minmax(0, 1fr) 300px",
          gap: 14,
          alignItems: "start",
        }}
      >
        {list}
        {center}
        {rail}
      </div>
      {notes.length > 0 && (
        <div style={{ fontSize: 10, color: "var(--text-muted)", marginTop: 8 }}>
          {notes.join(" · ")}
        </div>
      )}
    </div>
  );
}

function hasChart(i: Instrument): boolean {
  // We always have a candle symbol for an instrument we list by symbol; the
  // only "no chart" case is an IG/IBKR held row whose symbol is a broker
  // ticker with no Yahoo mapping — but those still attempt candles by symbol.
  // We try candles for everything; "No data" handles the misses honestly.
  return !!i.symbol;
}

// ---------------------------------------------------------------------------
// LEFT — instrument list
// ---------------------------------------------------------------------------

function InstrumentList({
  instruments, selected, onSelect, mobile,
}: {
  instruments: Instrument[];
  selected: string | null;
  onSelect: (sym: string) => void;
  mobile: boolean;
}) {
  return (
    <div
      style={{
        border: `1px solid ${SEP}`, borderRadius: 8,
        background: "rgba(255,255,255,0.015)",
        // On phone the list is a short, scrollable strip above the chart so it
        // never dominates the viewport; on desktop it fills the column.
        maxHeight: mobile ? 240 : "calc(100vh - 140px)",
        overflowY: "auto",
      }}
    >
      <div
        style={{
          fontSize: 11, fontWeight: 700, color: "var(--text-dim)",
          padding: "8px 10px", borderBottom: `1px solid ${SEP}`,
          position: "sticky", top: 0, background: "#0d1117", zIndex: 1,
        }}
      >
        Instruments · {instruments.length}
      </div>
      {instruments.map((i) => (
        <InstrumentRow
          key={i.symbol}
          instrument={i}
          on={i.symbol === selected}
          onSelect={() => onSelect(i.symbol)}
        />
      ))}
    </div>
  );
}

/** One left-list row. Fetches its own ~recent candles to show last + change%
 * (real). Falls back to the position's currentPrice for "last" when candles
 * are unavailable; change% shows "n/a" rather than a fabricated number. */
function InstrumentRow({
  instrument, on, onSelect,
}: { instrument: Instrument; on: boolean; onSelect: () => void }) {
  const [last, setLast] = useState<number | null>(null);
  const [changePct, setChangePct] = useState<number | null>(null);
  const [haveCandles, setHaveCandles] = useState(false);

  useEffect(() => {
    let live = true;
    setLast(null); setChangePct(null); setHaveCandles(false);
    const to = new Date();
    const from = new Date(Date.now() - 14 * 24 * 3600 * 1000); // ~2w → last 2 closes
    api.candles({
      symbol: instrument.symbol,
      provider: config.defaultProvider,
      interval: "1d",
      from: from.toISOString().slice(0, 10),
      to: to.toISOString().slice(0, 10),
    })
      .then((s) => {
        if (!live) return;
        const closes = (s.candles ?? [])
          .map((c) => c.close)
          .filter((v): v is number => typeof v === "number" && Number.isFinite(v));
        if (closes.length >= 1) {
          setHaveCandles(true);
          const l = closes[closes.length - 1];
          setLast(l);
          if (closes.length >= 2) {
            const prev = closes[closes.length - 2];
            if (prev) setChangePct(((l - prev) / prev) * 100);
          }
        }
      })
      .catch(() => { /* honest: leave null, fall back to position price below */ });
    return () => { live = false; };
  }, [instrument.symbol]);

  // Fallback "last" from the position currentPrice when candles are absent.
  const shownLast = last ?? instrument.held?.currentPrice ?? null;
  const ccy = instrument.held?.ccy ?? null;

  return (
    <button
      onClick={onSelect}
      style={{
        display: "block", width: "100%", textAlign: "left",
        background: on ? "rgba(79,140,255,0.12)" : "transparent",
        border: "none",
        borderLeft: `3px solid ${on ? "var(--accent, #4f8cff)" : "transparent"}`,
        borderBottom: "1px solid #141b2b",
        padding: "7px 10px", cursor: "pointer", color: "var(--text)",
      }}
    >
      <div style={{ display: "flex", alignItems: "baseline", justifyContent: "space-between", gap: 6 }}>
        <span style={{ fontWeight: 700, fontSize: 12 }}>{instrument.label}</span>
        <span style={{ fontSize: 12, fontFamily: "monospace", color: "var(--text)" }}>
          {shownLast != null ? fmtMoney(shownLast, ccy) : "—"}
        </span>
      </div>
      <div style={{ display: "flex", alignItems: "baseline", justifyContent: "space-between", gap: 6, marginTop: 2 }}>
        <span style={{ display: "flex", gap: 4, alignItems: "center" }}>
          {instrument.held && <Tag broker={instrument.held.broker} />}
          {instrument.fromWatchlist && !instrument.held && (
            <span style={{ fontSize: 8, color: "var(--text-muted)", letterSpacing: "0.04em" }}>WATCH</span>
          )}
        </span>
        <span style={{ fontSize: 11, fontFamily: "monospace", color: signColour(changePct) }}>
          {haveCandles && changePct != null ? fmtPct(changePct) : "n/a"}
        </span>
      </div>
    </button>
  );
}

function Tag({ broker }: { broker: "T212" | "IG" | "IBKR" }) {
  const colour = broker === "IG" ? "#4f8cff" : broker === "IBKR" ? "#d4793b" : "#1fc16b";
  return (
    <span
      title={broker}
      style={{
        fontSize: 8, fontWeight: 700, letterSpacing: "0.04em",
        color: colour, border: `1px solid ${colour}`, borderRadius: 4,
        padding: "0 3px",
      }}
    >
      {broker}
    </span>
  );
}

// ---------------------------------------------------------------------------
// CENTER — chart + timeframe pills
// ---------------------------------------------------------------------------

function ChartZone({
  instrument, tf, onTf,
}: {
  instrument: Instrument | null;
  tf: string;
  onTf: (tf: string) => void;
}) {
  if (!instrument) {
    return <Panel><Note>Select an instrument.</Note></Panel>;
  }
  return (
    <div style={{ border: `1px solid ${SEP}`, borderRadius: 8, background: "rgba(255,255,255,0.015)", padding: 12 }}>
      <div style={{ display: "flex", alignItems: "baseline", justifyContent: "space-between", flexWrap: "wrap", gap: 8, marginBottom: 8 }}>
        <div style={{ fontSize: 14, fontWeight: 800, color: "var(--text)" }}>
          {instrument.label}
          {instrument.company && instrument.company !== instrument.label && (
            <span style={{ fontSize: 11, fontWeight: 400, color: "var(--text-muted)", marginLeft: 8 }}>
              {instrument.company}
            </span>
          )}
        </div>
        <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
          {TIMEFRAMES.map((t) => (
            <button
              key={t.label}
              type="button"
              onClick={() => onTf(t.label)}
              style={{
                fontSize: 11, padding: "3px 10px", borderRadius: 999,
                border: `1px solid ${tf === t.label ? "var(--accent, #4f8cff)" : SEP}`,
                background: tf === t.label ? "rgba(79,140,255,0.18)" : "rgba(0,0,0,0.18)",
                color: tf === t.label ? "#9dc1ff" : "var(--text-muted)",
                cursor: "pointer",
              }}
            >
              {t.label}
            </button>
          ))}
        </div>
      </div>
      {/* Professional candlestick + Ichimoku-cloud chart (lightweight-charts).
          It fetches api.candles itself, padding the lookback so the cloud is
          computed across the WHOLE visible window. The timeframe pill above
          drives the displayed window. */}
      <CandleIchimokuChart
        symbol={instrument.symbol}
        timeframe={tf}
        height={360}
        ccy={instrument.held?.ccy ?? null}
      />
    </div>
  );
}

// ---------------------------------------------------------------------------
// RIGHT — instrument detail (from candles) + position panel
// ---------------------------------------------------------------------------

type Stats = {
  last: number | null;
  prevClose: number | null;
  changeAbs: number | null;
  changePct: number | null;
  dayOpen: number | null;
  dayHigh: number | null;
  dayLow: number | null;
  high52w: number | null;
  low52w: number | null;
  volume: number | null;
  asOf: string | null;
};

/** Derive every right-rail stat from the REAL candle series. Nothing here is
 * fabricated: Last = latest close, Change = latest vs prior close, Day O/H/L =
 * latest candle's o/h/l, 52w H/L = min/max close over ~252 trailing bars,
 * Volume = latest candle volume. */
function deriveStats(candles: Candle[]): Stats {
  if (candles.length === 0) {
    return {
      last: null, prevClose: null, changeAbs: null, changePct: null,
      dayOpen: null, dayHigh: null, dayLow: null,
      high52w: null, low52w: null, volume: null, asOf: null,
    };
  }
  const latest = candles[candles.length - 1];
  const prev = candles.length >= 2 ? candles[candles.length - 2] : null;
  const last = latest.close;
  const prevClose = prev ? prev.close : null;
  const changeAbs = prevClose != null ? last - prevClose : null;
  const changePct = prevClose ? (changeAbs! / prevClose) * 100 : null;
  const window = candles.slice(Math.max(0, candles.length - 252));
  const closes = window.map((c) => c.close).filter((v) => Number.isFinite(v));
  return {
    last,
    prevClose,
    changeAbs,
    changePct,
    dayOpen: latest.open,
    dayHigh: latest.high,
    dayLow: latest.low,
    high52w: closes.length ? Math.max(...closes) : null,
    low52w: closes.length ? Math.min(...closes) : null,
    volume: Number.isFinite(latest.volume) ? latest.volume : null,
    asOf: latest.timestamp.slice(0, 10),
  };
}

function DetailRail({ instrument, tf }: { instrument: Instrument | null; tf: string }) {
  const [series, setSeries] = useState<CandleSeries | null>(null);
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const days = TIMEFRAMES.find((t) => t.label === tf)?.days ?? 365;
  // 52w stats need ≥1y of bars regardless of the chart window, so fetch at
  // least a year here (more when the chart shows a longer window).
  const statDays = Math.max(days, 365);
  const sym = instrument?.symbol ?? null;

  useEffect(() => {
    if (!sym) { setSeries(null); return; }
    let live = true;
    setLoading(true); setErr(null); setSeries(null);
    const to = new Date();
    const from = new Date(Date.now() - statDays * 24 * 3600 * 1000);
    api.candles({
      symbol: sym,
      provider: config.defaultProvider,
      interval: "1d",
      from: from.toISOString().slice(0, 10),
      to: to.toISOString().slice(0, 10),
    })
      .then((s) => { if (live) setSeries(s); })
      .catch((e) => { if (live) setErr(e instanceof Error ? e.message : String(e)); })
      .finally(() => { if (live) setLoading(false); });
    return () => { live = false; };
  }, [sym, statDays]);

  if (!instrument) {
    return <Panel><Note>—</Note></Panel>;
  }

  const stats = deriveStats(series?.candles ?? []);
  // Absolute volume is not something we can stand behind until the store's
  // lot-conversion double-count is repaired. See lib/volumeUnits.
  const volumeCaveat = "the stored volume series is on an unverified scale "
    + "(the IBKR lot conversion ran twice) — it reads about 100x high for some "
    + "months and resolutions, and a single figure has nothing to check it "
    + "against. Restored when the store is repaired after the forward test."
    + (absoluteVolumeCaveat(series?.candles ?? []) ? " This window also changes units partway through." : "");
  const ccy = instrument.held?.ccy ?? null;
  const noData = !loading && (series?.candles.length ?? 0) === 0;

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 14 }}>
      <div style={{ border: `1px solid ${SEP}`, borderRadius: 8, background: "rgba(255,255,255,0.015)", padding: 12 }}>
        <div style={{ fontSize: 11, fontWeight: 700, color: "var(--text-dim)", marginBottom: 8 }}>
          {instrument.label} · Instrument
        </div>

        {err ? (
          <Note tone="down">Quote unavailable: {err}</Note>
        ) : loading && !series ? (
          <Note>Loading…</Note>
        ) : noData ? (
          <Note>No data for this symbol.</Note>
        ) : (
          <>
            <div style={{ display: "flex", alignItems: "baseline", gap: 10, marginBottom: 6 }}>
              <span style={{ fontSize: 22, fontWeight: 800, fontFamily: "monospace", color: "var(--text)" }}>
                {fmtMoney(stats.last, ccy)}
              </span>
              <span style={{ fontSize: 13, fontFamily: "monospace", color: signColour(stats.changeAbs) }}>
                {stats.changeAbs != null ? fmtMoney(stats.changeAbs, ccy, true) : "n/a"}
                {stats.changePct != null ? ` (${fmtPct(stats.changePct)})` : ""}
              </span>
            </div>
            <Stat label="Last" value={fmtMoney(stats.last, ccy)} />
            <Stat label="Prev close" value={fmtMoney(stats.prevClose, ccy)} />
            <Stat label="Day open" value={fmtMoney(stats.dayOpen, ccy)} />
            <Stat label="Day high" value={fmtMoney(stats.dayHigh, ccy)} />
            <Stat label="Day low" value={fmtMoney(stats.dayLow, ccy)} />
            <Stat label="52w high" value={fmtMoney(stats.high52w, ccy)} />
            <Stat label="52w low" value={fmtMoney(stats.low52w, ccy)} />
            {/* ABSOLUTE volume, withheld. A ratio can be sanity-checked against
                its own window; a single number cannot — there is nothing in
                "6,247,757,850" to tell a reader that SPY does about 59 million
                shares a day. The stored series is 100x high in some months and
                at every resolution (the lot conversion ran twice, data lane
                6c22ebd), and a one-day intraday window is uniformly wrong with
                no seam to detect, so the scale-break test can only ever return
                "definitely broken", never "definitely fine".

                So this says nothing rather than something confident and wrong.
                It comes back when the store is repaired after the forward-test
                window. The chart's RVOL and OBV, and the screens' volume ratio,
                are guarded separately — this was the one surface showing volume
                raw. */}
            <Stat label="Volume"
                  value={volumeCaveat ? "withheld" : (stats.volume != null ? fmtNum(stats.volume, 0) : "n/a")}
                  title={volumeCaveat ?? undefined} />
            {stats.asOf && (
              <div style={{ fontSize: 9, color: "var(--text-muted)", marginTop: 6 }}>
                As of {stats.asOf} · daily bars · no live bid/ask feed (omitted, not faked)
              </div>
            )}
          </>
        )}
      </div>

      <PositionPanel held={instrument.held} />
    </div>
  );
}

function PositionPanel({ held }: { held: Held | null }) {
  return (
    <div style={{ border: `1px solid ${SEP}`, borderRadius: 8, background: "rgba(255,255,255,0.015)", padding: 12 }}>
      <div style={{ fontSize: 11, fontWeight: 700, color: "var(--text-dim)", marginBottom: 8 }}>
        Position
      </div>
      {!held ? (
        <Note>No position.</Note>
      ) : (
        <>
          <div style={{ marginBottom: 6 }}>
            <Tag broker={held.broker} />
          </div>
          <Stat label="Shares" value={fmtNum(held.qty)} />
          <Stat label="Avg price" value={fmtMoney(held.avg, held.ccy)} />
          <Stat label="Market value" value={marketValue(held)} />
          <Stat
            label="Unrealised P&L"
            value={fmtMoney(held.unrealisedAbs, held.ccy, true)}
            colour={signColour(held.unrealisedAbs)}
          />
          <Stat
            label="Unrealised %"
            value={fmtPct(held.unrealisedPct)}
            colour={signColour(held.unrealisedPct)}
          />
          <div style={{ fontSize: 9, color: "var(--text-muted)", marginTop: 6 }}>
            Native currency · from {held.broker} positions (never blended)
          </div>
        </>
      )}
    </div>
  );
}

/** Market value = qty × currentPrice when both are present (honest), else n/a.
 * We don't substitute the candle last here so the position panel stays purely
 * a function of the broker position row. */
function marketValue(h: Held): string {
  if (h.currentPrice == null || !Number.isFinite(h.qty)) return "n/a";
  return fmtMoney(h.qty * h.currentPrice, h.ccy);
}

// ---------------------------------------------------------------------------
// Small shared bits
// ---------------------------------------------------------------------------

function Stat({ label, value, colour, title }: { label: string; value: string; colour?: string; title?: string }) {
  return (
    <div title={title} style={{ display: "flex", justifyContent: "space-between", gap: 8, padding: "3px 0", fontSize: 12 }}>
      <span style={{ color: "var(--text-muted)" }}>{label}</span>
      <span style={{ fontFamily: "monospace", color: colour ?? "var(--text)" }}>{value}</span>
    </div>
  );
}

function Panel({ children }: { children: React.ReactNode }) {
  return (
    <div style={{ border: `1px solid ${SEP}`, borderRadius: 8, background: "rgba(255,255,255,0.015)", padding: 12 }}>
      {children}
    </div>
  );
}

function Note({ children, tone }: { children: React.ReactNode; tone?: "down" }) {
  return (
    <div style={{ fontSize: 12, padding: 8, color: tone === "down" ? "var(--down, #ef4444)" : "var(--text-muted)" }}>
      {children}
    </div>
  );
}

function SubNote({ children }: { children: React.ReactNode }) {
  return <div style={{ fontSize: 10, padding: "0 8px 8px", color: "var(--text-muted)" }}>{children}</div>;
}

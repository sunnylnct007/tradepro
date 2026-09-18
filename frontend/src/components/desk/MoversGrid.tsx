/**
 * MoversGrid — today's biggest gainers and losers as a SORTABLE table.
 *
 * Replaces the one-line strip, which could show six names and a percentage
 * each. Owner, 18 Sep 2026: "we can have a better display of market movers
 * biggest and lowest gains ... in grid format that we can sort. they can
 * display the current price, 52 week high etc parameters."
 *
 * WHY THE EXTRA COLUMNS ARE THE POINT. A percentage on its own is not
 * actionable: +7% into a 52-week high and +7% off the floor are different
 * trades, and the strip could not tell them apart. `off high` and `range`
 * place the move inside the year; `vol x20d` says whether anyone showed up
 * for it.
 *
 * NOT A SIGNAL. No strategy has judged these names and none of them has
 * passed a gate — this is the market's list, not the desk's. The footer says
 * so, for the same reason every other surface here does: a table that looks
 * like the candidates table must not be mistaken for one.
 */
import { useMemo, useState } from "react";
import { SortTh } from "../SortTh";
import { useSort } from "../../util/useSort";

export type Mover = {
  symbol: string;
  last: number | null;
  chg_pct: number | null;
  status?: string;
  hi_52w?: number | null;
  lo_52w?: number | null;
  off_hi_pct?: number | null;
  range_pos_pct?: number | null;
  vol_x_20d?: number | null;
  window_sessions?: number | null;
};

const MUTED = "var(--muted)";
const OK = "var(--ok)";
const WARN = "var(--warn)";

const TH: React.CSSProperties = {
  textAlign: "left", padding: "6px 10px", color: MUTED, fontWeight: 500,
  fontSize: 11, textTransform: "uppercase", letterSpacing: ".05em",
  borderBottom: "1px solid var(--border)",
};
const TH_R: React.CSSProperties = { ...TH, textAlign: "right" };
const TD: React.CSSProperties = { padding: "5px 10px", whiteSpace: "nowrap" };
const TD_R: React.CSSProperties = { ...TD, textAlign: "right",
  fontVariantNumeric: "tabular-nums" };

/** A dash, not a zero. A missing number must never read as a measured one. */
const num = (v: number | null | undefined, dp = 2, suffix = "") =>
  v == null || Number.isNaN(v) ? <span style={{ color: MUTED }}>—</span>
    : <>{v.toFixed(dp)}{suffix}</>;

export function MoversGrid({
  gainers, losers, asOfUtc, scanned, onPick,
}: {
  gainers: Mover[];
  losers: Mover[];
  asOfUtc?: string | null;
  scanned?: number | null;
  onPick?: (symbol: string) => void;
}) {
  // Which half to show. Both lists in one table would sort into a single
  // ranking and the "losers" half would simply fall off the bottom — the one
  // thing the owner asked to be able to see.
  const [side, setSide] = useState<"gainers" | "losers">("gainers");
  const rows = side === "gainers" ? (gainers ?? []) : (losers ?? []);

  const { sorted, sortKey, dir, toggle } = useSort<Mover>(
    rows,
    {
      symbol: (r) => r.symbol,
      last: (r) => r.last,
      chg: (r) => r.chg_pct,
      hi: (r) => r.hi_52w ?? null,
      lo: (r) => r.lo_52w ?? null,
      offhi: (r) => r.off_hi_pct ?? null,
      range: (r) => r.range_pos_pct ?? null,
      vol: (r) => r.vol_x_20d ?? null,
    },
    // Default: biggest move first, which is the order the list arrives in and
    // the question being asked ("where is today's action").
    { key: "chg", dir: side === "gainers" ? "desc" : "asc" },
  );

  const pill = (active: boolean): React.CSSProperties => ({
    padding: "3px 10px", borderRadius: 999, fontSize: 12, cursor: "pointer",
    border: `1px solid ${active ? "var(--accent, #4ea1ff)" : "var(--border)"}`,
    color: active ? "var(--accent, #4ea1ff)" : MUTED,
    background: "transparent",
  });

  const shortHistory = useMemo(
    () => sorted.filter((r) => (r.window_sessions ?? 999) < 60).length,
    [sorted],
  );

  return (
    <div style={{ border: "1px solid var(--border)", borderRadius: 8,
                  overflow: "hidden" }}>
      <div style={{ display: "flex", gap: 8, alignItems: "baseline",
                    padding: "8px 10px", flexWrap: "wrap" }}>
        <span style={{ color: MUTED, fontSize: 12, textTransform: "uppercase",
                       letterSpacing: ".06em" }}>Movers</span>
        <button style={pill(side === "gainers")} onClick={() => setSide("gainers")}>
          Gainers {gainers?.length ? `(${gainers.length})` : ""}
        </button>
        <button style={pill(side === "losers")} onClick={() => setSide("losers")}>
          Losers {losers?.length ? `(${losers.length})` : ""}
        </button>
        <span style={{ color: MUTED, fontSize: 12, marginLeft: "auto" }}>
          {String(asOfUtc ?? "").slice(11, 16)}Z
          {scanned ? ` · ${scanned} scanned` : ""} · universe + your list · • = watched
        </span>
      </div>

      {/* Wide content scrolls INSIDE its own box — the page must never
          scroll sideways on a narrow window. */}
      <div style={{ overflowX: "auto" }}>
        <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 13 }}>
          <thead>
            <tr>
              <SortTh label="Symbol" col="symbol" sortKey={sortKey} dir={dir} onSort={toggle} style={TH} />
              <SortTh label="Last" col="last" sortKey={sortKey} dir={dir} onSort={toggle} style={TH_R} />
              <SortTh label="Chg %" col="chg" sortKey={sortKey} dir={dir} onSort={toggle} style={TH_R} />
              <SortTh label="52w high" col="hi" sortKey={sortKey} dir={dir} onSort={toggle} style={TH_R} />
              <SortTh label="52w low" col="lo" sortKey={sortKey} dir={dir} onSort={toggle} style={TH_R} />
              <SortTh label="Off high" col="offhi" sortKey={sortKey} dir={dir} onSort={toggle} style={TH_R} />
              <SortTh label="52w range" col="range" sortKey={sortKey} dir={dir} onSort={toggle} style={TH_R} />
              <SortTh label="Vol x20d" col="vol" sortKey={sortKey} dir={dir} onSort={toggle} style={TH_R} />
            </tr>
          </thead>
          <tbody>
            {sorted.map((m) => (
              <tr key={m.symbol}
                  onClick={() => onPick?.(m.symbol)}
                  style={{ cursor: onPick ? "pointer" : "default",
                           borderBottom: "1px solid var(--border)" }}>
                <td style={{ ...TD, fontWeight: 600 }}>
                  {m.symbol}
                  {m.status === "watch" && (
                    <span title="on your watch list" style={{ color: MUTED }}>&nbsp;•</span>
                  )}
                </td>
                <td style={TD_R}>{num(m.last)}</td>
                <td style={{ ...TD_R, color: (m.chg_pct ?? 0) >= 0 ? OK : WARN,
                             fontWeight: 600 }}>
                  {m.chg_pct == null ? <span style={{ color: MUTED }}>—</span>
                    : `${m.chg_pct > 0 ? "+" : ""}${m.chg_pct.toFixed(2)}%`}
                </td>
                <td style={TD_R}>{num(m.hi_52w)}</td>
                <td style={TD_R}>{num(m.lo_52w)}</td>
                <td style={TD_R}>{num(m.off_hi_pct, 1, "%")}</td>
                <td style={TD_R}>
                  {m.range_pos_pct == null
                    ? <span style={{ color: MUTED }}>—</span>
                    : `${m.range_pos_pct.toFixed(0)}%`}
                </td>
                <td style={TD_R}>{num(m.vol_x_20d, 2, "×")}</td>
              </tr>
            ))}
            {sorted.length === 0 && (
              <tr><td colSpan={8} style={{ ...TD, color: MUTED }}>
                No movers in this cycle — the scan has not published yet.
              </td></tr>
            )}
          </tbody>
        </table>
      </div>

      <div style={{ padding: "7px 10px", fontSize: 11, color: MUTED,
                    borderTop: "1px solid var(--border)" }}>
        <b>Off high</b> = distance below the 52-week high (0% is at it).{" "}
        <b>52w range</b> = where today sits between the year's low (0%) and
        high (100%).{" "}
        <b>Vol x20d</b> = today's volume against its own 20-day average.{" "}
        {shortHistory > 0 && (
          <>{shortHistory} name(s) have under 60 sessions of history, so their
          52-week columns are blank rather than computed from a short window.{" "}</>
        )}
        This is the <b>market's</b> list — no strategy has judged these names and
        none has passed a gate. Not a signal.
      </div>
    </div>
  );
}

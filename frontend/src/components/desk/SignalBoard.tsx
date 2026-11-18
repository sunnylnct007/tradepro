/**
 * SignalBoard — the first thing on the cockpit: BUY, SELL, HOLD.
 *
 * Owner, 13 Nov 2026: "when i go there i shd see a clear signal what to buy
 * and sell and hold", and "the execution in paper trading to support the
 * strategy". So each row is the signal AND what the paper account did about
 * it (waiting / ordered / filled), side by side — the screen shows whether
 * paper trading is actually following the strategy.
 *
 * Source: the momentum rotation sleeve publishes this board at the end of
 * every 15-minute run (artifact today-setups/momentum_rotation). The rows are
 * computed by the same decision the backtest runs; this card only renders.
 * Missing or old data is SAID, never shown as current.
 */
import { useEffect, useState } from "react";

import { api } from "../../api/client";
import { Sparkline } from "./Sparkline";

/**
 * Is a 5-minute "today" figure really TODAY's? (17 Nov 2026: half an hour after
 * the open the column still showed Monday's -5.6% under "Today", because the
 * first 5-minute harvest of the session had not landed.) Bars from today's New
 * York date are current. Once a weekday session has opened, older bars are
 * NOT shown as today. Before the open or at a weekend the last session is
 * shown, named by its day.
 */
const NY = "America/New_York";
const nyDate = (d: Date) => d.toLocaleDateString("en-CA", { timeZone: NY });
export function todayFigure(asOf: string | null | undefined, now: Date = new Date()):
  { show: boolean; label: string | null; title: string } {
  if (!asOf) return { show: false, label: null, title: "no 5-minute bars yet" };
  const bar = new Date(asOf);
  const t = bar.toLocaleTimeString("en-GB", { timeZone: NY, hour: "2-digit", minute: "2-digit" });
  const day = bar.toLocaleDateString("en-GB", { timeZone: NY, weekday: "short" });
  if (nyDate(bar) === nyDate(now)) return { show: true, label: null, title: `5-minute bars to ${t} New York` };
  const wd = now.toLocaleDateString("en-US", { timeZone: NY, weekday: "short" });
  const hm = Number(now.toLocaleTimeString("en-GB", { timeZone: NY, hour: "2-digit", minute: "2-digit", hourCycle: "h23" }).replace(":", ""));
  const sessionOpen = !["Sat", "Sun"].includes(wd) && hm >= 930;
  if (sessionOpen) return { show: false, label: null, title: `today's 5-minute bars are not in yet (last bar ${day} ${t} New York)` };
  return { show: true, label: day, title: `last session: 5-minute bars to ${day} ${t} New York` };
}

type Row = {
  action: "BUY" | "SELL" | "HOLD";
  symbol: string;
  sector?: string | null;
  why: string;
  paper?: string;
  last?: number | null;
  entry?: number | null;
  pnl_pct?: number | null;
  stop?: number | null;
  live?: number | null;
  intraday?: string | null;
  qty?: number | null;
  now?: number | null;
  now_source?: string | null;
  cost_usd?: number | null;
  value_usd?: number | null;
  pnl_usd?: number | null;
  pnl_note?: string | null;
  spark?: number[] | null;
  trend_30d_pct?: number | null;
  sma50?: number | null;
  vs_sma50_pct?: number | null;
  spark_today?: number[] | null;
  today_pct?: number | null;
  today_as_of?: string | null;
};

type Book = {
  names_held: number; names_priced: number; cost_usd: number; value_usd: number;
  pnl_usd: number; pnl_pct: number | null; up: number; down: number;
};

type Today = { as_of?: string; picks: string[]; entering: string[]; leaving: string[]; market_ok?: boolean; scores?: Record<string, number> };

type Board = {
  strategy: string;
  signal_date?: string;
  order_date?: string;
  next_signal_date?: string;
  market?: string;
  allocation_pct?: number;
  per_name_pct?: number;
  error?: string | null;
  rows: Row[];
  today?: Today | null;
  generated_at_utc?: string;
  universe_n?: number | null;
  eligible_n?: number | null;
  capital?: number | null;
  book?: Book | null;
  gauges?: Gauge[] | null;
};

type Gauge = {
  symbol: string; error?: string; last?: number; as_of?: string; chg_1m_pct?: number | null;
  chg_3m_pct?: number | null; from_high_pct?: number; vs_sma50_pct?: number; vs_sma200_pct?: number;
  today_pct?: number; today_as_of?: string;
};

const GAUGE_NAME: Record<string, string> = { SPY: "S&P 500", QQQ: "Nasdaq 100", SMH: "Semiconductors" };

function Gauges({ gauges }: { gauges: Gauge[] }) {
  const chip = (label: string, v: number | null | undefined) => v == null ? null : (
    <span style={{ color: pnlColor(v) }}>{label} {sign(v)}{Math.abs(v).toFixed(1)}%</span>
  );
  return (
    <div style={{ display: "flex", gap: 10, flexWrap: "wrap", marginTop: 10 }}>
      {gauges.map((g) => {
        const below50 = g.vs_sma50_pct != null && g.vs_sma50_pct < 0;
        const below200 = g.vs_sma200_pct != null && g.vs_sma200_pct < 0;
        return (
          <div key={g.symbol} style={{ flex: "1 1 220px", padding: "7px 10px", borderRadius: 6, fontSize: 12,
            border: `1px solid ${below200 ? "#f85149" : below50 ? "#d29922" : "var(--border)"}`,
            fontVariantNumeric: "tabular-nums", lineHeight: 1.6 }}>
            <b>{g.symbol}</b> <span style={{ color: "var(--text-muted)" }}>{GAUGE_NAME[g.symbol] ?? ""}</span>
            {g.error ? <div style={{ color: "#d29922" }}>{g.error}</div> : (
              <>
                <div>
                  {g.last?.toFixed(2)} <span style={{ color: "var(--text-muted)" }}>close {g.as_of}</span>
                  {g.today_pct != null && todayFigure(g.today_as_of).show && <> · {todayFigure(g.today_as_of).label ?? "today"} {chip("", g.today_pct)}</>}
                </div>
                <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
                  {chip("1m", g.chg_1m_pct)}{chip("3m", g.chg_3m_pct)}{chip("from high", g.from_high_pct)}
                </div>
                <div style={{ color: below200 ? "#f85149" : below50 ? "#d29922" : "#3fb950" }}>
                  {g.vs_sma50_pct != null && <>{below50 ? "below" : "above"} 50-day ({sign(g.vs_sma50_pct)}{Math.abs(g.vs_sma50_pct).toFixed(1)}%)</>}
                  {g.vs_sma200_pct != null && <> · {below200 ? "below" : "above"} 200-day ({sign(g.vs_sma200_pct)}{Math.abs(g.vs_sma200_pct).toFixed(1)}%)</>}
                </div>
              </>
            )}
          </div>
        );
      })}
    </div>
  );
}

const usd = (v: number) => `$${Math.abs(v).toLocaleString(undefined, { maximumFractionDigits: 0 })}`;
const sign = (v: number) => (v > 0 ? "+" : v < 0 ? "−" : "");
const pnlColor = (v: number) => (v > 0 ? "#3fb950" : v < 0 ? "#f85149" : "var(--text-dim)");

const TONE: Record<Row["action"], string> = { BUY: "#3fb950", SELL: "#f85149", HOLD: "#8b949e" };

function day(d?: string): string {
  if (!d) return "—";
  return new Date(`${d}T12:00:00Z`).toLocaleDateString(undefined, { day: "numeric", month: "short" });
}

function ago(iso?: string): string {
  if (!iso) return "unknown age";
  const m = Math.round((Date.now() - new Date(iso).getTime()) / 60000);
  return m < 60 ? `${m} min ago` : m < 48 * 60 ? `${Math.round(m / 60)} h ago` : `${Math.round(m / 1440)} days ago`;
}

const TH: React.CSSProperties = { textAlign: "right", padding: "6px 8px", fontSize: 11, fontWeight: 600,
  color: "var(--text-muted)", letterSpacing: "0.04em", textTransform: "uppercase", whiteSpace: "nowrap",
  borderBottom: "1px solid var(--border)" };
const TD: React.CSSProperties = { textAlign: "right", padding: "7px 8px", fontSize: 13, whiteSpace: "nowrap",
  fontVariantNumeric: "tabular-nums", borderBottom: "1px solid var(--border)" };

function pct(v: number | null | undefined, digits = 1): string {
  if (v == null) return "—";
  return `${v > 0 ? "+" : v < 0 ? "\u2212" : ""}${Math.abs(v).toFixed(digits)}%`;
}

function Holdings({ rows }: { rows: Row[] }) {
  const sorted = [...rows].sort((a, b) => (b.pnl_usd ?? -1e12) - (a.pnl_usd ?? -1e12));
  return (
    <div style={{ overflowX: "auto", marginTop: 12 }}>
      <table style={{ width: "100%", borderCollapse: "collapse", minWidth: 760 }}>
        <thead>
          <tr>
            <th style={{ ...TH, textAlign: "left" }}>Stock</th>
            <th style={TH}>Shares</th>
            <th style={TH}>Paid</th>
            <th style={TH}>Now</th>
            <th style={TH}>P&amp;L</th>
            <th style={TH}>P&amp;L %</th>
            <th style={{ ...TH, textAlign: "center" }}>Today (5-min)</th>
            <th style={{ ...TH, textAlign: "center" }}>30-day trend</th>
            <th style={TH}>vs 50-day avg</th>
            <th style={TH}>Stop</th>
            <th style={{ ...TH, textAlign: "left" }}>Signal</th>
          </tr>
        </thead>
        <tbody>
          {sorted.map((r) => {
            const toStop = r.stop != null && r.now != null ? (r.now / r.stop - 1) * 100 : null;
            return (
              <tr key={r.symbol}>
                <td style={{ ...TD, textAlign: "left", fontWeight: 700 }}>{r.symbol}</td>
                <td style={TD}>{r.qty ?? "—"}</td>
                <td style={TD}>{r.entry != null ? r.entry.toFixed(2) : "—"}</td>
                <td style={TD} title={r.now_source ? `price source: ${r.now_source}` : undefined}>
                  {r.now != null ? r.now.toFixed(2) : <span style={{ color: "var(--text-muted)" }}>{r.pnl_note ?? "—"}</span>}
                </td>
                <td style={{ ...TD, fontWeight: 600, color: r.pnl_usd != null ? pnlColor(r.pnl_usd) : undefined }}>
                  {r.pnl_usd != null ? `${sign(r.pnl_usd)}${usd(r.pnl_usd)}` : "—"}
                </td>
                <td style={{ ...TD, color: r.pnl_pct != null ? pnlColor(r.pnl_pct) : undefined }}>{pct(r.pnl_pct, 2)}</td>
                {(() => {
                  const f = todayFigure(r.today_as_of);
                  return (
                    <td style={{ ...TD, textAlign: "center" }} title={f.title}>
                      {f.show ? (
                        <div style={{ display: "inline-flex", alignItems: "center", gap: 6 }}>
                          <Sparkline data={r.spark_today} width={72} height={20} />
                          <span style={{ fontSize: 12, color: r.today_pct != null ? pnlColor(r.today_pct) : "var(--text-muted)" }}>
                            {pct(r.today_pct, 2)}{f.label && <span style={{ color: "var(--text-muted)" }}> {f.label}</span>}
                          </span>
                        </div>
                      ) : <span style={{ fontSize: 12, color: "var(--text-muted)" }}>not in yet</span>}
                    </td>
                  );
                })()}
                <td style={{ ...TD, textAlign: "center" }}>
                  <div style={{ display: "inline-flex", alignItems: "center", gap: 6 }}>
                    <Sparkline data={r.spark} width={72} height={20} />
                    <span style={{ fontSize: 12, color: r.trend_30d_pct != null ? pnlColor(r.trend_30d_pct) : "var(--text-muted)" }}>
                      {pct(r.trend_30d_pct)}
                    </span>
                  </div>
                </td>
                <td style={TD}>
                  {r.vs_sma50_pct != null ? (
                    <span style={{ padding: "1px 7px", borderRadius: 10, fontSize: 11.5, fontWeight: 600,
                      color: r.vs_sma50_pct >= 0 ? "#3fb950" : "#f85149",
                      background: r.vs_sma50_pct >= 0 ? "rgba(63,185,80,0.12)" : "rgba(248,81,73,0.12)" }}>
                      {r.vs_sma50_pct >= 0 ? "above" : "below"} {pct(r.vs_sma50_pct)}
                    </span>
                  ) : "—"}
                </td>
                <td style={TD} title={toStop != null ? `${toStop.toFixed(1)}% above the stop` : undefined}>
                  {r.stop != null ? r.stop.toFixed(2) : "—"}
                  {toStop != null && <span style={{ color: "var(--text-muted)", fontSize: 11.5 }}> ({toStop.toFixed(0)}% away)</span>}
                </td>
                <td style={{ ...TD, textAlign: "left" }}>
                  <span style={{ fontWeight: 700, fontSize: 12, color: TONE[r.action] }}>{r.action}</span>
                  {r.action === "SELL" && <span style={{ fontSize: 11.5, color: "var(--text-dim)" }}> · {r.paper ?? r.why}</span>}
                  {r.intraday && <div style={{ fontSize: 11, color: "#d29922", whiteSpace: "normal" }}>⚠ {r.intraday}</div>}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
      <div style={{ fontSize: 11.5, color: "var(--text-muted)", marginTop: 6 }}>
        HOLD = still in the top 10; a holding is sold on a close below its stop (20% under its highest close since the buy)
        or at the next decision. 30-day trend and 50-day average use settled daily closes (what the strategy trades on);
        Today uses the harvested 5-minute bars against yesterday's close — display only, hover for the last bar's time; it reads “not in yet” until the session's first bars are harvested, and is never the previous session shown as today.
        Now is the live quote while the market is open.
      </div>
    </div>
  );
}

function Column({ action, rows }: { action: Row["action"]; rows: Row[] }) {
  return (
    <div style={{ flex: "1 1 260px", minWidth: 240 }}>
      <div style={{ fontSize: 12, fontWeight: 700, color: TONE[action], letterSpacing: "0.06em", marginBottom: 6 }}>
        {action} <span style={{ color: "var(--text-muted)", fontWeight: 400 }}>· {rows.length}</span>
      </div>
      {rows.length === 0 && <div style={{ fontSize: 12, color: "var(--text-muted)" }}>nothing</div>}
      {rows.map((r) => (
        <div key={`${action}-${r.symbol}`} style={{ padding: "6px 0", borderTop: "1px solid var(--border)" }}>
          <div style={{ display: "flex", justifyContent: "space-between", gap: 8, alignItems: "baseline" }}>
            <span style={{ fontWeight: 700, fontSize: 14 }}>{r.symbol}</span>
            {r.paper && (
              <span style={{ fontSize: 11, color: r.paper.startsWith("filled") ? "#3fb950" : "var(--text-dim)" }}>
                paper: {r.paper}
              </span>
            )}
          </div>
          <div style={{ fontSize: 11.5, color: "var(--text-dim)", lineHeight: 1.4 }}>{r.why}</div>
          {(r.entry != null || r.stop != null) && (
            <div style={{ fontSize: 11.5, color: "var(--text-dim)", marginTop: 2, fontVariantNumeric: "tabular-nums" }}>
              {r.qty ? <>{r.qty} sh · </> : null}
              {r.entry != null && <>bought {r.entry.toFixed(2)}</>}
              {r.now != null && <> · now {r.now.toFixed(2)} <span style={{ color: "var(--text-muted)" }}>({r.now_source})</span></>}
              {r.pnl_note && <> · {r.pnl_note}</>}
              {r.stop != null && <> · stop {r.stop.toFixed(2)}</>}
            </div>
          )}
          {r.pnl_usd != null && r.pnl_pct != null && (
            <div style={{ fontSize: 12.5, fontWeight: 600, color: pnlColor(r.pnl_usd), fontVariantNumeric: "tabular-nums" }}>
              {sign(r.pnl_usd)}{usd(r.pnl_usd)} ({sign(r.pnl_pct)}{Math.abs(r.pnl_pct).toFixed(2)}%)
              {r.value_usd != null && <span style={{ color: "var(--text-muted)", fontWeight: 400 }}> · worth {usd(r.value_usd)}</span>}
            </div>
          )}
          {r.intraday && (
            <div style={{ fontSize: 11.5, color: "#d29922", marginTop: 2 }}>⚠ {r.intraday}</div>
          )}
        </div>
      ))}
    </div>
  );
}

export function SignalBoard() {
  const [board, setBoard] = useState<Board | null>(null);
  const [failed, setFailed] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    const load = () =>
      api.todaySetupsArtifact("momentum_rotation")
        .then((r) => { if (alive) { setBoard(r.artifact as Board); setFailed(null); } })
        .catch((e) => { if (alive) setFailed(String((e as Error)?.message || e)); });
    void load();
    const t = setInterval(load, 60_000);
    return () => { alive = false; clearInterval(t); };
  }, []);

  const frame = { padding: "12px 14px", border: "1px solid var(--border)", borderRadius: 8, background: "var(--panel, rgba(255,255,255,0.02))", marginBottom: 12 };

  if (failed || !board) {
    return (
      <div style={frame}>
        <div style={{ fontWeight: 700 }}>Today's signal</div>
        <div style={{ fontSize: 12.5, color: "var(--text-dim)", marginTop: 4 }}>
          {failed ? "No signal has been published yet — the momentum engine publishes it on its next run." : "Loading…"}
        </div>
      </div>
    );
  }

  const by = (a: Row["action"]) => board.rows.filter((r) => r.action === a);
  const stale = board.generated_at_utc && Date.now() - new Date(board.generated_at_utc).getTime() > 6 * 3600_000;

  return (
    <div style={frame}>
      <div style={{ display: "flex", justifyContent: "space-between", gap: 12, flexWrap: "wrap", alignItems: "baseline" }}>
        <div>
          <span style={{ fontWeight: 700, fontSize: 15 }}>Today's signal</span>
          <span style={{ color: "var(--text-dim)", fontSize: 12.5 }}> · {board.strategy}</span>
        </div>
        <span style={{ fontSize: 11.5, color: stale ? "#d29922" : "var(--text-muted)" }}>
          updated {ago(board.generated_at_utc)}{stale ? " — older than expected" : ""}
        </span>
      </div>
      {board.error ? (
        <div style={{ fontSize: 12.5, color: "#d29922", marginTop: 6 }}>
          No new decision: {board.error}. Holdings below keep their 20% trailing stops.
        </div>
      ) : (
        <div style={{ fontSize: 12.5, color: "var(--text-dim)", marginTop: 4, lineHeight: 1.5 }}>
          Decided on the {day(board.signal_date)} close; paper orders at the {day(board.order_date)} open.
          Next decision {day(board.next_signal_date)}. {board.market}. {board.allocation_pct}% of capital,
          {" "}{board.per_name_pct}% a name.
          {(board.universe_n || board.capital) ? (
            <div style={{ color: "var(--text-muted)", fontSize: 11.5 }}>
              {board.universe_n ? <>Ranked {board.eligible_n != null ? `${board.eligible_n} eligible of ` : ""}{board.universe_n} names (sector-labelled universe, SPY/QQQ excluded). </> : null}
              {board.capital ? <>Paper orders sized on ${Math.round(board.capital).toLocaleString()} paper capital
                {board.per_name_pct ? ` — about $${Math.round(board.capital * board.per_name_pct / 100).toLocaleString()} a name` : ""}.</> : null}
            </div>
          ) : null}
        </div>
      )}
      {/* TODAY'S RANKING, ITS OWN BLOCK. Owner, 18 Nov 2026: "i didnt see
          anywhere the new ranking" — it was one grey sentence under the
          decision. It is rebuilt on every lane run from the latest close. */}
      {board.today && board.today.picks.length > 0 && (() => {
        const T = board.today!;
        const stays = T.picks.filter((x) => !T.entering.includes(x));
        const sc = (x: string) => (T.scores && T.scores[x] != null ? ` ${T.scores[x].toFixed(2)}` : "");
        const group = (label: string, names: string[], color: string, score: boolean) => (
          <div style={{ minWidth: 150 }}>
            <div style={{ fontSize: 11, textTransform: "uppercase", letterSpacing: "0.05em", color: "var(--text-muted)" }}>
              {label} ({names.length})
            </div>
            <div style={{ color, fontWeight: 600 }}>
              {names.length ? names.map((x) => `${x}${score ? sc(x) : ""}`).join(", ") : "none"}
            </div>
          </div>
        );
        return (
          <div style={{ marginTop: 10, padding: "10px 12px", borderRadius: 6, border: "1px solid var(--border)", fontSize: 12.5, lineHeight: 1.5 }}>
            <div style={{ fontWeight: 700, fontSize: 13.5 }}>
              Today's ranking — if it rebalanced on the {day(T.as_of)} close
            </div>
            <div style={{ display: "flex", gap: 20, flexWrap: "wrap", marginTop: 6, fontVariantNumeric: "tabular-nums" }}>
              {group("Would come in", T.entering, "#3fb950", true)}
              {group("Would drop out", T.leaving, "#f85149", false)}
              {group("Stays", stays, "inherit", false)}
            </div>
            <div style={{ color: "var(--text-dim)", fontSize: 11.5, marginTop: 6 }}>
              Not an order. This book changes holdings only on its decision date ({day(board.next_signal_date)});
              the ranking is rebuilt from the latest close on every run so the drift is visible.
              {T.market_ok === false ? " SPY is below its 200-day average: the rule would hold cash." : ""}
            </div>
          </div>
        );
      })()}
      {board.gauges && board.gauges.length > 0 && <Gauges gauges={board.gauges} />}
      {board.book && (
        <div style={{ marginTop: 10, padding: "8px 10px", borderRadius: 6, background: "rgba(255,255,255,0.025)",
          fontSize: 13, fontVariantNumeric: "tabular-nums" }}>
          <b>Paper book:</b> {board.book.names_priced} names, {usd(board.book.cost_usd)} paid, worth {usd(board.book.value_usd)} now —{" "}
          <b style={{ color: pnlColor(board.book.pnl_usd) }}>
            {sign(board.book.pnl_usd)}{usd(board.book.pnl_usd)}
            {board.book.pnl_pct != null && <> ({sign(board.book.pnl_pct)}{Math.abs(board.book.pnl_pct).toFixed(2)}%)</>}
          </b>
          <span style={{ color: "var(--text-dim)" }}> · {board.book.up} up, {board.book.down} down</span>
          {board.book.names_priced < board.book.names_held && (
            <span style={{ color: "#d29922" }}> · {board.book.names_held - board.book.names_priced} held name(s) have no price since the buy and are not in this total</span>
          )}
          <div style={{ color: "var(--text-muted)", fontSize: 11.5 }}>
            Paid = our fill prices. Now = the live quote while the market is open, else the latest close after the buy.
          </div>
        </div>
      )}
      {by("BUY").length > 0 && (
        <div style={{ display: "flex", gap: 20, flexWrap: "wrap", marginTop: 12 }}>
          <Column action="BUY" rows={by("BUY")} />
        </div>
      )}
      {(by("HOLD").length + by("SELL").length) > 0 ? (
        <Holdings rows={[...by("SELL"), ...by("HOLD")]} />
      ) : (
        <div style={{ fontSize: 12.5, color: "var(--text-muted)", marginTop: 12 }}>No holdings.</div>
      )}
      {by("BUY").length === 0 && by("SELL").length === 0 && (
        <div style={{ fontSize: 12, color: "var(--text-dim)", marginTop: 8 }}>
          No trades due. Next decision {day(board.next_signal_date)}.
        </div>
      )}
    </div>
  );
}

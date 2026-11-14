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
};

type Today = { as_of?: string; picks: string[]; entering: string[]; leaving: string[]; market_ok?: boolean };

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
};

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
          {(r.last != null || r.stop != null) && (
            <div style={{ fontSize: 11.5, color: "var(--text-dim)", marginTop: 2, fontVariantNumeric: "tabular-nums" }}>
              {r.entry != null && <>bought {r.entry.toFixed(2)} · </>}
              {r.last != null && <>now {r.last.toFixed(2)}</>}
              {r.pnl_pct != null && (
                <span style={{ color: r.pnl_pct >= 0 ? "#3fb950" : "#f85149" }}> ({r.pnl_pct >= 0 ? "+" : ""}{r.pnl_pct}%)</span>
              )}
              {r.stop != null && <> · stop {r.stop.toFixed(2)}</>}
              {r.live != null && <> · live {r.live.toFixed(2)}</>}
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
        </div>
      )}
      {board.today && board.today.picks.length > 0 && (
        <div style={{ marginTop: 10, padding: "8px 10px", borderRadius: 6, background: "rgba(255,255,255,0.025)",
          fontSize: 12.5, lineHeight: 1.6 }}>
          <b>If it rebalanced on the {day(board.today.as_of)} close:</b>{" "}
          {board.today.picks.map((sym, i) => (
            <span key={sym} style={{ fontWeight: board.today!.entering.includes(sym) ? 700 : 400,
              color: board.today!.entering.includes(sym) ? "#3fb950" : "inherit" }}>
              {sym}{i < board.today!.picks.length - 1 ? ", " : ""}
            </span>
          ))}
          <div style={{ color: "var(--text-dim)", fontSize: 11.5 }}>
            {board.today.entering.length || board.today.leaving.length
              ? <>Drifting since the decision — would come in: {board.today.entering.join(", ") || "none"};
                  would drop out: {board.today.leaving.join(", ") || "none"}. </>
              : <>Same ten as the decision. </>}
            Holdings change only on the next decision date ({day(board.next_signal_date)}); this updates daily.
          </div>
        </div>
      )}
      <div style={{ display: "flex", gap: 20, flexWrap: "wrap", marginTop: 12 }}>
        <Column action="BUY" rows={by("BUY")} />
        <Column action="SELL" rows={by("SELL")} />
        <Column action="HOLD" rows={by("HOLD")} />
      </div>
    </div>
  );
}

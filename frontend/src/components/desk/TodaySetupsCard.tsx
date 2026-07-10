/**
 * TodaySetupsCard — the dashboard scanner: "what's worth a look today?"
 *
 * The curated, risk-aware replacement for the original flat BUY-light board.
 * Scans BOTH universes — large_50 (core, stable) and high_beta (volatile) —
 * tags each setup with its universe + ATR so the risk is visible, and surfaces
 * the few worth considering by ENTRY QUALITY:
 *   ⭐ CONSIDER  — LONG + pulled back near the kijun (good risk-entry)
 *   ⚠  EXTENDED  — LONG but stretched (98th-pctile) → wait for a pullback
 * It never emits a confident BUY — "LONG + here's the risk; you decide".
 * High-beta setups carry far higher ATR (size down, wider stop) — flagged amber.
 *
 * FRESHNESS GATE (the ANET trap): the scan is computed off DAILY CLOSING bars and
 * displayed until the next EOD refresh, so every morning it shows YESTERDAY's
 * world. On a gap day a name can rip through its "pulled-back-to-kijun" entry and
 * the stale card still stars it — actively misleading if you trade off the UI. So
 * we fetch the LIVE price per card and compare to the cached close in ATR units:
 * a card that's moved > FLAG_ATR is flagged "moved since close", and a ⭐ that ran
 * up > EXPIRED_ATR is de-starred to "setup expired" (the pullback is gone). Powered
 * by live quotes so the scan can't hand you an entry the tape already erased.
 */
import { useCallback, useEffect, useState } from "react";
import { api } from "../../api/client";

const UNIVERSES: Array<{ key: string; tag: string; label: string }> = [
  { key: "large_50", tag: "core", label: "L50" },
  { key: "high_beta", tag: "high-β", label: "HB" },
];
const HIGH_ATR = 6;      // %/day above which we flag a setup as a wild ride
const FLAG_ATR = 0.4;    // live moved ≥ this many ATRs from the cached close → flag "moved"
const EXPIRED_ATR = 0.6; // a ⭐ that ran UP ≥ this many ATRs → the pullback entry is gone (chasing)

type Setup = Awaited<ReturnType<typeof api.todaySetups>>["artifact"]["setups"][number] & { universe: string };
type State = "loading" | "ok" | "nodata" | "error";

const TAG: Record<string, { dot: string; color: string }> = {
  consider: { dot: "⭐", color: "#3fb950" },
  extended: { dot: "⚠", color: "#d29922" },
  below_trend: { dot: "📉", color: "#d29922" },  // above cloud but below 200d SMA — primary trend unconfirmed
  hold: { dot: "·", color: "var(--text-muted)" },
};

/** Today's date (UTC) as YYYY-MM-DD, to detect a stale (prior-close) scan. */
function todayUtc(): string {
  return new Date().toISOString().slice(0, 10);
}

/** Is the US cash session open right now? Weekdays 13:30–20:00 UTC (09:30–16:00 ET).
 * Approximate — ignores holidays/DST edge — used only to FRAME the scan, never to
 * gate trading. Pre-open, a daily-close scan is the correct next-session basis (not
 * stale); intraday, an un-refreshed scan genuinely IS stale. */
function usMarketOpen(now = new Date()): boolean {
  const dow = now.getUTCDay();                 // 0 Sun … 6 Sat
  if (dow === 0 || dow === 6) return false;
  const mins = now.getUTCHours() * 60 + now.getUTCMinutes();
  return mins >= 13 * 60 + 30 && mins < 20 * 60;
}

/** Signed move of the live price from the cached close, in ATR units.
 * + = up since close. null when we can't compute (no live px / no ATR). */
function moveAtr(s: Setup, livePx: number | null | undefined): number | null {
  if (livePx == null || !s.close || !s.atr_pct) return null;
  const atrAbs = s.close * (s.atr_pct / 100);
  if (atrAbs <= 0) return null;
  return (livePx - s.close) / atrAbs;
}

export function TodaySetupsCard() {
  const [state, setState] = useState<State>("loading");
  const [setups, setSetups] = useState<Setup[]>([]);
  const [counts, setCounts] = useState<{ consider: number; extended: number; excluded: number; weak: number; suspect: number }>({ consider: 0, extended: 0, excluded: 0, weak: 0, suspect: 0 });
  const [asOf, setAsOf] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [showAll, setShowAll] = useState(false);
  // Live price per symbol → freshness gate (see file header).
  const [live, setLive] = useState<Record<string, number | null>>({});

  const load = useCallback(async () => {
    const results = await Promise.allSettled(UNIVERSES.map((u) => api.todaySetups(u.key)));
    const merged: Setup[] = [];
    const c = { consider: 0, extended: 0, excluded: 0, weak: 0, suspect: 0 };
    let anyOk = false, latest: string | null = null;
    results.forEach((r, i) => {
      if (r.status !== "fulfilled") return;
      anyOk = true;
      const a = r.value.artifact;
      if (!latest || (a.as_of_utc ?? "") > latest) latest = a.as_of_utc;
      c.consider += a.counts.consider; c.extended += a.counts.extended; c.excluded += a.counts.excluded;
      c.weak += a.counts.weak ?? 0; c.suspect += a.counts.suspect ?? 0;
      a.setups.forEach((s) => merged.push({ ...s, universe: UNIVERSES[i].key }));
    });
    if (!anyOk) {
      const first = results[0];
      const msg = first.status === "rejected" ? String(first.reason) : "";
      if (msg.includes("404")) setState("nodata"); else { setErr(msg); setState("error"); }
      return;
    }
    // Rank: consider → extended → hold; core (large_50) before high_beta within a tier;
    // then closest-to-kijun first.
    const order: Record<string, number> = { consider: 0, extended: 1, hold: 2 };
    merged.sort((a, b) =>
      (order[a.classification] - order[b.classification]) ||
      ((a.universe === "large_50" ? 0 : 1) - (b.universe === "large_50" ? 0 : 1)) ||
      ((a.dist_atr ?? 99) - (b.dist_atr ?? 99)));
    setSetups(merged); setCounts(c); setAsOf(latest); setState("ok"); setErr(null);
  }, []);

  useEffect(() => {
    void load();
    const t = setInterval(load, 300_000);
    return () => clearInterval(t);
  }, [load]);

  // Freshness gate: fetch the LIVE price per actionable setup and compare to the
  // cached close. Uses IBKR on-demand (api.ibkrLivePrice) — the Yahoo candles
  // endpoint serves stale prior-close data in this environment. Capped to the
  // consider/extended setups to keep IBKR load light; failed fetches leave a card
  // unflagged (never a false "moved").
  useEffect(() => {
    if (state !== "ok" || setups.length === 0) return;
    let alive = true;
    const syms = [...new Set(
      setups.filter((s) => s.classification === "consider" || s.classification === "extended").map((s) => s.symbol),
    )].slice(0, 15);
    const fetchLive = () => {
      Promise.allSettled(syms.map((sym) => api.ibkrLivePrice(sym).then((px) => ({ sym, px })))).then((res) => {
        if (!alive) return;
        const m: Record<string, number | null> = {};
        res.forEach((r) => { if (r.status === "fulfilled" && r.value) m[r.value.sym] = r.value.px; });
        setLive((prev) => ({ ...prev, ...m }));
      });
    };
    fetchLive();
    const t = setInterval(fetchLive, 180_000); // re-check every 3 min (IBKR-friendly)
    return () => { alive = false; clearInterval(t); };
  }, [state, setups]);

  // The scan is a prior-session snapshot. Two very different meanings:
  //  · market CLOSED  → this IS today's actionable basis (act at the open) — NOT stale.
  //  · market OPEN    → the tape has moved since this scan → genuinely stale intraday.
  const priorClose = state === "ok" && !!asOf && asOf.slice(0, 10) < todayUtc();
  const mktOpen = usMarketOpen();
  const staleScan = priorClose && mktOpen;        // the alarming case: stale during a live session
  const nextSession = priorClose && !mktOpen;     // the benign case: pre-open next-session basis
  const shown = (showAll ? setups : setups.filter((s) => s.classification === "consider")).slice(0, showAll ? 30 : 12);

  return (
    <div style={{ border: "1px solid var(--border)", borderRadius: 8, background: "rgba(255,255,255,0.02)", overflow: "hidden" }}>
      <div style={{ display: "flex", alignItems: "center", gap: 8, padding: "5px 10px", fontSize: 11, borderBottom: state === "ok" ? "1px solid #1b2233" : "none" }}>
        <span style={{ fontWeight: 600 }}>Today's Setups — large_50 + high-β</span>
        {state === "ok" && (
          <span style={{ fontSize: 9.5, color: "var(--text-muted)" }} title="weak = above cloud but below kijun (support breaking); suspect = bad data (corrupt bar). Both filtered out, never starred.">
            ⭐{counts.consider} · ⚠{counts.extended} · {counts.excluded} excluded
            {counts.weak + counts.suspect > 0 ? ` · ${counts.weak + counts.suspect} filtered` : ""}
          </span>
        )}
        {state === "ok" && asOf && (
          <span style={{ marginLeft: "auto", fontSize: 9.5, color: staleScan ? "#d29922" : "var(--text-muted)", fontWeight: staleScan ? 700 : 400 }}>
            {nextSession
              ? `from ${asOf.slice(0, 10)} close · for today's open`
              : `as of ${asOf.slice(0, 10)}${staleScan ? " (prior close)" : ""}`}
          </span>
        )}
      </div>

      {/* Genuinely stale: market is OPEN and the tape has moved past this daily scan. */}
      {staleScan && (
        <div style={{ padding: "5px 10px", fontSize: 10, background: "rgba(210,153,34,0.12)", color: "#d29922", borderBottom: "1px solid #1b2233", lineHeight: 1.4 }}>
          ⚠ Market is open and these are <b>{asOf!.slice(0, 10)}'s close</b> — the tape has moved. Cards live-checked below;
          any that moved &gt;{FLAG_ATR} ATR since close are flagged, and ⭐ setups that ran are marked EXPIRED. Re-verify before acting — especially high-β.
        </div>
      )}

      {/* Benign: pre-open. A daily scan from the last close IS today's actionable basis. */}
      {nextSession && (
        <div style={{ padding: "5px 10px", fontSize: 10, background: "rgba(88,166,255,0.10)", color: "#79c0ff", borderBottom: "1px solid #1b2233", lineHeight: 1.4 }}>
          ⓘ Market closed. These are the setups from <b>{asOf!.slice(0, 10)}'s close</b> — the basis to act on at today's open, not stale data.
          Prices refresh live once the session opens.
        </div>
      )}

      {state === "loading" && <div style={{ padding: "8px 10px", fontSize: 11, color: "var(--text-muted)" }}>Scanning…</div>}
      {state === "error" && <div style={{ padding: "8px 10px", fontSize: 11, color: "#f85149" }}>Scan unavailable: {err}</div>}
      {state === "nodata" && (
        <div style={{ padding: "8px 10px", fontSize: 11, color: "var(--text-muted)", fontStyle: "italic" }}>
          No scan pushed yet — run <code style={{ color: "var(--text-dim)" }}>tradepro-today-setups --push</code> on the worker.
        </div>
      )}

      {state === "ok" && (
        <div style={{ padding: "6px 6px 8px" }}>
          {shown.length === 0 && (
            <div style={{ padding: "6px", fontSize: 11, color: "var(--text-muted)", fontStyle: "italic" }}>
              No ⭐ "consider" setups today. {counts.extended > 0 && `${counts.extended} extended — `}
              <span onClick={() => setShowAll(true)} style={{ cursor: "pointer", color: "var(--accent, #4f8cff)" }}>show all</span>
            </div>
          )}
          {shown.map((s) => {
            const mv = moveAtr(s, live[s.symbol]);
            // Expired = a ⭐ consider that ran UP past its entry by ≥ EXPIRED_ATR.
            const expired = s.classification === "consider" && mv != null && mv >= EXPIRED_ATR;
            const moved = mv != null && Math.abs(mv) >= FLAG_ATR;
            const t = expired ? { dot: "", color: "var(--text-muted)" } : (TAG[s.classification] ?? TAG.hold);
            const dot = expired ? "✕" : t.dot;
            const hb = s.universe !== "large_50";
            const hotAtr = (s.atr_pct ?? 0) >= HIGH_ATR;
            const livePx = live[s.symbol];
            return (
              <div key={`${s.universe}:${s.symbol}`} style={{ display: "grid", gridTemplateColumns: "auto 58px 50px 1fr", gap: 7, alignItems: "baseline", padding: "4px 6px", borderTop: "1px solid #11161f", opacity: expired ? 0.55 : 1 }}>
                <span style={{ color: expired ? "var(--text-muted)" : t.color, fontWeight: 700, fontSize: 12, whiteSpace: "nowrap", textDecoration: expired ? "line-through" : "none" }}>
                  {dot} {s.symbol}
                  <span style={{ fontSize: 8, marginLeft: 4, padding: "0 3px", borderRadius: 3, color: hb ? "#d29922" : "var(--text-muted)", border: `1px solid ${hb ? "#5a4a1a" : "#1b2233"}` }}>{hb ? "HB" : "L50"}</span>
                </span>
                <span style={{ fontFamily: "monospace", fontSize: 11, lineHeight: 1.1 }}>
                  {livePx != null ? (
                    <>
                      {/* LIVE (today) is the headline; prior close shown small as the scan basis. */}
                      <span style={{ color: livePx >= s.close ? "#3fb950" : "#f85149", fontWeight: 700 }} title="live price (today)">
                        ${livePx.toFixed(2)}
                      </span>
                      <span style={{ display: "block", fontSize: 8, color: "var(--text-muted)" }} title="prior close — the scan basis">
                        cl {s.close.toFixed(2)}
                      </span>
                    </>
                  ) : (
                    <span style={{ color: "var(--text-dim)" }} title="prior close (no live quote)">${s.close.toFixed(2)}</span>
                  )}
                </span>
                <span style={{ fontSize: 10, color: hotAtr ? "#d29922" : "var(--text-muted)", fontWeight: hotAtr ? 700 : 400 }} title="ATR %/day — high = size down, wider stop">
                  {s.atr_pct == null ? "—" : `${s.atr_pct.toFixed(1)}%`}
                </span>
                <span style={{ fontSize: 10, color: "var(--text-dim)", lineHeight: 1.4 }}>
                  {(expired || moved) && (
                    <span style={{ color: expired ? "#f85149" : "#d29922", fontWeight: 700, marginRight: 4 }}>
                      {expired
                        ? `EXPIRED — ran +${mv!.toFixed(1)} ATR past entry`
                        : `moved ${mv! >= 0 ? "+" : ""}${mv!.toFixed(1)} ATR since close`}
                      {" · "}
                    </span>
                  )}
                  {s.why}
                </span>
              </div>
            );
          })}
          <div style={{ marginTop: 6, fontSize: 9, color: "var(--text-muted)", padding: "0 6px", display: "flex", justifyContent: "space-between" }}>
            <span>
              {showAll ? "consider + extended" : "⭐ consider only"} ·
              <span onClick={() => setShowAll((v) => !v)} style={{ cursor: "pointer", color: "var(--accent, #4f8cff)" }}> {showAll ? "consider only" : "show extended"}</span>
              {" · "}<span style={{ color: "#d29922" }}>HB / amber ATR = higher risk, size down</span>
            </span>
            <span title="signal — not advice; systematic signal, discretionary entry">you decide entry ⓘ</span>
          </div>
        </div>
      )}
    </div>
  );
}

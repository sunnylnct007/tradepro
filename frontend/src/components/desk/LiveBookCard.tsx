/**
 * LiveBookCard — your REAL account: what you are carrying, and what to close.
 *
 * Owner, 7 Nov 2026: "for live account we wree in deep trouble and tradepro
 * didnt asissted at all". That was true, and not because TradePro could not
 * work it out. On 1 Nov the same rules this card renders flagged MRVL Nov20
 * 230P and CRWV Nov20 70P as CLOSE on assignment grounds, nineteen days out.
 * The analysis existed in a CONVERSATION and never on a screen, so when the
 * week turned there was nothing to open.
 *
 * The week it missed: SNDK -£47,000, MU -£20,260, STX -£13,413 — memory and
 * storage, one theme.
 *
 * READ-ONLY. The source is the IBKR Flex Web Service, a report pulled with its
 * own token that cannot place, modify or cancel anything.
 *
 * AND IT DOES NOT ADVISE. "CLOSE" means this position crossed a line the owner
 * set, and the line is printed next to it. Every verdict shows the number that
 * fired it, so the argument can be with a THRESHOLD rather than with a verdict
 * that appeared from nowhere.
 */
import { useEffect, useState } from "react";

import { api } from "../../api/client";

const TONE = {
  act: "#f85149", watch: "#d29922", ok: "#3fb950",
  dim: "var(--text-dim)", muted: "var(--text-muted)",
};

const ACTION: Record<string, { tone: string; label: string }> = {
  CLOSE:   { tone: TONE.act,   label: "CLOSE" },
  WATCH:   { tone: TONE.watch, label: "watch" },
  UNKNOWN: { tone: TONE.watch, label: "?" },
  HOLD:    { tone: TONE.dim,   label: "hold" },
};

function usd(n: number | null | undefined, d = 0): string {
  if (n == null || Number.isNaN(n)) return "—";
  return `${n < 0 ? "−" : ""}$${Math.abs(n).toLocaleString(undefined, {
    minimumFractionDigits: d, maximumFractionDigits: d })}`;
}

function hoursSince(iso?: string | null): number | null {
  if (!iso) return null;
  const t = Date.parse(iso);
  return Number.isFinite(t) ? (Date.now() - t) / 3.6e6 : null;
}

export function LiveBookCard() {
  const [art, setArt] = useState<any>(null);
  const [asOf, setAsOf] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [showAll, setShowAll] = useState(false);

  useEffect(() => {
    let live = true;
    const load = () =>
      api.todaySetupsArtifact("live-book-risk")
        .then((d) => { if (live) { setArt(d.artifact); setAsOf(d.asOfUtc); setErr(null); } })
        .catch((e) => { if (live) setErr(e instanceof Error ? e.message : "unavailable"); });
    load();
    const t = setInterval(load, 300_000);
    return () => { live = false; clearInterval(t); };
  }, []);

  const rows: any[] = art?.positions ?? [];
  const counts = art?.counts ?? {};
  const age = hoursSince(asOf ?? art?.as_of_utc);

  // ACT ROWS ALWAYS SHOW. The tail is collapsed because 16 of 22 were HOLD
  // the first time this ran, and a card that opens with sixteen fine
  // positions buries the two that expire inside a fortnight.
  const acting = rows.filter((r) => r.action === "CLOSE" || r.action === "WATCH"
                                  || r.action === "UNKNOWN");
  const resting = rows.filter((r) => r.action === "HOLD");
  const shown = showAll ? [...acting, ...resting] : acting;

  return (
    <div style={{
      border: `1px solid ${counts.close ? TONE.act : "var(--border)"}`,
      borderRadius: 10, padding: "12px 14px", background: "var(--surface)",
      marginBottom: 14,
    }}>
      <div style={{ display: "flex", alignItems: "baseline", gap: 10, flexWrap: "wrap" }}>
        <h2 style={{ margin: 0, fontSize: 16 }}>Live book — what you are carrying</h2>
        <span style={{ fontSize: 11, color: TONE.muted }}>
          real account · read-only
        </span>
        {/* STALENESS IS A FACT ABOUT THE ADVICE. A close verdict computed
            yesterday on a position expiring this week is not the same claim
            as one computed an hour ago, and the reader must not have to
            guess which they are looking at. */}
        {age != null && (
          <span style={{ fontSize: 11, color: age > 24 ? TONE.watch : TONE.muted }}>
            {age < 1 ? "just now" : `${Math.round(age)}h old`}
            {age > 24 ? " — STALE" : ""}
          </span>
        )}
        <span style={{ marginLeft: "auto", fontSize: 12 }}>
          {counts.close ? (
            <b style={{ color: TONE.act }}>{counts.close} to close</b>
          ) : (
            <span style={{ color: TONE.ok }}>nothing needs action</span>
          )}
          <span style={{ color: TONE.muted }}>
            {" "}· {counts.watch ?? 0} watch · {counts.hold ?? 0} hold
            {counts.unknown ? ` · ${counts.unknown} unassessed` : ""}
          </span>
        </span>
      </div>

      {err && (
        <div style={{ fontSize: 12, color: TONE.act, marginTop: 8 }}>
          Live book unavailable — {err}. This is NOT "nothing to do": the book
          could not be read.
        </div>
      )}
      {!err && !art && (
        <div style={{ fontSize: 12, color: TONE.dim, marginTop: 8 }}>loading…</div>
      )}

      {art && (
        <>
          <div style={{ display: "flex", gap: 18, flexWrap: "wrap", margin: "8px 0 4px",
                        fontSize: 11.5, color: TONE.dim }}>
            <span>
              assignment obligation{" "}
              <b style={{ color: "var(--text)", fontFamily: "var(--font-mono)" }}>
                {usd(art.assignment_obligation_usd)}
              </b>{" "}
              <span style={{ color: TONE.muted }}>if every short put is exercised</span>
            </span>
            <span>
              marked at{" "}
              <b style={{
                color: (art.unrealised_usd ?? 0) < 0 ? TONE.act : TONE.ok,
                fontFamily: "var(--font-mono)",
              }}>{usd(art.unrealised_usd)}</b>
            </span>
            {/* THE SECOND NUMBER IS THE HONEST ONE, and showing only the
                first is what made this book read three times worse than it
                is. The mark is the cost of exiting today; most of it is time
                value the seller has earned and not yet been credited. */}
            {art.unrealised_hold_usd != null && (
              <span>
                at expiry if nothing moves{" "}
                <b style={{
                  color: (art.unrealised_hold_usd ?? 0) < 0 ? TONE.act : TONE.ok,
                  fontFamily: "var(--font-mono)",
                }}>{usd(art.unrealised_hold_usd)}</b>{" "}
                <span style={{ color: TONE.muted }}>
                  — {usd(art.time_value_at_stake_usd)} of that gap is time
                  value you forfeit by closing early
                </span>
              </span>
            )}
          </div>

          {rows.length === 0 && (
            <div style={{ fontSize: 12, color: TONE.dim, padding: "6px 0" }}>
              No short puts in the live book.
            </div>
          )}

          {shown.map((r) => {
            const a = ACTION[r.action] ?? ACTION.HOLD;
            return (
              <div key={`${r.contract}`} style={{
                display: "flex", alignItems: "baseline", gap: 10, flexWrap: "wrap",
                padding: "5px 0", borderTop: "1px solid #141a26", fontSize: 12.5,
              }}>
                <span style={{ color: a.tone, fontWeight: 700, minWidth: 54 }}>
                  {a.label}
                </span>
                <span style={{ fontWeight: 600, minWidth: 56,
                               fontFamily: "var(--font-mono)" }}>{r.symbol}</span>
                <span style={{ color: TONE.dim, fontFamily: "var(--font-mono)" }}>
                  {r.strike}P {r.expiry?.slice(5)} · {r.dte}d
                </span>
                <span style={{
                  color: (r.unrealised ?? 0) < 0 ? TONE.act : TONE.ok,
                  fontFamily: "var(--font-mono)",
                }}>{usd(r.unrealised)}</span>
                {/* Basis before verdict: "would I own it here" is the question
                    a short put actually asks, and the owner rejected a screen
                    that answered a different one. */}
                {r.basis != null && (
                  <span style={{ color: (r.cushion_pct ?? 0) >= 0 ? TONE.ok : TONE.muted,
                                 fontFamily: "var(--font-mono)", fontSize: 11.5 }}>
                    basis {r.basis.toFixed(2)}
                    {r.cushion_pct != null
                      && ` (${r.cushion_pct >= 0 ? "+" : ""}${r.cushion_pct.toFixed(1)}%)`}
                  </span>
                )}
                <span style={{ color: TONE.muted, flex: 1, minWidth: 220 }}>{r.why}</span>
              </div>
            );
          })}

          {/* NOTHING IS SILENTLY WITHHELD — the tail is counted, not hidden. */}
          {resting.length > 0 && (
            <div onClick={() => setShowAll((v) => !v)}
                 style={{ fontSize: 11.5, color: TONE.dim, cursor: "pointer",
                          padding: "6px 0 0", userSelect: "none" }}>
              {showAll
                ? "▲ show only what needs attention"
                : `▼ ${resting.length} out-of-the-money position(s) running — show all`}
            </div>
          )}

          {/* The thresholds, so a verdict can be argued with. */}
          {art.rules && (
            <div style={{ fontSize: 10.5, color: TONE.muted, marginTop: 8, lineHeight: 1.5 }}>
              Rules: close at {art.rules.take_profit_pct}% of credit captured,
              in-the-money inside {art.rules.near_expiry_days} days, or the stock
              more than {art.rules.max_basis_shortfall_pct}% below the effective
              basis (strike minus credit — what assignment really costs you).
              These are thresholds you set — "CLOSE" means a line was crossed,
              not that anyone thinks you should sell. A position in the money
              but still ABOVE its basis reads as WATCH, because assignment
              there is profitable.
            </div>
          )}
        </>
      )}
    </div>
  );
}

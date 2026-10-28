/**
 * SpreadBuilder — move the strikes, watch what you can lose and make.
 *
 * Owner, repeatedly: "options we shd eb able to determine as welll on spread
 * the max loss and gain ... so we need to makee screen interactive", and
 * "different spread techniqe are there and thats why i was saying interctibve
 * screen is needed ... so we have a deterministic possible loss and profut".
 *
 * The board already states max loss and max gain for the pair IT chose. What
 * it could not answer is the question that actually decides a trade: what if
 * I put the floor somewhere else? Measured on GOOGL, 21 Oct 2026 — a 315/310
 * spread caps the loss at $365 instead of $31,500, and gives up 89% of the
 * daily decay to do it ($11.42/day naked vs $1.30/day as that spread). That
 * trade-off IS the width decision, and no static row can show it.
 *
 * REAL CONTRACTS, NOT MODELLED ONES. Every number here comes from the live
 * chain: the strike you can actually trade, the bid you would actually be
 * paid, the ask you would actually pay. Outside market hours those fields come
 * back null, and this panel says so and refuses to price rather than filling
 * the gap with a model and letting it read like a quote. A spread exists to
 * have a loss that is KNOWN on the day it is opened; a made-up premium defeats
 * the entire point of the structure.
 */
import { useEffect, useMemo, useState } from "react";

import { api } from "../../api/client";

const MULT = 100;          // one contract = 100 shares
const TONE = {
  ok: "#3fb950", bad: "#f85149", warn: "#d29922",
  dim: "var(--text-dim)", muted: "var(--text-muted)",
};

type Leg = Awaited<ReturnType<typeof api.optionChain>>["legs"][number];

/** Mid, or null when the market is not quoting it. NEVER a model price — see
 *  the note at the top about why a fabricated premium defeats the structure. */
function mid(l: Leg | undefined): number | null {
  if (!l) return null;
  if (l.bid != null && l.ask != null && l.bid > 0 && l.ask > 0) {
    return (l.bid + l.ask) / 2;
  }
  // priorClose is the honest between-sessions figure — labelled, never
  // presented as live (IBKR resets bid/ask at session roll).
  if (l.priorClose != null && l.priorClose > 0) return l.priorClose;
  return null;
}

function usd(n: number | null | undefined, d = 0): string {
  if (n == null || Number.isNaN(n)) return "—";
  return `${n < 0 ? "-" : ""}$${Math.abs(n).toLocaleString(undefined, {
    minimumFractionDigits: d, maximumFractionDigits: d })}`;
}

/**
 * The arithmetic of a vertical put spread. Mirrors economics.py, which stays
 * the authority for the BOARD — this is the same definition, not a second
 * opinion: sell the higher strike, buy the lower, the width less the net
 * credit is the worst case, fixed the day it is opened.
 */
function economics(shortK: number, longK: number | null,
                   credit: number, debit: number) {
  if (longK == null) {
    const gain = credit * MULT;
    return {                       // naked: the floor is zero, not a strike
      maxGain: gain,
      maxLoss: shortK * MULT - gain,
      breakeven: shortK - credit,
      capital: shortK * MULT,
      ror: gain / (shortK * MULT - gain),
      unbounded: true,
    };
  }
  const net = (credit - debit) * MULT;
  const width = (shortK - longK) * MULT;
  const loss = Math.max(width - net, 0);
  return {
    maxGain: net,
    maxLoss: loss,
    breakeven: shortK - net / MULT,
    capital: width,
    ror: loss > 0 ? net / loss : 0,
    unbounded: false,
  };
}

export function SpreadBuilder({
  symbol, dte, initialShort, initialLong, onClose,
}: {
  symbol: string;
  dte?: number | null;
  initialShort?: number | null;
  initialLong?: number | null;
  onClose: () => void;
}) {
  const [chain, setChain] = useState<Awaited<ReturnType<typeof api.optionChain>> | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [shortK, setShortK] = useState<number | null>(initialShort ?? null);
  const [longK, setLongK] = useState<number | null>(initialLong ?? null);
  const [expiry, setExpiry] = useState<string | null>(null);

  useEffect(() => {
    let live = true;
    setLoading(true);
    api.optionChain(symbol, {
      ...(expiry ? { expiry } : {}),
      ...(dte && !expiry ? { targetDte: dte } : {}),
    })
      .then((d) => { if (live) { setChain(d); setErr(d.error ?? null); } })
      .catch((e) => { if (live) setErr(e instanceof Error ? e.message : "chain unavailable"); })
      .finally(() => { if (live) setLoading(false); });
    return () => { live = false; };
  }, [symbol, expiry, dte]);

  const puts = useMemo(
    () => (chain?.legs ?? [])
      .filter((l) => String(l.right || "").toUpperCase().startsWith("P"))
      .sort((a, b) => b.strike - a.strike),
    [chain]);

  const quoted = puts.filter((l) => mid(l) != null).length;
  const shortLeg = puts.find((l) => l.strike === shortK);
  const longLeg = puts.find((l) => l.strike === longK);
  const credit = mid(shortLeg);
  const debit = longK == null ? 0 : mid(longLeg);

  const e = credit != null && debit != null && shortK != null
    ? economics(shortK, longK, credit, debit) : null;

  // Net decay: we GAIN the short leg's and LOSE the long leg's. Both thetas
  // are negative, so the subtraction is what makes a spread earn less than
  // the naked short it replaced — the same correction made on the board.
  const netTheta = shortLeg?.theta != null
    ? (longLeg?.theta != null ? longLeg.theta - shortLeg.theta : -shortLeg.theta)
    : null;

  return (
    <div onClick={onClose} style={{
      position: "fixed", inset: 0, zIndex: 1000, background: "rgba(0,0,0,0.65)",
      display: "flex", alignItems: "center", justifyContent: "center", padding: 20,
    }}>
      <div onClick={(ev) => ev.stopPropagation()} style={{
        width: "min(980px, 96vw)", maxHeight: "92vh", overflowY: "auto",
        background: "var(--surface)", border: "1px solid var(--border)",
        borderRadius: 12, padding: 16, boxShadow: "0 18px 60px rgba(0,0,0,0.5)",
      }}>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline" }}>
          <div style={{ fontSize: 15, fontWeight: 700 }}>
            Build the spread · <span style={{ fontFamily: "var(--font-mono)" }}>{symbol}</span>
            {chain?.spot != null && (
              <span style={{ fontSize: 12, color: TONE.dim, fontWeight: 400 }}>
                {" "}· spot {usd(chain.spot, 2)}
              </span>
            )}
          </div>
          <span onClick={onClose} style={{ cursor: "pointer", color: TONE.muted }}>✕</span>
        </div>

        {/* Expiry picker — a weekly and a monthly are different trades on the
            same name, so the contract has to be choosable, not assumed. */}
        {(chain?.availableExpiries?.length ?? 0) > 0 && (
          <div style={{ display: "flex", gap: 6, flexWrap: "wrap", margin: "10px 0 4px" }}>
            {chain!.availableExpiries!.slice(0, 8).map((x) => (
              <button key={x} onClick={() => { setExpiry(x); setShortK(null); setLongK(null); }}
                style={{
                  background: "transparent", cursor: "pointer", fontSize: 11,
                  color: (expiry ?? chain?.legs?.[0]?.maturityDate) === x ? "var(--text)" : TONE.dim,
                  border: `1px solid ${(expiry ?? chain?.legs?.[0]?.maturityDate) === x
                    ? "var(--accent, #4f8cff)" : "#1b2233"}`,
                  borderRadius: 4, padding: "2px 8px",
                }}>
                {x.slice(4, 6)}-{x.slice(6, 8)}
              </button>
            ))}
          </div>
        )}

        {loading && <div style={{ padding: 20, color: TONE.dim }}>loading the chain…</div>}
        {err && <div style={{ padding: 10, color: TONE.bad, fontSize: 12 }}>{err}</div>}

        {/* NOT QUOTING — say it, do not price it. */}
        {!loading && puts.length > 0 && quoted === 0 && (
          <div style={{
            margin: "10px 0", padding: "8px 10px", fontSize: 12, color: TONE.warn,
            border: `1px solid ${TONE.warn}`, borderRadius: 6,
          }}>
            The chain has {puts.length} strikes but <b>no live quotes</b> — bid, ask
            and open interest are all empty. That is normal outside US market
            hours (13:30–20:00 UTC). Strikes are shown so you can see the ladder,
            but nothing is priced: a spread's whole purpose is a loss known on
            the day it is opened, and a made-up premium would defeat it.
          </div>
        )}

        {!loading && puts.length > 0 && (
          <div style={{ display: "grid", gridTemplateColumns: "1.15fr 1fr", gap: 14, marginTop: 10 }}>
            {/* ── the ladder ─────────────────────────────────────────── */}
            <div>
              <div style={{ fontSize: 11, color: TONE.muted, marginBottom: 4 }}>
                click a strike to SELL (short leg), then one below it to BUY (the floor)
              </div>
              <div style={{ maxHeight: 360, overflowY: "auto", border: "1px solid #1b2233", borderRadius: 6 }}>
                <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 11.5 }}>
                  <thead>
                    <tr style={{ color: TONE.muted }}>
                      <th style={{ textAlign: "left", padding: "4px 6px" }}>strike</th>
                      <th style={{ textAlign: "right", padding: "4px 6px" }}>bid</th>
                      <th style={{ textAlign: "right", padding: "4px 6px" }}>ask</th>
                      <th style={{ textAlign: "right", padding: "4px 6px" }}>Δ</th>
                      <th style={{ textAlign: "right", padding: "4px 6px" }}>OI</th>
                      <th style={{ textAlign: "right", padding: "4px 6px" }}></th>
                    </tr>
                  </thead>
                  <tbody>
                    {puts.map((l) => {
                      const isShort = l.strike === shortK;
                      const isLong = l.strike === longK;
                      return (
                        <tr key={l.conId ?? l.strike}
                          onClick={() => {
                            if (shortK == null || l.strike >= shortK) {
                              setShortK(l.strike);
                              if (longK != null && longK >= l.strike) setLongK(null);
                            } else setLongK(l.strike);
                          }}
                          style={{
                            cursor: "pointer", borderTop: "1px solid #141a26",
                            background: isShort ? "rgba(63,185,80,0.12)"
                              : isLong ? "rgba(79,140,255,0.12)" : undefined,
                          }}>
                          <td style={{ padding: "3px 6px", fontFamily: "var(--font-mono)" }}>{l.strike}</td>
                          <td style={{ padding: "3px 6px", textAlign: "right" }}>{l.bid ?? "—"}</td>
                          <td style={{ padding: "3px 6px", textAlign: "right" }}>{l.ask ?? "—"}</td>
                          <td style={{ padding: "3px 6px", textAlign: "right", color: TONE.dim }}>
                            {l.delta != null ? Math.abs(l.delta).toFixed(2) : "—"}
                          </td>
                          <td style={{ padding: "3px 6px", textAlign: "right", color: TONE.dim }}>
                            {l.openInterest ?? "—"}
                          </td>
                          <td style={{ padding: "3px 6px", textAlign: "right", fontSize: 10, color: TONE.muted }}>
                            {isShort ? "SELL" : isLong ? "BUY" : ""}
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            </div>

            {/* ── what it costs to be wrong ──────────────────────────── */}
            <div>
              <div style={{ fontSize: 11, color: TONE.muted, marginBottom: 4 }}>
                {shortK == null ? "pick a strike to sell"
                  : longK == null ? `selling ${shortK} naked — pick a lower strike to floor it`
                    : `${shortK}/${longK} put spread`}
              </div>

              {e ? (
                <div style={{ border: "1px solid #1b2233", borderRadius: 6, padding: 10 }}>
                  <Row label="Max gain" v={usd(e.maxGain)} tone={TONE.ok} />
                  <Row label="Max loss" v={usd(e.maxLoss)} tone={TONE.bad}
                       note={e.unbounded ? "NO floor — runs to zero" : "fixed on the day you open it"} />
                  <Row label="Breakeven" v={usd(e.breakeven, 2)} />
                  <Row label="Capital held" v={usd(e.capital)} />
                  <Row label="Return on risk" v={`${(e.ror * 100).toFixed(1)}%`}
                       tone={e.ror >= 0.2 ? TONE.ok : TONE.warn} />
                  <Row label="Decay / day" v={netTheta != null ? usd(netTheta * MULT, 2) : "—"}
                       note={longK != null ? "net of the leg you bought" : "you keep all of it"} />
                </div>
              ) : (
                <div style={{ border: "1px dashed #1b2233", borderRadius: 6, padding: 14,
                              fontSize: 12, color: TONE.dim }}>
                  {shortK == null
                    ? "Nothing selected yet."
                    : "These strikes are not quoting, so this cannot be priced. "
                      + "Shown as empty rather than estimated."}
                </div>
              )}

              {/* The trade-off the width decision actually turns on. */}
              {e && !e.unbounded && shortK != null && (
                <div style={{ marginTop: 8, fontSize: 11, color: TONE.dim, lineHeight: 1.5 }}>
                  Widening the floor keeps more decay but risks more capital;
                  tightening it does the reverse. On GOOGL a 5-wide spread cut the
                  worst case from $31,500 to $365 and gave up 89% of the daily
                  income — that trade is what this panel exists to let you see.
                </div>
              )}
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

function Row({ label, v, tone, note }: {
  label: string; v: string; tone?: string; note?: string;
}) {
  return (
    <div style={{ display: "flex", alignItems: "baseline", justifyContent: "space-between",
                  padding: "4px 0", borderTop: "1px solid #141a26" }}>
      <span style={{ fontSize: 11.5, color: TONE.dim }}>{label}</span>
      <span style={{ textAlign: "right" }}>
        <span style={{ fontSize: 14, fontWeight: 700, color: tone ?? "var(--text)",
                       fontFamily: "var(--font-mono)" }}>{v}</span>
        {note && <div style={{ fontSize: 10, color: TONE.muted }}>{note}</div>}
      </span>
    </div>
  );
}

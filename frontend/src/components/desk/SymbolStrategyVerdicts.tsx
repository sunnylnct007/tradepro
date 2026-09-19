/**
 * SymbolStrategyVerdicts — what OUR strategies say about this symbol.
 *
 * The one thing a research terminal cannot show you. Koyfin will tell you
 * PLTR's P/E, its 52-week range and what four analysts think; it has no idea
 * that our swing rule looked at it this morning and refused it, or by how
 * much. Owner, 19 Sep 2026, agreeing the split: research stays on Koyfin,
 * TradePro answers "what do MY rules make of this".
 *
 * Every state is named, because the four of them mean completely different
 * things and a blank card conflates all of them:
 *
 *   CANDIDATE    a strategy shortlisted it — with its gate trace
 *   REJECTED     a strategy evaluated it and said no, with the number
 *   NEAR MISS    it failed on degree, not on kind — how far off
 *   NOT LISTED   no strategy shortlisted it today
 *
 * The last is the one that needs care. "No strategy shortlisted this" is NOT
 * "every strategy rejected this" — most strategies never look at most names,
 * and a screen that renders silence as a verdict is inventing one.
 */
const MUTED = "var(--muted)";
const OK = "var(--ok)";
const WARN = "var(--warn)";

export type VerdictRow = {
  symbol: string;
  strategy: string;
  tierRaw?: string;
  action?: string;
  why?: string;
  eligible?: boolean;
  gates?: any[];
  blocks?: string[];
};

export type PricedOut = {
  symbol: string;
  breakeven_win_pct?: number | null;
  reward_risk?: number | null;
  target_pct?: number | null;
};

export type NearMiss = {
  symbol: string;
  sigma_from_mean?: number | null;
  why?: string | null;
  reason?: string | null;
};

const Card: React.CSSProperties = {
  border: "1px solid var(--border)", borderRadius: 8, padding: "10px 12px",
};

function Tag({ text, tone }: { text: string; tone: "ok" | "warn" | "muted" }) {
  const c = tone === "ok" ? OK : tone === "warn" ? WARN : MUTED;
  return (
    <span style={{ border: `1px solid ${c}`, color: c, borderRadius: 999,
                   padding: "1px 8px", fontSize: 11, whiteSpace: "nowrap" }}>
      {text}
    </span>
  );
}

export function SymbolStrategyVerdicts({
  symbol, rows, pricedOut, nearMisses, breakevenBar,
}: {
  symbol: string;
  rows: VerdictRow[];
  pricedOut?: PricedOut[];
  nearMisses?: NearMiss[];
  breakevenBar?: number | null;
}) {
  const sym = (symbol || "").toUpperCase();
  const mine = (rows ?? []).filter((r) => (r.symbol || "").toUpperCase() === sym);
  const rejected = (pricedOut ?? []).filter((r) => (r.symbol || "").toUpperCase() === sym);
  const near = (nearMisses ?? []).filter((r) => (r.symbol || "").toUpperCase() === sym);
  const nothing = mine.length === 0 && rejected.length === 0 && near.length === 0;

  return (
    <div style={Card}>
      <div style={{ fontSize: 12, textTransform: "uppercase", letterSpacing: ".06em",
                    color: MUTED, marginBottom: 8 }}>
        What our strategies say
      </div>

      {mine.map((r, i) => (
        <div key={`c${i}`} style={{ marginBottom: 10 }}>
          <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
            <b>{r.strategy}</b>
            <Tag text={r.eligible === false ? "blocked" : "candidate"}
                 tone={r.eligible === false ? "warn" : "ok"} />
            {/* An ungated sleeve's candidate must never read like a gated
                sleeve's — the desk makes this distinction everywhere. */}
            <Tag text={r.tierRaw === "gated" ? "gated" : "unproven"} tone="muted" />
            {r.action && <span style={{ color: MUTED, fontSize: 12 }}>{r.action}</span>}
          </div>
          {r.why && (
            <div style={{ fontSize: 12, color: MUTED, marginTop: 3 }}>{r.why}</div>
          )}
          {!!r.blocks?.length && (
            <div style={{ fontSize: 12, color: WARN, marginTop: 3 }}>
              blocked: {r.blocks.join("; ")}
            </div>
          )}
          {!!r.gates?.length && (
            <div style={{ fontSize: 11, color: MUTED, marginTop: 4 }}>
              {r.gates.map((g: any, j: number) => (
                <div key={j}>
                  {g?.ok === false ? "✗" : "✓"} {g?.name ?? g?.gate ?? "gate"}
                  {g?.detail ? ` — ${g.detail}` : ""}
                </div>
              ))}
            </div>
          )}
        </div>
      ))}

      {rejected.map((r, i) => (
        <div key={`p${i}`} style={{ marginBottom: 10 }}>
          <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
            <b>Swing</b>
            <Tag text="rejected — cannot pay" tone="warn" />
          </div>
          <div style={{ fontSize: 12, color: MUTED, marginTop: 3 }}>
            Needs <b style={{ color: WARN }}>{r.breakeven_win_pct}%</b> of these to
            win just to break even, against the {breakevenBar ?? 72.8}% this
            strategy actually achieves.
            {r.reward_risk != null && ` R:R ${r.reward_risk}`}
            {r.target_pct != null && `, upside ${r.target_pct}%`}.
            {" "}It passed the entry rule and failed the arithmetic.
          </div>
        </div>
      ))}

      {near.map((r, i) => (
        <div key={`n${i}`} style={{ marginBottom: 10 }}>
          <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
            <b>Swing</b>
            <Tag text="near miss" tone="muted" />
          </div>
          <div style={{ fontSize: 12, color: MUTED, marginTop: 3 }}>
            {r.why || r.reason ||
              (r.sigma_from_mean != null
                ? `${Number(r.sigma_from_mean).toFixed(2)}σ below the 20-day mean — short of the entry band.`
                : "Evaluated and not qualified.")}
          </div>
        </div>
      ))}

      {nothing && (
        <div style={{ fontSize: 12, color: MUTED }}>
          No strategy shortlisted {sym} today.
          <div style={{ marginTop: 4 }}>
            That is <b>not</b> the same as every strategy rejecting it — most
            strategies never look at most names. Silence here means "not
            evaluated", and only the rows above are verdicts.
          </div>
        </div>
      )}
    </div>
  );
}

import { useTradingMode, type TradingMode } from "../contexts/TradingMode";

/** Top-of-page Intraday / Long-term switch. Lives in the global
 * Layout header so it's always visible — a user mid-session should
 * never have to guess which mode the rest of the UI is configured
 * for. Two pills, the active one filled.
 *
 * Behaviour change today is small: persisted to localStorage, drives
 * subtitle copy + a couple of explainer paragraphs. The bigger
 * mode-aware defaults (strategy menu, backtest window, horizon
 * pills) layer on incrementally — see DATA_ROADMAP §14 phases
 * 16.1–16.5. */
export function ModePill() {
  const { mode, setMode } = useTradingMode();
  return (
    <div
      role="group"
      aria-label="Trading mode"
      style={{
        display: "inline-flex",
        gap: 2,
        padding: 2,
        borderRadius: 999,
        background: "rgba(255,255,255,0.04)",
        border: "1px solid var(--border)",
        fontSize: 11,
      }}
    >
      <Pill
        label="Long-term"
        active={mode === "long_term"}
        onClick={() => setMode("long_term")}
        hint="Daily bars · multi-week to multi-year hold"
      />
      <Pill
        label="Intraday"
        active={mode === "intraday"}
        onClick={() => setMode("intraday")}
        hint="1m/5m bars · same-session in/out"
      />
    </div>
  );
}

function Pill({
  label, active, onClick, hint,
}: {
  label: string; active: boolean; onClick: () => void; hint: string;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      title={hint}
      aria-pressed={active}
      style={{
        padding: "4px 10px",
        border: "none",
        borderRadius: 999,
        cursor: "pointer",
        fontWeight: active ? 700 : 500,
        color: active ? "var(--bg)" : "var(--text-dim)",
        background: active ? "var(--text)" : "transparent",
        transition: "background 0.12s ease, color 0.12s ease",
        fontSize: 11,
      }}
    >
      {label}
    </button>
  );
}

/** Mode-aware subtitle helper — keeps the per-mode copy in ONE
 * place so we don't end up with inconsistent phrasing across
 * pages. Pass a key, get a sentence. Add new keys as needed. */
const SUBTITLES: Record<string, Record<TradingMode, string>> = {
  decide_intro: {
    long_term:
      "Daily-close strategy votes across the universe. Intended for "
      + "multi-week to multi-year holds — not for intraday execution.",
    intraday:
      "Daily verdicts here are LONG-TERM signal. For intraday entries, "
      + "switch to the Intraday tab — the strategy menu, timeframe, and "
      + "P&L attribution there are tuned for same-session work.",
  },
};

export function ModeSubtitle({ k }: { k: keyof typeof SUBTITLES }) {
  const { mode } = useTradingMode();
  return <>{SUBTITLES[k][mode]}</>;
}

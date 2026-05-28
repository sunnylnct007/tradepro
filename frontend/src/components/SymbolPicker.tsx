import { useEffect, useRef, useState } from "react";
import { api } from "../api/client";
import type { InstrumentMatch } from "../api/types";

interface Props {
  value: string;
  onChange: (next: string) => void;
  placeholder?: string;
}

const DEBOUNCE_MS = 220;

/**
 * Debounced ticker autocomplete. The backing endpoint
 * (/api/instruments/search) hits Yahoo's symbol search, so the symbol
 * the user picks is guaranteed to resolve in /api/marketdata/candles —
 * no more "I typed NV → 500 from Yahoo" bug. The component still lets
 * raw input through (so power users can type a known ticker without
 * waiting for suggestions); the dropdown is suggestive, not gating.
 */
export function SymbolPicker({ value, onChange, placeholder }: Props) {
  const [draft, setDraft] = useState(value);
  const [matches, setMatches] = useState<InstrumentMatch[]>([]);
  const [open, setOpen] = useState(false);
  const [loading, setLoading] = useState(false);
  const [highlight, setHighlight] = useState(0);
  const wrap = useRef<HTMLDivElement | null>(null);
  const lastQuery = useRef("");

  useEffect(() => setDraft(value), [value]);

  useEffect(() => {
    const q = draft.trim();
    if (q.length < 1) {
      setMatches([]);
      return;
    }
    if (q === lastQuery.current) return;
    let cancelled = false;
    const t = setTimeout(async () => {
      lastQuery.current = q;
      setLoading(true);
      try {
        const r = await api.searchInstruments(q, 10);
        if (!cancelled) {
          setMatches(r.items);
          setHighlight(0);
        }
      } catch {
        if (!cancelled) setMatches([]);
      } finally {
        if (!cancelled) setLoading(false);
      }
    }, DEBOUNCE_MS);
    return () => {
      cancelled = true;
      clearTimeout(t);
    };
  }, [draft]);

  // Click outside closes the dropdown.
  useEffect(() => {
    function onDoc(e: MouseEvent) {
      if (wrap.current && !wrap.current.contains(e.target as Node)) {
        setOpen(false);
      }
    }
    document.addEventListener("mousedown", onDoc);
    return () => document.removeEventListener("mousedown", onDoc);
  }, []);

  function commit(symbol: string) {
    setDraft(symbol);
    onChange(symbol);
    setOpen(false);
  }

  function onKeyDown(e: React.KeyboardEvent<HTMLInputElement>) {
    if (e.key === "ArrowDown") {
      e.preventDefault();
      setOpen(true);
      setHighlight((h) => Math.min(h + 1, matches.length - 1));
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      setHighlight((h) => Math.max(h - 1, 0));
    } else if (e.key === "Enter") {
      if (open && matches[highlight]) {
        e.preventDefault();
        commit(matches[highlight].symbol);
      } else {
        onChange(draft.trim().toUpperCase());
      }
    } else if (e.key === "Escape") {
      setOpen(false);
    }
  }

  return (
    <div
      ref={wrap}
      style={{ position: "relative", display: "flex", flexDirection: "column" }}
    >
      <input
        className="num"
        value={draft}
        placeholder={placeholder ?? "e.g. NVDA, BARC.L"}
        onChange={(e) => {
          setDraft(e.target.value);
          setOpen(true);
        }}
        onFocus={() => draft.trim().length > 0 && setOpen(true)}
        onKeyDown={onKeyDown}
        onBlur={() => {
          // Commit the typed value on blur even if the user hasn't picked
          // a suggestion (preserves the old free-text behaviour).
          if (draft.trim() && draft !== value) onChange(draft.trim().toUpperCase());
        }}
        autoComplete="off"
      />
      {open && (matches.length > 0 || loading) && (
        <ul
          role="listbox"
          style={{
            position: "absolute",
            top: "100%",
            left: 0,
            right: 0,
            margin: 0,
            padding: 4,
            listStyle: "none",
            // var(--bg-card) doesn't exist in styles.css; was falling
            // through to transparent and inheriting the page bg, which
            // killed contrast on the Backtest page. --bg-panel is the
            // dedicated elevated-surface colour.
            background: "var(--bg-panel)",
            color: "var(--text)",
            border: "1px solid var(--border)",
            borderRadius: 6,
            // Higher z-index than the cards / charts that follow on
            // pages like /simulations and /compare so the dropdown
            // never gets hidden under recharts SVG layers.
            zIndex: 200,
            maxHeight: 280,
            overflowY: "auto",
            boxShadow: "0 8px 24px rgba(0,0,0,0.55)",
          }}
        >
          {loading && (
            <li style={{ padding: "6px 8px", fontSize: 11, color: "var(--text-muted)" }}>
              Searching…
            </li>
          )}
          {matches.map((m, i) => (
            <li
              key={`${m.symbol}-${i}`}
              role="option"
              aria-selected={i === highlight}
              onMouseEnter={() => setHighlight(i)}
              onMouseDown={(e) => {
                // mousedown so it fires before the input's blur.
                e.preventDefault();
                commit(m.symbol);
              }}
              style={{
                padding: "6px 8px",
                borderRadius: 4,
                cursor: "pointer",
                background: i === highlight ? "var(--bg-hover)" : "transparent",
                fontSize: 12,
                display: "grid",
                gridTemplateColumns: "minmax(70px, max-content) 1fr auto",
                gap: 8,
                alignItems: "baseline",
              }}
            >
              <span style={{ fontWeight: 600, display: "flex", alignItems: "center", gap: 6, color: "var(--text)" }}>
                {m.symbol}
                {m.source === "trading212" && (
                  <span
                    title="Tradeable in your Trading 212 account"
                    style={{
                      fontSize: 9,
                      padding: "1px 5px",
                      borderRadius: 3,
                      background: "rgba(31,193,107,0.15)",
                      color: "var(--up)",
                      letterSpacing: "0.04em",
                      fontWeight: 600,
                    }}
                  >
                    T212
                  </span>
                )}
              </span>
              <span style={{ color: "var(--text-dim)", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                {m.name}
              </span>
              <span style={{ fontSize: 10, color: "var(--text-muted)" }}>
                {[m.type, m.exchange, m.currency].filter(Boolean).join(" · ")}
              </span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

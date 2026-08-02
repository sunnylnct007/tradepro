/**
 * SymbolSearch — the top-bar global search, wired for real (spec §6a D6c
 * bug #2: "⌘K search cannot find a symbol. The search box does not do the
 * one thing a trading tool's search must do.")
 *
 * Fuzzy match is delegated server-side to /api/instruments/search (already
 * built, already typed as api.searchInstruments — just never wired to any
 * UI). Ticker or company name both work. Recents are a small local list so
 * the box is useful before the user has typed anything.
 *
 * ⌘K / Ctrl+K focuses the box from anywhere in the /desk shell. Enter (or
 * click) on a result navigates to the symbol detail page and records it as
 * a recent. Escape closes the dropdown and blurs.
 */
import { useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { api } from "../../api/client";
import type { InstrumentMatch } from "../../api/types";
import { SEP } from "./shellTheme";

const RECENTS_KEY = "tradepro.search.recents";
const MAX_RECENTS = 8;

function loadRecents(): InstrumentMatch[] {
  try {
    const raw = localStorage.getItem(RECENTS_KEY);
    return raw ? (JSON.parse(raw) as InstrumentMatch[]) : [];
  } catch {
    return [];
  }
}

function pushRecent(match: InstrumentMatch) {
  try {
    const cur = loadRecents().filter((m) => m.symbol !== match.symbol);
    cur.unshift(match);
    localStorage.setItem(RECENTS_KEY, JSON.stringify(cur.slice(0, MAX_RECENTS)));
  } catch {
    // best-effort — a full/blocked localStorage must never break search
  }
}

export function SymbolSearch({
  onSelectSymbol,
}: {
  /** When provided (i.e. rendered inside /desk via DeskShell), a picked
   * result opens in-place via SymbolDetailRail — /desk is a single
   * composite cockpit that never navigates away (see Desk.tsx). Falls back
   * to navigating to the legacy /symbol/:ticker route only when absent,
   * so SymbolSearch still works standalone outside the desk shell. */
  onSelectSymbol?: (symbol: string) => void;
}) {
  const navigate = useNavigate();
  const inputRef = useRef<HTMLInputElement>(null);
  const rootRef = useRef<HTMLDivElement>(null);
  const [query, setQuery] = useState("");
  const [open, setOpen] = useState(false);
  const [results, setResults] = useState<InstrumentMatch[]>([]);
  const [loading, setLoading] = useState(false);
  const [activeIdx, setActiveIdx] = useState(0);

  // Global ⌘K / Ctrl+K — focus + open from anywhere in the shell.
  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") {
        e.preventDefault();
        inputRef.current?.focus();
        inputRef.current?.select();
        setOpen(true);
      }
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  // Click-outside closes the dropdown.
  useEffect(() => {
    function onClick(e: MouseEvent) {
      if (rootRef.current && !rootRef.current.contains(e.target as Node)) {
        setOpen(false);
      }
    }
    document.addEventListener("mousedown", onClick);
    return () => document.removeEventListener("mousedown", onClick);
  }, []);

  // Debounced fetch — 200ms, cancels a stale in-flight request via a
  // generation counter so a fast typist never sees an out-of-order result.
  useEffect(() => {
    const q = query.trim();
    if (q.length < 1) {
      setResults([]);
      setLoading(false);
      return;
    }
    setLoading(true);
    let cancelled = false;
    const t = window.setTimeout(async () => {
      try {
        const resp = await api.searchInstruments(q, 10);
        if (!cancelled) setResults(resp.items);
      } catch {
        if (!cancelled) setResults([]);
      } finally {
        if (!cancelled) setLoading(false);
      }
    }, 200);
    return () => {
      cancelled = true;
      window.clearTimeout(t);
    };
  }, [query]);

  const showingRecents = query.trim().length === 0;
  const list = showingRecents ? loadRecents() : results;

  useEffect(() => {
    setActiveIdx(0);
  }, [query, open]);

  function select(match: InstrumentMatch) {
    pushRecent(match);
    setQuery("");
    setOpen(false);
    inputRef.current?.blur();
    if (onSelectSymbol) {
      onSelectSymbol(match.symbol);
    } else {
      navigate(`/symbol/${encodeURIComponent(match.symbol)}`);
    }
  }

  function onKeyDown(e: React.KeyboardEvent<HTMLInputElement>) {
    if (e.key === "Escape") {
      setOpen(false);
      inputRef.current?.blur();
      return;
    }
    if (!open || list.length === 0) return;
    if (e.key === "ArrowDown") {
      e.preventDefault();
      setActiveIdx((i) => Math.min(i + 1, list.length - 1));
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      setActiveIdx((i) => Math.max(i - 1, 0));
    } else if (e.key === "Enter") {
      e.preventDefault();
      const m = list[activeIdx];
      if (m) select(m);
    }
  }

  return (
    <div ref={rootRef} style={{ position: "relative", width: 220, maxWidth: "32vw", flexShrink: 1, minWidth: 0 }}>
      <div
        style={{
          display: "flex", alignItems: "center", gap: 8,
          background: "#0a0e17", border: `1px solid ${SEP}`, borderRadius: 6,
          padding: "6px 10px", color: "var(--text-muted)", fontSize: 12,
        }}
      >
        <span>🔍</span>
        <input
          ref={inputRef}
          value={query}
          onChange={(e) => { setQuery(e.target.value); setOpen(true); }}
          onFocus={() => setOpen(true)}
          onKeyDown={onKeyDown}
          placeholder="Search ticker or company…"
          aria-label="Search symbols"
          style={{
            background: "transparent", border: "none", outline: "none",
            color: "var(--text)", fontSize: 12, width: "100%", minWidth: 0,
          }}
        />
        {!query && <span style={{ opacity: 0.7, flexShrink: 0 }}>⌘K</span>}
      </div>

      {open && (list.length > 0 || loading) && (
        <div
          role="listbox"
          style={{
            position: "absolute", top: "calc(100% + 4px)", left: 0, width: 320,
            maxHeight: 360, overflowY: "auto", background: "var(--bg-panel)",
            border: `1px solid ${SEP}`, borderRadius: 8, boxShadow: "var(--shadow)",
            zIndex: 200,
          }}
        >
          {showingRecents && (
            <div style={{ padding: "6px 10px", fontSize: 10, textTransform: "uppercase", letterSpacing: "0.05em", color: "var(--text-muted)" }}>
              Recent
            </div>
          )}
          {loading && list.length === 0 && (
            <div style={{ padding: "10px", fontSize: 12, color: "var(--text-muted)" }}>Searching…</div>
          )}
          {list.map((m, i) => (
            <div
              key={`${m.symbol}-${m.source}-${i}`}
              role="option"
              aria-selected={i === activeIdx}
              onMouseEnter={() => setActiveIdx(i)}
              onMouseDown={(e) => { e.preventDefault(); select(m); }}
              style={{
                display: "flex", alignItems: "center", gap: 8, padding: "8px 10px",
                cursor: "pointer", fontSize: 12,
                background: i === activeIdx ? "rgba(79,140,255,0.12)" : "transparent",
              }}
            >
              <span style={{ fontWeight: 700, color: "var(--text)", minWidth: 56 }}>{m.symbol}</span>
              <span style={{ color: "var(--text-dim)", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", flex: 1 }}>
                {m.name}
              </span>
              {m.exchange && (
                <span style={{ color: "var(--text-muted)", fontSize: 10, flexShrink: 0 }}>{m.exchange}</span>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

/**
 * /universes — manage Wikipedia-scraped symbol universes.
 *
 * Two halves:
 *   1. Universe list (top): all ingested universes with effective
 *      symbol counts + last-fetched timestamps. Click → drills in.
 *   2. Per-universe symbols table (bottom): sortable + searchable +
 *      include/exclude toggles. Trader curates the universe once,
 *      the daily Ichimoku auto-trigger respects those overrides on
 *      every refresh (Wikipedia refresh wipes the symbol table but
 *      NEVER touches universe_overrides).
 */
import { useEffect, useMemo, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { api } from "../api/client";

type UniverseSummary = Awaited<ReturnType<typeof api.universes>>["universes"][number];
type UniverseSymbol = Awaited<ReturnType<typeof api.universe>>["symbols"][number];

export function Universes() {
  const [params, setParams] = useSearchParams();
  const selected = params.get("name") ?? "";

  const [list, setList] = useState<UniverseSummary[] | null>(null);
  const [listErr, setListErr] = useState<string | null>(null);

  useEffect(() => {
    api.universes()
      .then((r) => { setList(r.universes); setListErr(null); })
      .catch((e) => setListErr(String(e)));
  }, []);

  return (
    <div style={{ padding: 24, maxWidth: 1100 }}>
      <h1 style={{ margin: 0, fontSize: 22 }}>Universes</h1>
      <p style={{ color: "var(--text-dim)", fontSize: 13, marginTop: 6, marginBottom: 16 }}>
        Wikipedia-scraped symbol lists (S&amp;P 500, NASDAQ 100, FTSE 100, …).
        The Mac worker refreshes them daily; your INCLUDE / EXCLUDE
        overrides survive every refresh and feed the cockpit's
        Universe pills + the daily Ichimoku auto-trigger.
      </p>

      {listErr && (
        <div style={ERROR_BOX}>
          {listErr} — the universe pipeline may not be ingested yet.
          Run <code>tradepro-refresh-universes --push</code> on the Mac.
        </div>
      )}

      <UniverseList
        list={list}
        selected={selected}
        onSelect={(name) => setParams({ name })}
      />

      {selected && (
        <UniverseDetail
          key={selected}
          name={selected}
          onChanged={() => {
            // Reload list so override counts refresh.
            api.universes().then((r) => setList(r.universes)).catch(() => {});
          }}
        />
      )}
    </div>
  );
}

function UniverseList({
  list, selected, onSelect,
}: {
  list: UniverseSummary[] | null;
  selected: string;
  onSelect: (name: string) => void;
}) {
  if (list === null) {
    return <div style={{ color: "var(--text-muted)", fontSize: 12 }}>Loading universes…</div>;
  }
  if (list.length === 0) {
    return <div style={{ color: "var(--text-muted)", fontSize: 12 }}>
      No universes ingested yet.
    </div>;
  }
  return (
    <div style={{
      display: "flex", flexWrap: "wrap", gap: 6, marginBottom: 18,
    }}>
      {list.map((u) => {
        const isSelected = selected === u.name;
        const effective = u.symbolCount - u.excludedOverrides;
        return (
          <button
            key={u.name}
            onClick={() => onSelect(u.name)}
            title={
              `${u.symbolCount} symbols (Wikipedia) · `
              + `${u.excludedOverrides} excluded by you · `
              + `${u.includedOverrides} force-included · `
              + `fetched ${new Date(u.fetchedAtUtc).toLocaleString()}`
            }
            style={{
              padding: "6px 12px", fontSize: 12, borderRadius: 999,
              border: `1px solid ${isSelected ? "#a855f7" : "var(--border)"}`,
              background: isSelected ? "rgba(168,85,247,0.10)" : "transparent",
              color: isSelected ? "#a855f7" : "var(--text-dim)",
              cursor: "pointer", fontFamily: "monospace",
              display: "inline-flex", gap: 6, alignItems: "baseline",
            }}
          >
            {u.name}
            <span style={{ fontSize: 10, opacity: 0.7 }}>{effective}</span>
            {u.excludedOverrides > 0 && (
              <span style={{ fontSize: 10, color: "#ef4444" }} title={`${u.excludedOverrides} excluded`}>
                −{u.excludedOverrides}
              </span>
            )}
          </button>
        );
      })}
    </div>
  );
}

function UniverseDetail({
  name, onChanged,
}: {
  name: string;
  onChanged: () => void;
}) {
  const [data, setData] = useState<{
    header: UniverseSummary | null;
    symbols: UniverseSymbol[];
  } | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [filter, setFilter] = useState("");
  const [acting, setActing] = useState<string | null>(null);

  const load = async () => {
    try {
      const r = await api.universe(name);
      setData(r as unknown as { header: UniverseSummary; symbols: UniverseSymbol[] });
      setErr(null);
    } catch (e) { setErr(String(e)); }
  };
  useEffect(() => { void load(); }, [name]);

  const toggle = async (sym: UniverseSymbol) => {
    setActing(sym.ticker);
    try {
      if (sym.overrideAction === "EXCLUDE") {
        await api.clearUniverseOverride(name, sym.ticker);
      } else {
        await api.setUniverseOverride(name, {
          Ticker: sym.ticker,
          Action: "EXCLUDE",
          Note: "excluded via /universes page",
        });
      }
      await load();
      onChanged();
    } catch (e) {
      setErr(String(e));
    } finally {
      setActing(null);
    }
  };

  const filtered = useMemo(() => {
    if (!data) return [];
    if (!filter) return data.symbols;
    const q = filter.toLowerCase();
    return data.symbols.filter((s) =>
      s.ticker.toLowerCase().includes(q) ||
      (s.name && s.name.toLowerCase().includes(q)) ||
      (s.sector && s.sector.toLowerCase().includes(q)),
    );
  }, [data, filter]);

  if (err) return <div style={ERROR_BOX}>{err}</div>;
  if (!data) return <div style={{ color: "var(--text-muted)", fontSize: 12 }}>Loading symbols…</div>;

  const effective = data.symbols.filter((s) => s.effective).length;
  return (
    <div style={{
      border: "1px solid var(--border)", borderRadius: 8, padding: 14,
    }}>
      <div style={{ display: "flex", alignItems: "baseline", gap: 10, marginBottom: 10 }}>
        <h2 style={{ margin: 0, fontSize: 16 }}>{name}</h2>
        <span style={{ fontSize: 11, color: "var(--text-muted)" }}>
          {effective} effective of {data.symbols.length} total
        </span>
        <input
          type="text"
          placeholder="filter ticker / name / sector"
          value={filter}
          onChange={(e) => setFilter(e.target.value)}
          style={{
            marginLeft: "auto",
            padding: "4px 8px", fontSize: 11,
            border: "1px solid var(--border)", borderRadius: 4,
            background: "transparent", color: "var(--text)",
            width: 220, fontFamily: "monospace",
          }}
        />
      </div>
      <div style={{
        border: "1px solid var(--border)", borderRadius: 6,
        maxHeight: 540, overflowY: "auto",
      }}>
        <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 12 }}>
          <thead>
            <tr style={{
              color: "var(--text-dim)",
              background: "var(--bg-hover, rgba(255,255,255,0.03))",
              position: "sticky", top: 0,
            }}>
              <th style={TH}>Ticker</th>
              <th style={TH}>Name</th>
              <th style={TH}>Sector</th>
              <th style={TH}>Status</th>
              <th style={TH}>Action</th>
            </tr>
          </thead>
          <tbody>
            {filtered.map((s) => (
              <SymbolRow
                key={s.ticker}
                row={s}
                busy={acting === s.ticker}
                onToggle={() => void toggle(s)}
              />
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function SymbolRow({
  row, busy, onToggle,
}: {
  row: UniverseSymbol;
  busy: boolean;
  onToggle: () => void;
}) {
  const statusFg =
    row.overrideAction === "EXCLUDE" ? "#ef4444" :
    row.overrideAction === "INCLUDE" ? "#a855f7" :
    "var(--text-dim)";
  const statusLabel =
    row.overrideAction === "EXCLUDE" ? "EXCLUDED" :
    row.overrideAction === "INCLUDE" ? "INCLUDED" :
    "base";
  return (
    <tr style={{ borderTop: "1px solid var(--border)", opacity: row.effective ? 1 : 0.55 }}>
      <td style={{ ...TD, fontFamily: "monospace", fontWeight: 600 }}>{row.ticker}</td>
      <td style={TD}>{row.name ?? "—"}</td>
      <td style={{ ...TD, color: "var(--text-dim)" }}>{row.sector ?? "—"}</td>
      <td style={{ ...TD, color: statusFg, fontFamily: "monospace", fontSize: 10 }}>
        {statusLabel}
      </td>
      <td style={TD}>
        <button
          onClick={onToggle}
          disabled={busy}
          style={{
            fontSize: 10, padding: "2px 8px",
            border: `1px solid ${row.overrideAction === "EXCLUDE" ? "#1fc16b" : "var(--border)"}`,
            borderRadius: 3,
            background: "transparent",
            color: row.overrideAction === "EXCLUDE" ? "#1fc16b" : "var(--text-dim)",
            cursor: busy ? "wait" : "pointer",
          }}
          title={
            row.overrideAction === "EXCLUDE"
              ? "Re-include this symbol in the universe"
              : "Exclude this symbol from the universe (you can always re-include)"
          }
        >
          {row.overrideAction === "EXCLUDE" ? "include" : "exclude"}
        </button>
      </td>
    </tr>
  );
}

const ERROR_BOX: React.CSSProperties = {
  padding: "10px 12px",
  border: "1px solid rgba(239,68,68,0.3)",
  background: "rgba(239,68,68,0.06)",
  color: "var(--down)",
  borderRadius: 6, fontSize: 13,
  marginBottom: 14,
};

const TH: React.CSSProperties = {
  textAlign: "left", padding: "6px 10px", fontSize: 10,
  fontWeight: 700, letterSpacing: "0.04em", textTransform: "uppercase",
};
const TD: React.CSSProperties = { padding: "5px 10px" };

// Suppress unused-import lints when react-router-dom's Link isn't
// pulled into the JSX paths above.
export const _ensureLinkImport = Link;

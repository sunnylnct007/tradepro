/**
 * Universes — a desk view (10 Nov 2026; was the standalone /universes page).
 *
 * THE "harvest" UNIVERSE IS A LIST OF ON/OFF SWITCHES, and the page has to
 * read that way. Owner, 10 Nov: "i see all exclude only" — the generic
 * include/exclude rendering showed every row as "base" with an "exclude"
 * button, while the actual harvest state (ON for 84, OFF for 876) sat in the
 * `industry` column as text. For a universe whose rows carry ON/OFF the state
 * is: your override if you set one, else the seed; the button says "turn off"
 * / "turn on"; the default filter shows the names that are ON.
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
  // Inside the desk shell the URL also carries ?view=universes — merge, never
  // replace, or selecting a universe would drop the view.
  const selected = params.get("name") ?? "harvest";

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
        <b>harvest</b> is the list of names TradePro fetches bars for every night and
        uses on its screens — each row is an ON/OFF switch and the nightly lane honours
        it. The other lists are reference index memberships (S&amp;P 500, NASDAQ 100, …);
        excluding a name there removes it from that list's screens.
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
        onSelect={(name) => setParams((prev) => { const q = new URLSearchParams(prev); q.set("name", name); return q; })}
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

  // ON/OFF universes (harvest): rows carry industry "ON"/"OFF" as the seed.
  const switchMode = !!data?.symbols.some((s) => s.industry === "ON" || s.industry === "OFF");
  const [show, setShow] = useState<"on" | "off" | "all">("on");

  const toggle = async (sym: UniverseSymbol) => {
    setActing(sym.ticker);
    try {
      if (switchMode) {
        const seedOn = sym.industry === "ON";
        const target = !isOn(sym);
        if (target === seedOn) {
          await api.clearUniverseOverride(name, sym.ticker);          // back to the seed
        } else {
          await api.setUniverseOverride(name, {
            Ticker: sym.ticker,
            Action: target ? "INCLUDE" : "EXCLUDE",
            Note: `${target ? "turned on" : "turned off"} from the Universe screen`,
          });
        }
      } else if (sym.overrideAction === "EXCLUDE") {
        await api.clearUniverseOverride(name, sym.ticker);
      } else {
        await api.setUniverseOverride(name, {
          Ticker: sym.ticker,
          Action: "EXCLUDE",
          Note: "excluded via the Universe screen",
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
    let rows = data.symbols;
    if (switchMode && show !== "all") rows = rows.filter((s) => isOn(s) === (show === "on"));
    if (!filter) return rows;
    const q = filter.toLowerCase();
    return rows.filter((s) =>
      s.ticker.toLowerCase().includes(q) ||
      (s.name && s.name.toLowerCase().includes(q)) ||
      (s.sector && s.sector.toLowerCase().includes(q)),
    );
  }, [data, filter, switchMode, show]);

  if (err) return <div style={ERROR_BOX}>{err}</div>;
  if (!data) return <div style={{ color: "var(--text-muted)", fontSize: 12 }}>Loading symbols…</div>;

  const effective = data.symbols.filter((s) => s.effective).length;
  const onCount = data.symbols.filter(isOn).length;
  return (
    <div style={{
      border: "1px solid var(--border)", borderRadius: 8, padding: 14,
    }}>
      <div style={{ display: "flex", alignItems: "baseline", gap: 10, marginBottom: 10, flexWrap: "wrap" }}>
        <h2 style={{ margin: 0, fontSize: 16 }}>{name}</h2>
        <span style={{ fontSize: 11, color: "var(--text-muted)" }}>
          {switchMode
            ? `${onCount} ON · ${data.symbols.length - onCount} OFF of ${data.symbols.length} tracked`
            : `${effective} effective of ${data.symbols.length} total`}
        </span>
        {switchMode && (
          <span style={{ display: "inline-flex", gap: 4 }}>
            {(["on", "off", "all"] as const).map((k) => (
              <button key={k} onClick={() => setShow(k)} style={{
                fontSize: 10, padding: "2px 9px", borderRadius: 999, cursor: "pointer",
                border: `1px solid ${show === k ? "#a855f7" : "var(--border)"}`,
                background: show === k ? "rgba(168,85,247,0.10)" : "transparent",
                color: show === k ? "#a855f7" : "var(--text-dim)",
              }}>{k.toUpperCase()}</button>
            ))}
          </span>
        )}
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
                switchMode={switchMode}
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

/** Effective ON/OFF for a switch-mode row: your override wins, else the seed. */
function isOn(row: UniverseSymbol): boolean {
  if (row.overrideAction === "INCLUDE") return true;
  if (row.overrideAction === "EXCLUDE") return false;
  return row.industry === "ON";
}

function SymbolRow({
  row, switchMode, busy, onToggle,
}: {
  row: UniverseSymbol;
  switchMode: boolean;
  busy: boolean;
  onToggle: () => void;
}) {
  if (switchMode) {
    const on = isOn(row);
    const overridden = row.overrideAction != null;
    return (
      <tr style={{ borderTop: "1px solid var(--border)", opacity: on ? 1 : 0.6 }}>
        <td style={{ ...TD, fontFamily: "monospace", fontWeight: 600 }}>{row.ticker}</td>
        <td style={{ ...TD, color: "var(--text-dim)" }}>{row.name ?? "—"}</td>
        <td style={{ ...TD, color: "var(--text-dim)" }}>{row.sector ?? "—"}</td>
        <td style={{ ...TD, fontFamily: "monospace", fontSize: 10 }}>
          <span style={{ padding: "1px 8px", borderRadius: 999, fontWeight: 700,
                         border: `1px solid ${on ? "#1fc16b" : "var(--border)"}`,
                         color: on ? "#1fc16b" : "var(--text-muted)" }}>
            {on ? "ON" : "OFF"}
          </span>
          {overridden && <span style={{ marginLeft: 6, color: "#a855f7" }} title="you set this; the seed said the opposite">(you)</span>}
        </td>
        <td style={TD}>
          <button onClick={onToggle} disabled={busy} style={{
            fontSize: 10, padding: "2px 8px", borderRadius: 3, background: "transparent",
            border: `1px solid ${on ? "var(--border)" : "#1fc16b"}`,
            color: on ? "var(--text-dim)" : "#1fc16b", cursor: busy ? "wait" : "pointer",
          }} title={on ? "Stop harvesting and using this name" : "Harvest and use this name from tonight"}>
            {on ? "turn off" : "turn on"}
          </button>
        </td>
      </tr>
    );
  }
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

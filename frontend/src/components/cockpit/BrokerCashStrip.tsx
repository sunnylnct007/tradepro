/**
 * BrokerCashStrip — one tile per connected broker showing free/total
 * cash + status. Reads /api/integrations/cash-summary so every broker
 * appears even when one is down (each row is independent).
 *
 * Currencies vary per broker (T212 USD demo, IG GBP) so each tile
 * shows its own currency rather than forcing FX conversion.
 */
import { useEffect, useState } from "react";
import { api } from "../../api/client";

type Row = {
  broker: string;
  label: string;
  status: "ok" | "degraded" | "down" | "disabled";
  currency?: string | null;
  free?: number | null;
  invested?: number | null;
  total?: number | null;
  openPnl?: number | null;
  available?: number | null;
  balance?: number | null;
  error?: string | null;
  note?: string | null;
  mode?: string | null;
};

export function BrokerCashStrip() {
  const [rows, setRows] = useState<Row[]>([]);
  const [err, setErr] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    const load = async () => {
      try {
        const d = await api.cashSummary();
        if (cancelled) return;
        setRows(d.brokers as Row[]);
        setErr(null);
      } catch (e) {
        if (cancelled) return;
        setErr(String(e));
      } finally {
        if (!cancelled) setLoading(false);
      }
    };
    void load();
    const t = setInterval(load, 30_000);
    return () => { cancelled = true; clearInterval(t); };
  }, []);

  if (loading && rows.length === 0) {
    return <div style={{ fontSize: 11, color: "var(--text-muted)" }}>Loading broker cash…</div>;
  }
  if (err) {
    return <div style={{ fontSize: 11, color: "var(--down)" }}>cash-summary failed: {err}</div>;
  }
  return (
    <div style={{
      display: "grid",
      gridTemplateColumns: "repeat(auto-fit, minmax(220px, 1fr))",
      gap: 8,
    }}>
      {rows.map((r) => <CashTile key={r.broker} r={r} />)}
    </div>
  );
}

function CashTile({ r }: { r: Row }) {
  const colour = statusColour(r.status);
  const isOk = r.status === "ok";
  const free = r.free ?? r.available ?? null;
  const total = r.total ?? r.balance ?? null;
  return (
    <div style={{
      padding: "8px 10px",
      borderLeft: `3px solid ${colour}`,
      border: `1px solid var(--border)`,
      borderRadius: 6,
      background: "rgba(0,0,0,0.10)",
    }}>
      <div style={{
        display: "flex", justifyContent: "space-between",
        alignItems: "baseline", gap: 8, marginBottom: 4,
      }}>
        <strong style={{ fontSize: 12, color: "var(--text)" }}>{r.label}</strong>
        <span style={{
          fontSize: 9, color: colour, fontWeight: 700,
          letterSpacing: "0.06em", textTransform: "uppercase",
        }}>{r.status}</span>
      </div>
      {isOk && free !== null && (
        <div style={{ fontSize: 11, fontFamily: "monospace", color: "var(--text)" }}>
          Free: <strong>{(r.currency ?? "")} {fmt(free)}</strong>
          {total !== null && (
            <> {" · "}Total: <strong>{fmt(total)}</strong></>
          )}
          {r.openPnl != null && (
            <> {" · "}
              <span style={{ color: r.openPnl >= 0 ? "var(--up)" : "var(--down)" }}>
                P&L {r.openPnl >= 0 ? "+" : ""}{fmt(r.openPnl)}
              </span>
            </>
          )}
        </div>
      )}
      {!isOk && (
        <div style={{ fontSize: 11, color: "var(--text-dim)", marginTop: 2 }}>
          {r.note ?? r.error ?? "unavailable"}
        </div>
      )}
    </div>
  );
}

function statusColour(s: Row["status"]): string {
  switch (s) {
    case "ok": return "var(--up)";
    case "degraded": return "var(--neutral)";
    case "down": return "var(--down)";
    case "disabled": return "var(--text-muted)";
  }
}

function fmt(n: number): string {
  if (Math.abs(n) > 1e6) return n.toLocaleString(undefined, { maximumFractionDigits: 0 });
  return n.toLocaleString(undefined, { maximumFractionDigits: 2, minimumFractionDigits: 2 });
}

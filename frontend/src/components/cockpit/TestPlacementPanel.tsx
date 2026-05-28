/**
 * TestPlacementPanel — operator smoke test for the OMS → T212 demo
 * chain. Bypasses the strategy / Mac daemon entirely: submits an
 * OrderIntent directly to /api/oms/orders and auto-approves so the
 * .NET OmsService → Trading212DemoClient → T212 demo path runs end-
 * to-end. Useful for verifying the broker wiring before triggering a
 * real strategy session, and for sanity checks after a redeploy.
 *
 * Defaults: BUY 1 AAPL (small enough not to move T212 demo cash
 * meaningfully; symbol T212 always has).
 *
 * Extracted from TraderCockpit.tsx for readability. Form fields
 * (Symbol / Side / Qty) use the shared FieldGroup helper kept in
 * this file rather than the trigger form's copy — they happen to
 * look the same today but their styling shouldn't be coupled.
 */
import { useState } from "react";
import { api } from "../../api/client";

export function TestPlacementPanel({ onPlaced }: { onPlaced: () => void }) {
  const [symbol, setSymbol] = useState("AAPL");
  const [qty, setQty] = useState(1);
  const [side, setSide] = useState<"BUY" | "SELL">("BUY");
  const [submitting, setSubmitting] = useState(false);
  const [feedback, setFeedback] = useState<string | null>(null);

  const fire = async () => {
    setSubmitting(true);
    setFeedback(null);
    try {
      // Browser crypto.randomUUID generates the ClientOrderId — OMS
      // dedupes on it so a double-click doesn't double-place.
      const clientOrderId = crypto.randomUUID();
      const enqueued = await api.omsEnqueue({
        ClientOrderId: clientOrderId,
        Broker: "T212_DEMO",
        Symbol: symbol.toUpperCase(),
        Side: side,
        Qty: qty,
        OrderType: "MKT",
        StrategyId: "manual_test_cockpit",
        PlacedBy: "HUMAN",
        TimeInForce: "DAY",
      });
      await api.omsApprove(enqueued.id);
      setFeedback(
        `✓ Enqueued + approved ${side} ${qty} ${symbol.toUpperCase()} — watch "Order placed" / "Trade executed" panels.`,
      );
      onPlaced();
    } catch (e) {
      setFeedback(`Failed: ${e}`);
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div>
      <div style={{
        fontSize: 11, color: "var(--text-muted)",
        marginBottom: 10, lineHeight: 1.5,
      }}>
        Bypasses the strategy + Mac daemon — creates an OMS intent
        directly + auto-approves so the .NET OmsService → T212 demo
        chain runs end-to-end. Use after a redeploy to verify nothing
        broke before triggering a real strategy run.
      </div>
      <div style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "end" }}>
        <FieldGroup label="Symbol">
          <input
            type="text"
            value={symbol}
            onChange={(e) => setSymbol(e.target.value)}
            style={INPUT}
          />
        </FieldGroup>
        <FieldGroup label="Side">
          <SideToggle side={side} onChange={setSide} />
        </FieldGroup>
        <FieldGroup label="Qty">
          <input
            type="number"
            min={1}
            max={1000}
            value={qty}
            onChange={(e) => setQty(Math.max(1, Number(e.target.value) || 1))}
            style={{ ...INPUT, width: 80 }}
          />
        </FieldGroup>
        <button
          onClick={fire}
          disabled={submitting}
          style={{
            padding: "6px 14px", fontSize: 12, fontWeight: 600,
            background: submitting ? "var(--text-muted)" : "#4f8cff",
            color: "white", border: "none", borderRadius: 4,
            cursor: submitting ? "wait" : "pointer",
          }}
        >
          {submitting ? "Placing…" : `Fire ${side} ${qty} ${symbol.toUpperCase()}`}
        </button>
      </div>
      {feedback && (
        <div style={{
          marginTop: 8, fontSize: 11,
          color: feedback.startsWith("✓") ? "#1fc16b" : "var(--down)",
        }}>
          {feedback}
        </div>
      )}
    </div>
  );
}

function SideToggle({
  side, onChange,
}: {
  side: "BUY" | "SELL"; onChange: (s: "BUY" | "SELL") => void;
}) {
  return (
    <div style={{ display: "flex", gap: 4 }}>
      {(["BUY", "SELL"] as const).map((s) => (
        <button
          key={s}
          onClick={() => onChange(s)}
          style={{
            ...INPUT,
            width: 56,
            cursor: "pointer",
            color: side === s
              ? s === "BUY" ? "#1fc16b" : "#ef4444"
              : "var(--text-dim)",
            borderColor: side === s
              ? s === "BUY" ? "#1fc16b" : "#ef4444"
              : "var(--border)",
            fontWeight: side === s ? 600 : 400,
            textAlign: "center",
          }}
        >
          {s}
        </button>
      ))}
    </div>
  );
}

function FieldGroup({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 2 }}>
      <span style={{
        fontSize: 9, color: "var(--text-muted)",
        letterSpacing: "0.04em", textTransform: "uppercase",
      }}>
        {label}
      </span>
      {children}
    </div>
  );
}

const INPUT: React.CSSProperties = {
  padding: "5px 8px", fontSize: 12,
  border: "1px solid var(--border)", borderRadius: 4,
  background: "transparent", color: "var(--text)",
};

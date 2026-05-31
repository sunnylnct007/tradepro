/**
 * SystemCaveatsBanner — makes the platform's *serious caveats* impossible
 * to miss, so the trader never tests assuming "all is sorted".
 *
 * The existing /health check tests CONNECTIVITY ("can I reach T212?") and
 * happily returns verdict:"ok" while the system genuinely can't trade —
 * e.g. the demo account is out of buying power, orders are stuck after a
 * market-closed placement, or the sentiment LLM is offline so a COMPASS
 * factor is blind. This banner re-derives SEVERITY from the raw signals
 * (broker free cash, provider state, OMS order states) and shows red/amber
 * caveats with a plain-English "what it means", on the surfaces the trader
 * actually looks at. It auto-clears when the underlying condition clears.
 *
 * Data: /health/integrations (broker cash + provider state) + OMS orders
 * (execution health). No new backend — derivation lives here on purpose so
 * a stale API verdict can't mask a real problem.
 */
import { useCallback, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../../api/client";

type Severity = "red" | "amber";
type Caveat = { sev: Severity; title: string; detail: string };

const RED = "#ef4444";
const AMBER = "#f59e0b";

// Below this much free broker cash, a multi-name strategy basket will start
// hitting insufficient-free-for-stocks rejections — treat as a hard caveat.
const LOW_CASH_USD = 2_000;

/** Pull "free 248.73 USD" / "available 10000908 GBP" out of a provider
 * detail string. Returns {amount, ccy} or null. */
function parseCash(detail: string): { amount: number; ccy: string } | null {
  const m = detail.match(/(?:free|available)\s+([\d,]+(?:\.\d+)?)\s*([A-Z]{3})/i);
  if (!m) return null;
  return { amount: Number(m[1].replace(/,/g, "")), ccy: m[2].toUpperCase() };
}

export function SystemCaveatsBanner() {
  const [caveats, setCaveats] = useState<Caveat[]>([]);
  const [open, setOpen] = useState(true);

  const compute = useCallback(async () => {
    const found: Caveat[] = [];

    // ── Broker + provider readiness ────────────────────────────────
    try {
      const h = await api.integrationsHealth();
      for (const p of h.providers) {
        // Broker buying power — the zero-fill root cause. status can be
        // "ok" (connectivity) while free cash is depleted.
        const cash = parseCash(p.detail);
        const isBroker = /trading212|ig/i.test(p.provider);
        if (isBroker && cash && cash.amount < LOW_CASH_USD && cash.ccy === "USD") {
          found.push({
            sev: "red",
            title: `${p.label}: buying power ${cash.ccy} ${cash.amount.toFixed(0)}`,
            detail:
              "Strategy BUY baskets will be rejected (insufficient-free-for-stocks). "
              + "Top up / reset the demo account or add a capital gate before expecting fills.",
          });
        }
        if (p.status === "down") {
          found.push({ sev: "red", title: `${p.label} DOWN`, detail: p.detail });
        } else if (p.status === "degraded") {
          found.push({ sev: "amber", title: `${p.label} degraded`, detail: p.detail });
        } else if (p.status === "disabled") {
          // LLM disabled => the sentiment COMPASS factor (10% weight) is
          // blind. Worth an amber so the trader knows the score is partial.
          const isLlm = /llm|ollama/i.test(p.provider);
          found.push({
            sev: "amber",
            title: isLlm ? "Sentiment factor offline (LLM unreachable)" : `${p.label} disabled`,
            detail: isLlm
              ? "News-sentiment scoring needs the Mac LLM. Until it's reachable, the sentiment factor defaults neutral (5/10) — 10% of every COMPASS score is blind."
              : p.detail,
          });
        }
      }
    } catch {
      found.push({
        sev: "amber",
        title: "Integration health unavailable",
        detail: "Couldn't reach /health/integrations — broker/provider state is unknown.",
      });
    }

    // ── Execution health from the OMS ──────────────────────────────
    try {
      const { orders } = await api.omsOrders(undefined, 500);
      const now = Date.now();
      const within72h = (iso: string) =>
        iso && now - new Date(iso).getTime() < 72 * 3600 * 1000;

      const stuckSubmitted = orders.filter(
        (o) => o.state === "SUBMITTED" && o.placedBy === "STRATEGY_AUTO",
      );
      if (stuckSubmitted.length > 0) {
        found.push({
          sev: "red",
          title: `${stuckSubmitted.length} strategy orders stuck SUBMITTED`,
          detail:
            "Accepted by the broker but never filled — typically placed after the market close, "
            + "or the fill poller isn't reconciling them. Check on the next open.",
        });
      }

      const recentRejects = orders.filter(
        (o) => o.state === "REJECTED" && within72h(o.createdAtUtc),
      );
      if (recentRejects.length > 0) {
        const cashRejects = recentRejects.filter((o) =>
          (o.cancelledReason || "").includes("insufficient-free"),
        ).length;
        found.push({
          sev: "red",
          title: `${recentRejects.length} order rejections in last 72h`,
          detail: cashRejects > 0
            ? `${cashRejects} were broker out-of-buying-power. The strategy is firing signals that can't be funded.`
            : "Strategy orders are being rejected at the broker — see the /oms rejection histogram.",
        });
      }

      // No real strategy fill in the last 72h while signals are firing.
      const recentStrategyFill = orders.some(
        (o) => o.state === "FILLED" && o.placedBy === "STRATEGY_AUTO"
          && o.strategyId !== "reconcile_from_broker" && within72h(o.lastStateChangeAtUtc),
      );
      const recentStrategyActivity = orders.some(
        (o) => o.placedBy === "STRATEGY_AUTO" && within72h(o.createdAtUtc),
      );
      if (recentStrategyActivity && !recentStrategyFill) {
        found.push({
          sev: "amber",
          title: "No strategy fills in 72h despite activity",
          detail: "Strategies placed orders but none filled — usually the buying-power or market-closed caveat above.",
        });
      }
    } catch {
      /* OMS unreachable — the provider caveat above already covers connectivity */
    }

    setCaveats(found);
  }, []);

  useEffect(() => {
    void compute();
    const t = setInterval(compute, 60_000);
    return () => clearInterval(t);
  }, [compute]);

  if (caveats.length === 0) return null;

  const hasRed = caveats.some((c) => c.sev === "red");
  const tone = hasRed ? RED : AMBER;
  const reds = caveats.filter((c) => c.sev === "red").length;
  const ambers = caveats.length - reds;

  return (
    <div
      style={{
        border: `1px solid ${tone}`,
        borderLeft: `4px solid ${tone}`,
        borderRadius: 8,
        background: hasRed ? "rgba(239,68,68,0.07)" : "rgba(245,158,11,0.07)",
        padding: "10px 14px",
        marginBottom: 12,
      }}
    >
      <div
        style={{ display: "flex", alignItems: "center", gap: 8, cursor: "pointer" }}
        onClick={() => setOpen((v) => !v)}
      >
        <span style={{ fontSize: 14 }}>{hasRed ? "🔴" : "🟡"}</span>
        <strong style={{ fontSize: 13, color: tone }}>
          {caveats.length} system caveat{caveats.length === 1 ? "" : "s"}
        </strong>
        <span style={{ fontSize: 11, color: "var(--text-muted)" }}>
          {reds > 0 && `${reds} blocking`}{reds > 0 && ambers > 0 && " · "}
          {ambers > 0 && `${ambers} degraded`}
          {" — signals/fills may be unreliable until cleared"}
        </span>
        <span style={{ marginLeft: "auto", fontSize: 11, color: "var(--text-muted)" }}>
          {open ? "▾ hide" : "▸ show"}
        </span>
      </div>
      {open && (
        <ul style={{ margin: "8px 0 2px", padding: 0, listStyle: "none", display: "flex", flexDirection: "column", gap: 6 }}>
          {caveats.map((c, i) => (
            <li key={i} style={{ display: "flex", gap: 8, fontSize: 12, lineHeight: 1.45 }}>
              <span style={{ color: c.sev === "red" ? RED : AMBER, fontWeight: 700, flexShrink: 0 }}>
                {c.sev === "red" ? "●" : "▲"}
              </span>
              <span>
                <strong style={{ color: "var(--text)" }}>{c.title}.</strong>{" "}
                <span style={{ color: "var(--text-dim)" }}>{c.detail}</span>
              </span>
            </li>
          ))}
        </ul>
      )}
      <div style={{ fontSize: 10, color: "var(--text-muted)", marginTop: 4 }}>
        Re-derived from broker cash + OMS state every 60s (not the API's
        connectivity-only verdict). <Link to="/health" style={{ color: "var(--text-muted)" }}>Full health →</Link>
      </div>
    </div>
  );
}

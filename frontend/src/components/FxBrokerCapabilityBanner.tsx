/**
 * FxBrokerCapabilityBanner — prominent amber banner on FX strategy
 * surfaces explaining why FX orders ride on PAPER instead of routing
 * to T212, even when CFD is enabled on the user's T212 demo account.
 *
 * Why prominent + reusable:
 *   The trader's natural expectation is "I enabled CFD on T212 demo,
 *   so FX orders should route there." That conflates two different
 *   T212 surfaces:
 *
 *     • T212 *account* (web + mobile app) — has Invest + ISA + CFD
 *       products. CFD covers FX/indices/commodities.
 *     • T212 *public API* — covers ONLY the Invest product. There is
 *       no public REST endpoint for CFD or FX. Account-level CFD
 *       toggle has no effect on what TradePro's Trading212DemoClient
 *       can reach.
 *
 *   The OmsOrders page already shows tiny "T212 ✗ FX" + SIMULATED
 *   chips, but tooltips are easy to miss during a demo. This banner
 *   makes the limitation impossible to miss on every FX surface.
 *
 * Used on:
 *   • /strategies/ichimoku-fx — primary FX strategy page
 *   • Anywhere else that surfaces an FX-routed flow (add as needed)
 */
export function FxBrokerCapabilityBanner() {
  return (
    <div
      role="status"
      aria-label="FX broker routing limitation"
      style={{
        display: "flex",
        gap: 12,
        alignItems: "flex-start",
        padding: "12px 16px",
        borderRadius: 10,
        border: "1px solid rgba(245,158,11,0.35)",
        background: "rgba(245,158,11,0.08)",
        color: "var(--text)",
      }}
    >
      <div
        aria-hidden
        style={{
          fontSize: 18,
          lineHeight: "20px",
          flexShrink: 0,
          marginTop: 1,
        }}
      >
        ⚠
      </div>
      <div style={{ display: "flex", flexDirection: "column", gap: 4, flex: 1 }}>
        <div style={{ fontSize: 13, fontWeight: 700, color: "#f59e0b" }}>
          FX orders run on PAPER — not T212, even if CFD is enabled on
          your account
        </div>
        <div style={{ fontSize: 12, color: "var(--text-dim)", lineHeight: 1.5 }}>
          Trading&nbsp;212 splits into two surfaces:
          {" "}
          <strong>Invest</strong> (equities + ETFs) and
          {" "}
          <strong>CFD</strong> (FX, indices, commodities). Their
          {" "}
          <em>public API only covers Invest</em> — there are no public
          REST endpoints for CFD/FX. Enabling CFD on the T212 web/app
          unlocks <em>manual</em> trading there, but TradePro cannot
          route FX orders through the API regardless.
        </div>
        <div style={{ fontSize: 12, color: "var(--text-dim)", lineHeight: 1.5 }}>
          So every Ichimoku-FX signal becomes a
          {" "}
          <span style={{
            display: "inline-block",
            padding: "1px 6px",
            borderRadius: 999,
            background: "rgba(168,85,247,0.14)",
            color: "#a855f7",
            fontWeight: 700,
            fontSize: 10,
            letterSpacing: "0.04em",
          }}>SIMULATED</span>
          {" "}
          fill on the local PAPER broker — visible in the cockpit + P&L
          tracking, but never touches a real (or demo) account. For real
          FX routing we need an IBKR adapter (their API does cover FX) —
          that's on the roadmap, not in this build.
        </div>
      </div>
    </div>
  );
}

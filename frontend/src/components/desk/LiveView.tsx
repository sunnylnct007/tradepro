/**
 * LIVE — the real-money account, and nothing else.
 *
 * Owner, 9 Nov 2026, two decisions that define this screen:
 *
 *   "I am not going to do algo trading to live from this platform and will
 *    only want algo execution to paper trading"
 *   "but i do like to see my live position and have alert/analysis there"
 *
 * So the live account is a READ. TradePro analyses it, mails about it, and
 * never places an order into it. That is now a property of the product rather
 * than a description of how it happens to be configured today.
 *
 * WHY THIS IS A SEPARATE SCREEN AT ALL. The cockpit mixed paper and live with
 * equal weight — paper NLV, paper sleeves, paper reconciliation banners — on
 * the page the owner opened to make real-money decisions. Three quarters of it
 * was a simulator reporting on itself. His words looking at it: "dashboard
 * screen like show me nothing". Separating by ACCOUNT is the fix, because the
 * question "is this my money" is the one the reader needs answered first and
 * no amount of labelling inside a shared table answers it.
 *
 * NOTHING PAPER RENDERS HERE. Not sleeve P&L, not the orphan-leg
 * reconciliation, not strategy health. Those are real and they belong in the
 * paper view; on this screen they would be noise wearing the colour of risk.
 */
import { LiveBookCard } from "./LiveBookCard";

export function LiveView() {
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
      <div style={{
        border: "1px solid var(--border)", borderRadius: 8,
        padding: "10px 12px", background: "rgba(90,170,255,0.05)",
      }}>
        <div style={{ fontSize: 12.5, fontWeight: 700, color: "var(--text)" }}>
          Live account — read-only
        </div>
        <div style={{ fontSize: 11.5, color: "var(--text-dim)", marginTop: 3,
                      lineHeight: 1.5 }}>
          Your real money, read through IBKR Flex, which is read-only by
          construction. TradePro analyses this account and emails about it.
          It cannot place an order into it, by design — algo execution runs on
          paper only. Everything below is this account; nothing from the paper
          desk appears on this screen.
        </div>
      </div>

      <LiveBookCard />
    </div>
  );
}

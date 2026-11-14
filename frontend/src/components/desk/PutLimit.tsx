/**
 * May NEW short puts be sold? One answer, read by every screen that suggests
 * selling a put.
 *
 * Owner's limits (build pack, 13 Nov 2026): total put strike value at most 50%
 * of net liquidation, 20% per sector, 10% per name. The live-risk job checks
 * the LIVE book against them and publishes `put_limits` in the live-book-risk
 * artifact. That day the book was far over (about $460k of strike value), so
 * "No new puts until back within limits".
 *
 * FAILS CLOSED: no artifact, no `put_limits`, or data older than 36 hours all
 * read as "not allowed", with the reason stated. A put list must never look
 * actionable because the check could not run.
 */
import { useEffect, useState } from "react";

import { api } from "../../api/client";

export type PutLimit = { allowed: boolean; detail: string; ageHours: number | null; loading: boolean };

export function usePutLimit(): PutLimit {
  const [state, setState] = useState<PutLimit>({ allowed: false, detail: "checking the put limit…", ageHours: null, loading: true });
  useEffect(() => {
    let alive = true;
    api.todaySetupsArtifact("live-book-risk")
      .then((r) => {
        if (!alive) return;
        const a: any = r?.artifact ?? {};
        const lim = a.put_limits;
        const asOf = a.as_of_utc ?? r?.asOfUtc;
        const ageHours = asOf ? (Date.now() - new Date(asOf).getTime()) / 3600_000 : null;
        if (!lim) {
          setState({ allowed: false, loading: false, ageHours,
            detail: "the live book has not been checked against your put limits yet — treat as no new puts" });
        } else if (ageHours == null || ageHours > 36) {
          setState({ allowed: false, loading: false, ageHours,
            detail: `the last limit check is ${ageHours == null ? "of unknown age" : `${Math.round(ageHours)} h old`} — treat as no new puts until it runs again` });
        } else {
          setState({ allowed: !!lim.new_puts_allowed, loading: false, ageHours, detail: String(lim.detail || "") });
        }
      })
      .catch(() => alive && setState({ allowed: false, loading: false, ageHours: null,
        detail: "the live book could not be read — treat as no new puts" }));
    return () => { alive = false; };
  }, []);
  return state;
}

export function PutLimitBanner({ limit }: { limit: PutLimit }) {
  if (limit.loading || limit.allowed) return null;
  return (
    <div style={{ margin: "8px 0", padding: "8px 10px", borderRadius: 6, fontSize: 12.5, lineHeight: 1.5,
      border: "1px solid rgba(248,81,73,0.45)", background: "rgba(248,81,73,0.08)" }}>
      <b style={{ color: "#f85149" }}>No new puts.</b> {limit.detail}
      {limit.ageHours != null && <span style={{ color: "var(--text-muted)" }}> · checked {Math.round(limit.ageHours)} h ago</span>}
    </div>
  );
}

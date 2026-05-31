/** Shared time formatting for cockpit feeds/tables. */

/** Time-of-day for today's events; "DD Mon HH:MM · Nh ago" for older ones,
 * so a stale event from a previous session can't masquerade as fresh. */
export function fmtWhen(iso: string): string {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso.slice(11, 19);
  const now = new Date();
  if (d.toDateString() === now.toDateString()) return iso.slice(11, 19);
  const ageH = Math.round((now.getTime() - d.getTime()) / 3_600_000);
  const dm = d.toLocaleDateString(undefined, { day: "2-digit", month: "short" });
  return `${dm} ${iso.slice(11, 16)} · ${ageH}h ago`;
}

/**
 * DeskHealthBanner — is the desk actually working, on every page.
 *
 * Owner, 20 Sep 2026: "this is so frustrating. we shd be highlighting that on
 * our dashboard if we are not able to action certian things so we can fix it.
 * observability and diagnostic is key."
 *
 * tradepro-desk-check already computed the right answer and could only print it
 * to a terminal or mail it. The first time anyone ran it by hand it reported
 * that the swing sleeve had opened 12 positions and closed NONE — 47 exit
 * attempts, zero reaching the broker — and nothing on any screen said so.
 *
 * DESIGN RULES, each one a mistake this desk has already paid for:
 *
 *  - SILENT WHEN HEALTHY. A banner that is always there stops being read. It
 *    renders nothing at all when every lane is OK.
 *  - AN ABSENT CHECK IS NOT A PASS. If no verdict has ever been published, or
 *    the last one is stale, it SAYS SO rather than rendering nothing —
 *    "no news" and "all clear" must never look the same.
 *  - EVERY LINE CARRIES ITS NUMBER. "Execution degraded" is a mood; "12 filled,
 *    exits 0/47" is a fact someone can act on.
 */
import { useEffect, useState } from "react";

type Check = { lane: string; status: string; detail: string; fix?: string };

const BROKEN = "BROKEN", WARN = "WARN", UNKNOWN = "UNKNOWN";

/** Older than this and the verdict describes a desk that no longer exists. */
const STALE_HOURS = 30;

export function DeskHealthBanner() {
  const [data, setData] = useState<any | null>(null);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    (async () => {
      try {
        const r = await fetch("/api/desk-check/latest");
        const j = await r.json();
        if (alive) setData(j);
      } catch (e: any) {
        // A failed fetch is itself a thing worth saying — otherwise the banner
        // is indistinguishable from a healthy desk.
        if (alive) setErr(String(e?.message ?? e).slice(0, 120));
      }
    })();
    return () => { alive = false; };
  }, []);

  if (err) {
    return (
      <Bar tone="warn">
        Desk health unknown — could not read the check ({err}). This is not
        "all clear"; it is no answer.
      </Bar>
    );
  }
  if (!data) return null;                       // still loading — say nothing

  if (data.present === false) {
    return (
      <Bar tone="warn">
        <b>No desk check has ever been published.</b> Nothing is asserting that
        the boards, data and execution lanes work. Run{" "}
        <code>tradepro-desk-check --push</code> or wait for the 21:45 job.
      </Bar>
    );
  }

  const art = data.artifact ?? {};
  const checks: Check[] = art.checks ?? [];
  const broken = checks.filter((c) => c.status === BROKEN || c.status === UNKNOWN);
  const warns = checks.filter((c) => c.status === WARN);

  const ageH = data.asOfUtc
    ? (Date.now() - Date.parse(data.asOfUtc)) / 36e5
    : null;
  const stale = ageH != null && ageH > STALE_HOURS;

  if (stale) {
    return (
      <Bar tone="warn">
        <b>Desk check is {ageH!.toFixed(0)}h old</b> — it last said "{art.verdict}",
        but that describes {ageH! > 48 ? "a desk from days ago" : "yesterday's desk"}.
        Treat it as unknown, not as passing.
      </Bar>
    );
  }

  // HEALTHY: render nothing. A permanent green bar is wallpaper.
  if (broken.length === 0 && warns.length === 0) return null;

  const tone = broken.length ? "bad" : "warn";
  return (
    <Bar tone={tone}>
      <div style={{ fontWeight: 700, marginBottom: 4 }}>
        {broken.length
          ? `${broken.length} lane${broken.length > 1 ? "s" : ""} BROKEN — do not trade from these boards until fixed`
          : `${warns.length} lane${warns.length > 1 ? "s" : ""} degraded`}
        <span style={{ fontWeight: 400, opacity: 0.75, marginLeft: 8, fontSize: 12 }}>
          checked {ageH == null ? "?" : ageH < 1 ? "just now" : `${ageH.toFixed(0)}h ago`}
        </span>
      </div>
      {[...broken, ...warns].slice(0, 6).map((c, i) => (
        <div key={i} style={{ fontSize: 12, lineHeight: 1.5 }}>
          <b>{c.lane}</b> — {c.detail}
          {c.fix && <span style={{ opacity: 0.8 }}> · {c.fix}</span>}
        </div>
      ))}
      {broken.length + warns.length > 6 && (
        <div style={{ fontSize: 12, opacity: 0.8 }}>
          …and {broken.length + warns.length - 6} more.
        </div>
      )}
    </Bar>
  );
}

function Bar({ tone, children }: { tone: "bad" | "warn"; children: React.ReactNode }) {
  const accent = tone === "bad" ? "var(--bad, #d64545)" : "var(--warn, #b8860b)";
  return (
    <div style={{
      border: `1px solid ${accent}`, borderLeft: `5px solid ${accent}`,
      borderRadius: 8, padding: "9px 12px", marginBottom: 10,
      background: "color-mix(in srgb, " + accent + " 8%, transparent)",
    }}>
      {children}
    </div>
  );
}

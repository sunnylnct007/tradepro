/**
 * ResearchView — the evidence behind every strategy claim.
 *
 * Owner, 22 Aug 2026: "we need the backtesting run to be enabled on UI with
 * details shown what we have done how we have derived these etc."
 *
 * Correct, and overdue. Every strategy conclusion this month — wheel v3
 * don't-fund, S/R zero edge, the Ichimoku exit findings, mean reversion —
 * existed only as gates files in git plus results in a chat window. That is
 * not auditable, and the owner should not have to take anyone's word for it.
 *
 * The protocol this renders, and why it matters more than the numbers:
 * gates are COMMITTED TO GIT BEFORE the run, with a prediction on record. The
 * commit sha is shown on every card so the claim is CHECKABLE rather than
 * trusted — `git show <sha>` proves the bar was set before the result was
 * known. Studies that FAILED are kept, not quietly dropped: four of these six
 * are negative results, and they are the ones that saved real money.
 */
import { useState } from "react";
import studies from "../../data/studies.json";

type Gate = { id: string; test: string; result: string; pass: boolean };
type Study = {
  id: string; title: string; date: string; gatesFile: string; gatesCommit: string;
  question: string; prediction: string; verdict: string; headline: string; why: string;
  gates: Gate[];
};

const TONE = { ok: "#1D9E75", warn: "#E6A817", bad: "#D85A30", dim: "var(--text-muted)" };

function verdictTone(v: string) {
  if (v.startsWith("PASS")) return TONE.ok;
  if (v.startsWith("PARTIAL")) return TONE.warn;
  return TONE.bad;
}

export function ResearchView() {
  const list = (studies as { studies: Study[] }).studies;
  const [open, setOpen] = useState<string | null>(list[list.length - 1]?.id ?? null);
  const passed = list.filter((s) => s.verdict.startsWith("PASS")).length;
  const failed = list.filter((s) => s.verdict.startsWith("FAIL")).length;

  return (
    <div style={{ padding: "8px 4px" }}>
      <h2 style={{ margin: "0 0 4px", fontSize: 18 }}>Research — pre-registered studies</h2>
      <div style={{ fontSize: 12, color: "var(--text-dim)", marginBottom: 4, maxWidth: 900, lineHeight: 1.6 }}>
        Every strategy claim, with the evidence. <b>Gates are committed to git BEFORE each run</b> with a
        prediction on record — the commit sha on each card makes that checkable rather than something you
        have to trust (<code>git show &lt;sha&gt;</code>). Failed studies are kept deliberately:
        they are usually the ones that saved money.
      </div>
      <div style={{ fontSize: 12, color: "var(--text-muted)", marginBottom: 14 }}>
        {list.length} studies · <span style={{ color: TONE.ok }}>{passed} pass</span> ·{" "}
        <span style={{ color: TONE.bad }}>{failed} fail</span> ·{" "}
        <span style={{ color: TONE.warn }}>{list.length - passed - failed} partial</span>
      </div>

      {[...list].reverse().map((s) => {
        const isOpen = open === s.id;
        const tone = verdictTone(s.verdict);
        return (
          <div key={s.id} style={{ border: "1px solid var(--border)", borderLeft: `3px solid ${tone}`,
                                   borderRadius: 8, marginBottom: 10, overflow: "hidden" }}>
            <button onClick={() => setOpen(isOpen ? null : s.id)}
              style={{ display: "flex", gap: 12, alignItems: "baseline", width: "100%", padding: "10px 14px",
                       background: "var(--surface-2)", border: "none", cursor: "pointer",
                       textAlign: "left", font: "inherit", color: "inherit", flexWrap: "wrap" }}>
              <span style={{ color: tone, fontSize: 11 }}>{isOpen ? "▾" : "▸"}</span>
              <b style={{ fontSize: 14 }}>{s.title}</b>
              <span style={{ fontSize: 11, color: "var(--text-muted)", fontFamily: "var(--font-mono)" }}>{s.date}</span>
              <span style={{ marginLeft: "auto", color: tone, fontWeight: 700, fontSize: 12 }}>{s.verdict}</span>
            </button>

            {isOpen && (
              <div style={{ padding: "12px 14px", fontSize: 12, lineHeight: 1.65 }}>
                <Row label="Question">{s.question}</Row>
                <Row label="Predicted">
                  <span style={{ color: "var(--text-dim)" }}>{s.prediction}</span>
                  <div style={{ fontSize: 11, color: "var(--text-muted)", marginTop: 2 }}>
                    Recorded before the run — a prediction that can be wrong is what makes the result informative.
                  </div>
                </Row>
                <Row label="Result"><b>{s.headline}</b></Row>
                <Row label="Reading">{s.why}</Row>

                <div style={{ margin: "10px 0 4px", fontWeight: 700, color: "var(--text-dim)" }}>Gates</div>
                <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 12 }}>
                  <tbody>
                    {/* `?? []` is not defensive padding — a study record with no
                        `gates` key took the ENTIRE /desk route down with
                        "Cannot read properties of undefined (reading 'map')"
                        on 23 Aug. A data file should never be able to crash a
                        route; a study missing its gates should render without
                        them and say so. */}
                    {(s.gates ?? []).map((g) => (
                      <tr key={g.id} style={{ borderTop: "1px solid #141b2b" }}>
                        <td style={{ padding: "5px 8px", width: 44, fontFamily: "var(--font-mono)",
                                     color: g.pass ? TONE.ok : TONE.bad, fontWeight: 700 }}>
                          {g.pass ? "PASS" : "FAIL"}
                        </td>
                        <td style={{ padding: "5px 8px", width: 46, fontFamily: "var(--font-mono)" }}>{g.id}</td>
                        <td style={{ padding: "5px 8px", color: "var(--text-dim)" }}>{g.test}</td>
                        <td style={{ padding: "5px 8px", fontFamily: "var(--font-mono)",
                                     color: g.pass ? "var(--text)" : TONE.bad }}>{g.result}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
                {!s.gates?.length && (
                  <div style={{ fontSize: 11, color: "var(--text-muted)", padding: "4px 0" }}>
                    Gate-by-gate results are not recorded for this study — see its gates file.
                  </div>
                )}

                <div style={{ marginTop: 10, fontSize: 11, color: "var(--text-muted)" }}>
                  Gates: <code>strategies/{s.gatesFile}</code> · committed{" "}
                  <code style={{ color: "var(--text-dim)" }}>{s.gatesCommit}</code> before the run
                </div>
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}

function Row({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div style={{ display: "grid", gridTemplateColumns: "90px 1fr", gap: 10, marginBottom: 7 }}>
      <div style={{ color: "var(--text-muted)", fontSize: 11, textTransform: "uppercase",
                    letterSpacing: "0.04em" }}>{label}</div>
      <div>{children}</div>
    </div>
  );
}

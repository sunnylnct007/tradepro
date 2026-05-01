"""Answer verification — the accuracy backbone for financial Q&A.

Given a draft answer + the tool outputs the LLM claims to have used,
this module:
  1. Extracts every numeric/factual claim from the answer (LLM-assisted
     extraction so we catch '24%' vs 'twenty-four percent' vs 'a fifth').
  2. For each claim, asks an LLM judge to verify it against the tool
     outputs.
  3. Returns a per-claim verdict (supported / contradicted / unsupported)
     so the client can either flag uncertainties to the user or
     auto-rewrite the answer.

Why this is required for our use-case (per project_goal): financial
decisions amplify hallucinated numbers. A "+8% upside" that doesn't
trace to a tool output is dangerous. The verifier is a non-negotiable
brake — like the rule-based decision being non-negotiable for the
verdict.

Design choice: verification uses the same LlmProvider as the rest of
the system. NoOpProvider returns "verifier_unavailable" for every
claim so the client surfaces the warning rather than silently
accepting the draft.
"""
from __future__ import annotations

import json
from dataclasses import dataclass

from ..llm import LlmProvider, get_provider


@dataclass
class ClaimVerdict:
    claim: str
    status: str          # supported | contradicted | unsupported | error
    citation: str | None
    evidence: str | None
    confidence: float | None


def _extraction_prompt(answer: str) -> str:
    return f"""Extract every quantitative or definitive factual claim from this answer.

Answer:
\"\"\"{answer}\"\"\"

Output a JSON object: {{ "claims": [list of strings] }}.
Each string is one atomic claim ("QQQ Sharpe is 0.94", "VOO lost 34% in COVID").
Skip opinions, hedges ("might", "appears"), and pure prose.
"""


def _verification_prompt(claim: str, evidence: str) -> str:
    return f"""You are a factual-claim verifier for financial-decision
software. Given a single claim and the JSON tool outputs that should
support it, decide:

- "supported"    — the claim is exactly stated in the JSON.
- "contradicted" — the JSON contains the same number/fact but DIFFERENT.
- "unsupported"  — the claim is not in the JSON at all (the most
                   dangerous case — hallucinated number).

Be strict. A claim "Sharpe 0.94" against JSON containing "sharpe: 0.93"
is contradicted, not supported. A claim "QQQ is up" with no QQQ
performance number in the JSON is unsupported.

Claim: {claim}

Tool outputs (JSON):
{evidence[:8000]}

Output JSON:
{{
  "status": "supported" | "contradicted" | "unsupported",
  "citation": <path within the JSON if supported, e.g. "envelope.payload.rows[0].stats.sharpe">,
  "evidence": <the exact value found, or null>,
  "confidence": <0.0 to 1.0>
}}
"""


def extract_claims(answer: str, provider: LlmProvider | None = None) -> list[str]:
    """LLM-assisted extraction. Returns [] if extraction fails — the
    caller can either skip verification (and warn) or fall back to
    treating the whole answer as one claim."""
    p = provider or get_provider()
    if not p.healthy():
        return []
    result = p.complete_json(_extraction_prompt(answer), max_tokens=400)
    if not result.ok:
        return []
    raw = result.data.get("claims") or []
    if not isinstance(raw, list):
        return []
    return [str(c)[:280] for c in raw if c]


def verify_claim(
    claim: str,
    evidence_json: str,
    provider: LlmProvider | None = None,
) -> ClaimVerdict:
    p = provider or get_provider()
    if not p.healthy():
        return ClaimVerdict(claim=claim, status="error",
                            citation=None, evidence=None, confidence=None)
    result = p.complete_json(_verification_prompt(claim, evidence_json), max_tokens=200)
    if not result.ok:
        return ClaimVerdict(claim=claim, status="error",
                            citation=None, evidence=None, confidence=None)
    d = result.data
    status = str(d.get("status", "unsupported"))
    if status not in ("supported", "contradicted", "unsupported"):
        status = "unsupported"
    return ClaimVerdict(
        claim=claim,
        status=status,
        citation=d.get("citation"),
        evidence=str(d.get("evidence"))[:200] if d.get("evidence") is not None else None,
        confidence=_coerce_float(d.get("confidence")),
    )


def verify_answer(
    answer: str,
    tool_outputs: list[dict] | dict | str,
    provider: LlmProvider | None = None,
) -> dict:
    """Top-level verification. Returns a dict suitable for an MCP
    tool response — a per-claim verdict list plus a coarse 'is the
    answer trustworthy' summary."""
    p = provider or get_provider()
    if not p.healthy():
        return {
            "_source": "live://verify",
            "ok": False,
            "error": "verifier unavailable — LLM provider is down",
            "claims_checked": 0,
            "verdicts": [],
        }

    if isinstance(tool_outputs, str):
        evidence_json = tool_outputs
    else:
        try:
            evidence_json = json.dumps(tool_outputs, default=str)
        except Exception:  # noqa: BLE001
            return {
                "_source": "live://verify",
                "ok": False,
                "error": "tool_outputs not serialisable",
            }

    claims = extract_claims(answer, p)
    if not claims:
        return {
            "_source": "live://verify",
            "ok": True,
            "warning": "no atomic claims extracted — verifier could not assess the answer",
            "claims_checked": 0,
            "verdicts": [],
            "trustworthy": False,
        }

    verdicts = [verify_claim(c, evidence_json, p) for c in claims]
    supported = sum(1 for v in verdicts if v.status == "supported")
    contradicted = sum(1 for v in verdicts if v.status == "contradicted")
    unsupported = sum(1 for v in verdicts if v.status == "unsupported")
    errored = sum(1 for v in verdicts if v.status == "error")
    trustworthy = (
        contradicted == 0
        and unsupported == 0
        and errored == 0
        and supported > 0
    )
    # Fail-closed: any contradiction or unsupported claim means the
    # caller MUST NOT deliver the draft as-is. The verifier itself
    # never "accepts" — it only reports. The MCP prompt and the chat
    # client are responsible for honouring should_refuse.
    should_refuse = not trustworthy
    refusal_reasons: list[str] = []
    for vv in verdicts:
        if vv.status == "contradicted":
            refusal_reasons.append(
                f"contradicted: '{vv.claim}' (evidence said {vv.evidence!r})"
            )
        elif vv.status == "unsupported":
            refusal_reasons.append(f"unsupported: '{vv.claim}'")
        elif vv.status == "error":
            refusal_reasons.append(f"verifier error on: '{vv.claim}'")
    return {
        "_source": "live://verify",
        "ok": True,
        "claims_checked": len(verdicts),
        "supported": supported,
        "contradicted": contradicted,
        "unsupported": unsupported,
        "errored": errored,
        "trustworthy": trustworthy,
        # Explicit refusal flag + the human-readable reason, so the
        # caller can render "I can't answer that confidently because…"
        "should_refuse": should_refuse,
        "refusal_reasons": refusal_reasons,
        "verdicts": [v.__dict__ for v in verdicts],
    }


def _coerce_float(x) -> float | None:
    try:
        return float(x)
    except (TypeError, ValueError):
        return None

"""Per-Q&A trace artefact.

Every MCP-driven answer leaves a JSON trace at
    ~/.tradepro/traces/<trace_id>.json

containing:
  - the user's question
  - the decomposition (sub-questions)
  - every tool call (name, args, output, latency, status)
  - every LLM call (prompt hash, model, raw response, latency)
  - the draft answer
  - the verification verdicts (per claim)
  - the final outcome (delivered / refused) with reason

This is the "no hallucination accepted, fully visible" contract: a
user can pull up any answer's trace and see the chain of reasoning,
including which tool returned which value at which path.
"""
from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

TRACE_ROOT = Path.home() / ".tradepro" / "traces"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _prompt_hash(text: str) -> str:
    return hashlib.sha1(text.encode()).hexdigest()[:12]


@dataclass
class TraceStep:
    ts: str
    kind: str            # "decompose" | "tool_call" | "llm_call" | "draft" | "verify" | "final"
    name: str
    inputs: Any = None
    outputs: Any = None
    error: str | None = None
    latency_ms: int | None = None


@dataclass
class AnswerTrace:
    trace_id: str
    question: str
    started_at: str
    steps: list[TraceStep] = field(default_factory=list)
    decomposition: list[str] | None = None
    draft_answer: str | None = None
    verification: dict | None = None
    outcome: str | None = None       # "delivered" | "refused" | "errored"
    refusal_reasons: list[str] = field(default_factory=list)

    def step(self, kind: str, name: str, **fields: Any) -> None:
        self.steps.append(TraceStep(ts=_now_iso(), kind=kind, name=name, **fields))

    def to_dict(self) -> dict:
        return {
            "trace_id": self.trace_id,
            "question": self.question,
            "started_at": self.started_at,
            "ended_at": _now_iso(),
            "decomposition": self.decomposition,
            "draft_answer": self.draft_answer,
            "verification": self.verification,
            "outcome": self.outcome,
            "refusal_reasons": list(self.refusal_reasons),
            "steps": [s.__dict__ for s in self.steps],
        }

    def save(self) -> Path:
        TRACE_ROOT.mkdir(parents=True, exist_ok=True)
        path = TRACE_ROOT / f"{self.trace_id}.json"
        path.write_text(json.dumps(self.to_dict(), indent=2, default=str))
        return path


def new_trace(question: str) -> AnswerTrace:
    return AnswerTrace(
        trace_id=str(uuid.uuid4()),
        question=question,
        started_at=_now_iso(),
    )


# Helpers for tools that want to log their own activity to a trace —
# kept tiny so adoption is friction-free.

def record_tool_call(
    trace: AnswerTrace | None,
    name: str,
    inputs: Any,
    outputs: Any,
    latency_ms: int | None = None,
    error: str | None = None,
) -> None:
    if trace is None:
        return
    trace.step(
        kind="tool_call",
        name=name,
        inputs=inputs,
        outputs=outputs,
        latency_ms=latency_ms,
        error=error,
    )


def record_llm_call(
    trace: AnswerTrace | None,
    purpose: str,
    prompt: str,
    raw_response: str,
    model: str | None = None,
    latency_ms: int | None = None,
    error: str | None = None,
) -> None:
    if trace is None:
        return
    trace.step(
        kind="llm_call",
        name=purpose,
        inputs={"prompt_hash": _prompt_hash(prompt), "model": model},
        outputs={"raw_response": raw_response[:1200]},
        latency_ms=latency_ms,
        error=error,
    )

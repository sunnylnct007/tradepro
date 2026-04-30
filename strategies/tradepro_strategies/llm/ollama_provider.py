"""Ollama HTTP provider.

Talks to the local Ollama daemon (default localhost:11434). Free,
private, runs on M-series via MPS. Default for high-frequency,
low-stakes tasks like per-headline sentiment scoring.

Override the model with TRADEPRO_OLLAMA_MODEL env var; default is
llama3.1:8b (fast, reliable for short structured tasks).
"""
from __future__ import annotations

import json
import os
import time
from typing import Any

import requests

from .provider import LlmProvider, LlmResult


class OllamaProvider(LlmProvider):
    DEFAULT_HOST = "http://localhost:11434"
    DEFAULT_MODEL = "llama3.1:8b"

    def __init__(self, host: str | None = None, model: str | None = None,
                 timeout: float = 30.0):
        self._host = (host or os.environ.get("TRADEPRO_OLLAMA_HOST")
                      or self.DEFAULT_HOST).rstrip("/")
        self._model = (model or os.environ.get("TRADEPRO_OLLAMA_MODEL")
                       or self.DEFAULT_MODEL)
        self._timeout = timeout

    @property
    def name(self) -> str: return "ollama"

    @property
    def model(self) -> str: return self._model

    def healthy(self) -> bool:
        try:
            r = requests.get(f"{self._host}/api/tags", timeout=2)
            return r.status_code == 200
        except requests.RequestException:
            return False

    def complete_json(self, prompt: str, *, schema_hint: dict | None = None,
                      max_tokens: int = 256, temperature: float = 0.0) -> LlmResult:
        # Two-stage prompt: schema first, then the task. Ollama's `format=json`
        # parameter constrains the output to valid JSON server-side, but
        # we still parse defensively in case the field is absent.
        full_prompt = prompt
        if schema_hint is not None:
            full_prompt = (
                f"Respond with a JSON object matching this schema:\n"
                f"{json.dumps(schema_hint, indent=2)}\n\n"
                f"{prompt}"
            )

        body = {
            "model": self._model,
            "prompt": full_prompt,
            "format": "json",
            "stream": False,
            "options": {
                "temperature": temperature,
                "num_predict": max_tokens,
            },
        }

        t0 = time.time()
        try:
            resp = requests.post(
                f"{self._host}/api/generate",
                json=body,
                timeout=self._timeout,
            )
        except requests.RequestException as e:
            return LlmResult.fail(f"network: {e}")
        latency_ms = int((time.time() - t0) * 1000)

        if resp.status_code != 200:
            return LlmResult.fail(
                f"http {resp.status_code}: {resp.text[:200]}",
                raw=resp.text[:500],
            )

        try:
            outer = resp.json()
        except ValueError:
            return LlmResult.fail("ollama returned non-JSON envelope", raw=resp.text[:500])

        raw_text = outer.get("response", "").strip()
        if not raw_text:
            return LlmResult.fail("empty response", raw=str(outer))

        # When format=json is requested, Ollama returns a JSON string in
        # `response` — parse it. If the model misbehaves and returns
        # text, we still try a best-effort substring extraction.
        try:
            data: Any = json.loads(raw_text)
        except json.JSONDecodeError:
            data = _extract_first_json(raw_text)
            if data is None:
                return LlmResult.fail("response wasn't valid JSON", raw=raw_text)

        if not isinstance(data, dict):
            return LlmResult.fail(f"expected JSON object, got {type(data).__name__}",
                                  raw=raw_text)

        return LlmResult(
            ok=True,
            data=data,
            raw=raw_text,
            latency_ms=latency_ms,
            model=self._model,
        )


def _extract_first_json(text: str) -> dict | None:
    """Best-effort: find the first balanced {...} substring in arbitrary
    text. Models occasionally wrap JSON in prose like 'Here is the
    answer: {...}'."""
    depth = 0
    start = -1
    for i, c in enumerate(text):
        if c == "{":
            if depth == 0:
                start = i
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0 and start >= 0:
                try:
                    return json.loads(text[start : i + 1])
                except json.JSONDecodeError:
                    return None
    return None

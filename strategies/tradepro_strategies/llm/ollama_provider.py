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

    def available_models(self) -> list[str]:
        """The list of model names the local Ollama daemon reports.
        Empty list when the daemon is unreachable. Used by callers to
        diagnose 'model X not pulled' before paying for a long
        backtest run that would silently get empty completions."""
        try:
            r = requests.get(f"{self._host}/api/tags", timeout=2)
            r.raise_for_status()
            data = r.json() or {}
        except requests.RequestException:
            return []
        return [m.get("name") for m in (data.get("models") or []) if m.get("name")]

    def model_available(self) -> bool:
        """Strong health check: daemon up AND the configured model is
        present. Distinguishes 'Ollama not running' from 'model not
        pulled' — both are silent killers of sentiment scoring."""
        return self._model in self.available_models()

    def health_summary(self) -> dict:
        """Structured health report. Returns one of three states with
        an actionable message:
          daemon_down       — Ollama isn't running (start it)
          model_missing     — daemon ok, model not pulled (ollama pull X)
          ok                — daemon up + model present
        """
        if not self.healthy():
            return {
                "ok": False,
                "state": "daemon_down",
                "host": self._host,
                "model": self._model,
                "message": (
                    f"Ollama not reachable at {self._host}. "
                    f"Start it with `ollama serve` (it usually auto-starts)."
                ),
            }
        avail = self.available_models()
        if self._model not in avail:
            return {
                "ok": False,
                "state": "model_missing",
                "host": self._host,
                "model": self._model,
                "available_models": avail,
                "message": (
                    f"Ollama is running but model '{self._model}' is not pulled. "
                    f"Run: ollama pull {self._model}"
                ),
            }
        return {
            "ok": True,
            "state": "ok",
            "host": self._host,
            "model": self._model,
            "message": f"Ollama healthy with {self._model}",
        }

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
            # `think: false` disables the chain-of-thought reasoning
            # phase on models that have one (qwen3.x, deepseek-r1).
            # Ignored by models that don't think (llama3.x, gemma).
            # Cleaner + faster than letting the model emit a
            # <think>...</think> block and stripping it after the
            # fact — but we keep the strip helper as defense-in-depth
            # for forks that don't honour the parameter.
            "think": False,
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

        # Some models — qwen3.5, deepseek-r1, etc. — emit a
        # <think>...</think> reasoning block BEFORE the JSON answer.
        # Ollama's format=json doesn't suppress this on every model.
        # Strip the block so the JSON parser sees only the answer;
        # without this, sentiment scoring silently returns "empty
        # response" because json.loads chokes on the leading prose.
        cleaned = _strip_thinking_blocks(raw_text)

        # When format=json is requested, Ollama returns a JSON string in
        # `response` — parse it. If the model misbehaves and returns
        # text, we still try a best-effort substring extraction.
        try:
            data: Any = json.loads(cleaned)
        except json.JSONDecodeError:
            data = _extract_first_json(cleaned)
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


def _strip_thinking_blocks(text: str) -> str:
    """Remove leading <think>...</think> reasoning blocks emitted by
    chain-of-thought models (qwen3.5, deepseek-r1, etc.). Also
    handles the variants `<thinking>` and ```think``` fenced blocks
    seen on some forks. Conservative: only strips at the start so
    we don't accidentally drop content from a JSON payload that
    legitimately contains the substring '<think'."""
    import re
    # Strip a leading <think>...</think> (case-insensitive, multi-line).
    cleaned = re.sub(
        r"^\s*<think(?:ing)?>.*?</think(?:ing)?>\s*",
        "",
        text,
        count=1,
        flags=re.IGNORECASE | re.DOTALL,
    )
    # Some forks use markdown-fenced think blocks: ```think\n...\n```
    cleaned = re.sub(
        r"^\s*```(?:think|thinking|reasoning)\s*\n.*?\n```\s*",
        "",
        cleaned,
        count=1,
        flags=re.IGNORECASE | re.DOTALL,
    )
    return cleaned.strip()


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

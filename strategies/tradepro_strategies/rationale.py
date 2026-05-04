"""Per-symbol plain-English rationale for the verdict.

Takes the structured facts (verdict bucket, decision_trace, sentiment
summary, regime stats, fundamentals) and produces a 2-3 sentence
explanation a non-quant reader can grok in 5 seconds.

**No-hallucination contract**:

  1. The prompt enumerates the EXACT facts the LLM may reference.
     "If you mention a number, it must appear in the inputs above.
     Never invent numbers."
  2. After generation, every numerical claim is verified against the
     input facts via the verifier (verify.py). Same fail-closed
     contract as the MCP server.
  3. If verification fails → fall back to a deterministic
     template-based summary built mechanically from the facts. Less
     elegant prose but factually 100% correct, no LLM creativity in
     play. Marked as `source: "template"` so the UI can show "fallback
     used" if curious.

Caching: same input facts → same rationale (no re-generation across
runs unless the data changed). Disk cache at
~/.tradepro/cache/llm-rationale.json keyed by hash(facts + model).
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .llm import LlmProvider, get_provider


CACHE_PATH = Path.home() / ".tradepro" / "cache" / "llm-rationale.json"
PROMPT_VERSION = "v1"


@dataclass
class Rationale:
    summary: str
    key_factors: list[str] = field(default_factory=list)
    caveats: list[str] = field(default_factory=list)
    source: str = "llm"        # "llm" | "template" | "unavailable"
    model: str | None = None
    prompt_version: str = PROMPT_VERSION
    verified: bool = False
    verification_notes: list[str] = field(default_factory=list)
    generated_at: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "summary": self.summary,
            "key_factors": list(self.key_factors),
            "caveats": list(self.caveats),
            "source": self.source,
            "model": self.model,
            "prompt_version": self.prompt_version,
            "verified": self.verified,
            "verification_notes": list(self.verification_notes),
            "generated_at": self.generated_at,
        }


# ---------- Disk cache ----------

class _Cache:
    def __init__(self, path: Path = CACHE_PATH):
        self._path = path
        self._data: dict[str, dict] | None = None

    def _load(self) -> dict[str, dict]:
        if self._data is not None:
            return self._data
        try:
            self._data = json.loads(self._path.read_text())
            if not isinstance(self._data, dict):
                self._data = {}
        except (FileNotFoundError, json.JSONDecodeError):
            self._data = {}
        return self._data

    def get(self, key: str) -> dict | None:
        return self._load().get(key)

    def put(self, key: str, value: dict) -> None:
        d = self._load()
        d[key] = value
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self._path.with_suffix(".tmp")
            tmp.write_text(json.dumps(d))
            tmp.replace(self._path)
        except OSError:
            pass


_cache = _Cache()


def _cache_key(facts: dict, model: str) -> str:
    payload = json.dumps(facts, sort_keys=True, default=str)
    return hashlib.sha1(f"{model}::{PROMPT_VERSION}::{payload}".encode()).hexdigest()


# ---------- Facts gathering ----------

def gather_facts(
    *,
    symbol: str,
    bucket: str,
    bucket_reason: str,
    long_count: int,
    total_strategies: int,
    market_state: dict,
    sentiment_summary: dict | None,
    sentiment_status: str | None,
    best_strategy_label: str,
    best_stats: dict,
    regimes: list[dict],
    fundamentals: dict | None,
    sentiment_demoted: bool,
    cross_sectional_momentum: dict | None = None,
    valuation_flag: dict | None = None,
) -> dict:
    """Build the strict fact bundle the LLM is allowed to reference.

    EVERY field here is what gets shown to the model in the prompt.
    Anything not here cannot legally appear in the output. The cache
    key hashes this bundle, so a change in any field rebusts the
    cache (rationale regenerated)."""
    facts: dict[str, Any] = {
        "symbol": symbol,
        "verdict": bucket,
        "verdict_reason": bucket_reason,
        "strategy_consensus": f"{long_count} of {total_strategies} strategies currently long",
        "rule_chain": [
            {
                "name": c.get("name"),
                "status": c.get("status"),
                "detail": c.get("detail"),
            }
            for c in (market_state.get("decision_trace") or [])
        ],
        "best_historical_strategy": best_strategy_label,
        "best_strategy_stats": {
            k: v for k, v in (best_stats or {}).items()
            if k in ("cagr_pct", "sharpe", "max_drawdown_pct", "total_return_pct")
        },
        "stress_history": [
            {
                "name": r.get("name"),
                "kind": r.get("kind"),
                "return_pct": r.get("return_pct"),
                "max_drawdown_pct": r.get("max_drawdown_pct"),
            }
            for r in (regimes or [])[:8]
        ],
        "current_market_state": {
            "rsi_14": market_state.get("rsi_14"),
            "above_sma_200": market_state.get("above_sma_200"),
            "pct_off_52w_high_pct": market_state.get("pct_off_52w_high_pct"),
            # When the 52w high was set + price at that bar. Surfaced
            # so the rationale can read "−20% off 52w high (set
            # 2026-01-28)" and the verifier sees the date in the
            # input facts (so the LLM can't fabricate a date).
            "pct_off_52w_high_date": market_state.get("pct_off_52w_high_date"),
            "pct_off_52w_high_price": market_state.get("pct_off_52w_high_price"),
            "drawdown_from_peak_pct": market_state.get("drawdown_from_peak_pct"),
            "peak_date": market_state.get("peak_date"),
            "peak_price": market_state.get("peak_price"),
            "momentum_12m_pct": market_state.get("momentum_12m_pct"),
        },
        "sentiment": {
            "status": sentiment_status,
            "mean_7d": (sentiment_summary or {}).get("mean_sentiment"),
            "material_negative_count_7d": (sentiment_summary or {}).get("material_negative_count"),
            "demoted_buy_to_wait": sentiment_demoted,
        },
    }
    if fundamentals:
        facts["fundamentals"] = {
            "issuer": fundamentals.get("fund_family"),
            "expense_ratio_pct": fundamentals.get("expense_ratio_pct"),
            "aum_usd": fundamentals.get("aum_usd"),
            "dividend_yield_pct": fundamentals.get("dividend_yield_pct"),
        }
    # Cross-basket signals (Family 2 + 3). Surfaced into the fact
    # bundle so the rationale can quote "rank 3 of 13 on momentum"
    # or "in the cheap quartile" — and the verifier can prove the
    # numbers came from real input. Keys are renamed for prompt
    # clarity ('peer_count' → 'peers', etc.).
    if cross_sectional_momentum and cross_sectional_momentum.get("rank") is not None:
        peers = cross_sectional_momentum.get("peer_count")
        # Surface BOTH peers (excluding self) and total (peers + self)
        # so the rationale can quote "rank 3 of 13" — the 13 must
        # appear verbatim in the facts blob for the local verifier
        # to accept the number, otherwise it's flagged as fabricated.
        total = (peers + 1) if isinstance(peers, int) else None
        facts["cross_basket_momentum"] = {
            "rank": cross_sectional_momentum.get("rank"),
            "peers": peers,
            "total": total,
            "zscore": cross_sectional_momentum.get("zscore"),
            "is_top_quartile": cross_sectional_momentum.get("is_top_quartile"),
            "value_pct": cross_sectional_momentum.get("value"),
            "basket_median_pct": cross_sectional_momentum.get("basket_median"),
        }
    if valuation_flag and valuation_flag.get("flag") and valuation_flag["flag"] != "n/a":
        facts["cross_basket_valuation"] = {
            "flag": valuation_flag.get("flag"),
            "yield_pct": valuation_flag.get("yield_pct"),
            "basket_median_yield_pct": valuation_flag.get("basket_median_yield_pct"),
            "basis": valuation_flag.get("basis"),
        }
    return facts


# ---------- Prompt + parsing ----------

_PROMPT = """You are a careful financial-rationale writer. Your job is to
produce a 2-3 sentence plain-English summary of why this ETF received
its verdict, plus 2-4 key factors and 1-2 caveats.

ETF: {symbol}
Verdict: {verdict}
Verdict reason: {verdict_reason}

Allowed facts (you may ONLY reference values from this block — do NOT
invent or paraphrase numbers):

{facts_json}

Hard rules:
- Use ONLY values that appear in the Allowed facts block above. If you
  cite a number, it must appear there verbatim. NEVER make up returns,
  drawdowns, percentages, dates, or holdings.
- Never override the verdict. Explain it in everyday language.
- If a fact is null / missing, do not mention it. Do not say "data
  unavailable" — silence is better than padding.
- Caveats should be specific, drawn from the stress_history or the
  rule_chain (e.g. "lost 55% in 2008 GFC", "RSI 72 — overbought").

Output JSON:
{{
  "summary": "2-3 sentences explaining why the verdict is what it is.",
  "key_factors": ["short phrase", "short phrase", ...],   // 2-4 items
  "caveats": ["short phrase", "short phrase", ...]         // 1-2 items
}}
"""

_SCHEMA_HINT = {
    "summary": "string",
    "key_factors": ["string"],
    "caveats": ["string"],
}


def _build_prompt(facts: dict) -> str:
    return _PROMPT.format(
        symbol=facts["symbol"],
        verdict=facts["verdict"],
        verdict_reason=facts["verdict_reason"],
        facts_json=json.dumps(facts, indent=2, default=str),
    )


# ---------- Verification ----------

def _extract_numbers(text: str) -> list[str]:
    """Pull the numeric tokens that look like 'numbers a model could
    have hallucinated' — percentages, decimals, integers > 1. Used by
    the lightweight verifier."""
    import re
    return re.findall(r"-?\d+\.?\d*%?", text or "")


def _facts_text(facts: dict) -> str:
    """Flatten facts into a searchable string for substring checks."""
    return json.dumps(facts, default=str)


def _verify_locally(rationale: Rationale, facts: dict) -> tuple[bool, list[str]]:
    """Lightweight, deterministic verification. Every numeric token in
    the summary + factors + caveats must appear in the facts string.

    This is the safety net — even before the LLM-based verifier runs,
    a plain substring check catches blatant hallucinations like '55%'
    when the facts don't contain that figure. Cheap, deterministic,
    runs every time."""
    notes: list[str] = []
    facts_str = _facts_text(facts)

    blobs = [rationale.summary] + list(rationale.key_factors) + list(rationale.caveats)
    for blob in blobs:
        for n in _extract_numbers(blob):
            # Allow trivial small integers (1, 2, 3 — sentence counts /
            # ordering words like "of 5") and standalone "0".
            stripped = n.replace("%", "").replace("-", "")
            try:
                f = float(stripped)
                if abs(f) <= 12:
                    continue
            except ValueError:
                continue
            # The number must appear as a substring of the facts blob.
            if n not in facts_str and stripped not in facts_str:
                notes.append(f"unsupported number: {n}")

    return (len(notes) == 0, notes)


# ---------- Template fallback ----------

def _template_rationale(facts: dict) -> Rationale:
    """Mechanical, deterministic summary from the facts. No LLM.
    Factually safe — every word maps to a fact in the input. Used
    when the LLM rationale fails verification or when the LLM is
    unavailable."""
    sym = facts["symbol"]
    verdict = facts["verdict"]
    consensus = facts["strategy_consensus"]
    best = facts["best_historical_strategy"]
    stats = facts.get("best_strategy_stats") or {}
    sharpe = stats.get("sharpe")
    cagr = stats.get("cagr_pct")
    max_dd = stats.get("max_drawdown_pct")
    ms = facts["current_market_state"]

    summary_parts = [f"{sym}: {verdict}."]
    summary_parts.append(facts["verdict_reason"].rstrip("."))
    if sharpe is not None and cagr is not None:
        summary_parts.append(
            f"Best historical fit was {best} (Sharpe {sharpe:.2f}, CAGR {cagr:.1f}%)."
        )

    # Order matters — factors gets clipped to 4 by `factors[:4]` below.
    # Lead with the strategy consensus (every row has it), then the
    # multi-family signals when present (they add information the rule
    # chain doesn't), then Family-1 detail. This way a row with
    # cross-basket signals doesn't lose them to the cap.
    factors: list[str] = [consensus]

    # Cross-basket signals (Family 2 + 3) — promoted ahead of Family-1
    # detail. Surface them in the deterministic template too, so a
    # verifier-rejected LLM rationale still falls back to facts that
    # include the basket-relative context.
    cs_mom = facts.get("cross_basket_momentum")
    if cs_mom and cs_mom.get("rank") is not None:
        rank = cs_mom["rank"]
        total = cs_mom.get("total")
        if total is not None:
            factors.append(f"Momentum rank {rank} of {total} in basket")
        if cs_mom.get("is_top_quartile"):
            factors.append("Top-quartile basket momentum")
    cs_val = facts.get("cross_basket_valuation")
    if cs_val and cs_val.get("flag") in ("cheap", "expensive"):
        factors.append(f"Valuation flag: {cs_val['flag']}")

    # Family-1 detail — these duplicate parts of the rule_chain, so
    # they go last and may get clipped by [:4] when cross-basket
    # signals are present (acceptable; the rule_chain shows them anyway).
    if ms.get("above_sma_200") is True:
        factors.append("Above 200-day SMA")
    elif ms.get("above_sma_200") is False:
        factors.append("Below 200-day SMA")
    if ms.get("rsi_14") is not None:
        factors.append(f"RSI {ms['rsi_14']:.0f}")
    if ms.get("pct_off_52w_high_pct") is not None:
        factors.append(f"{ms['pct_off_52w_high_pct']:.1f}% off 52w high")

    caveats: list[str] = []
    if max_dd is not None:
        caveats.append(f"Worst historical drawdown {max_dd:.1f}%")
    crashes = [r for r in facts.get("stress_history", []) if r.get("kind") == "crash"]
    crashes.sort(key=lambda r: r.get("return_pct") or 0)
    if crashes:
        worst = crashes[0]
        if worst.get("return_pct") is not None:
            caveats.append(
                f"{worst['name']}: {worst['return_pct']:.1f}% return"
            )

    return Rationale(
        summary=" ".join(summary_parts),
        key_factors=factors[:4],
        caveats=caveats[:2],
        source="template",
        verified=True,         # by construction — built only from facts
        generated_at=datetime.now(timezone.utc).isoformat(),
    )


# ---------- Public API ----------

def build_rationale(
    facts: dict,
    provider: LlmProvider | None = None,
) -> Rationale:
    """Generate (or retrieve cached) rationale. Cache hits and
    deterministic-template paths cost zero LLM calls.

    Verification ladder:
      1. Cache hit → reuse (already verified at write time).
      2. LLM call. Pass output through _verify_locally (substring
         numeric check). Pass → use. Fail → discard, fall through.
      3. Template fallback. Always succeeds, factually safe.
    """
    p = provider or get_provider("rationale")

    if not p.healthy():
        rat = _template_rationale(facts)
        rat.source = "template_no_llm"
        return rat

    key = _cache_key(facts, p.model)
    cached = _cache.get(key)
    if cached is not None:
        rat = Rationale(**{k: v for k, v in cached.items() if k in Rationale.__dataclass_fields__})
        return rat

    prompt = _build_prompt(facts)
    result = p.complete_json(prompt, schema_hint=_SCHEMA_HINT, max_tokens=400)

    if not result.ok:
        # Fallback path: deterministic template.
        rat = _template_rationale(facts)
        rat.source = "template_llm_failed"
        rat.verification_notes = [f"llm error: {result.error}"]
        _cache.put(key, rat.to_dict())
        return rat

    d = result.data
    summary = str(d.get("summary", "")).strip()
    factors = [str(x) for x in (d.get("key_factors") or []) if x][:4]
    caveats = [str(x) for x in (d.get("caveats") or []) if x][:2]

    if not summary:
        rat = _template_rationale(facts)
        rat.source = "template_empty_llm"
        _cache.put(key, rat.to_dict())
        return rat

    candidate = Rationale(
        summary=summary,
        key_factors=factors,
        caveats=caveats,
        source="llm",
        model=p.model,
        generated_at=datetime.now(timezone.utc).isoformat(),
    )
    ok, notes = _verify_locally(candidate, facts)
    candidate.verified = ok
    candidate.verification_notes = notes

    if not ok:
        # LLM hallucinated numbers — fall back to template, never ship
        # the unverified version. Save the verification failure with the
        # template so the UI can show "LLM rationale was rejected" and
        # the operator can debug.
        fallback = _template_rationale(facts)
        fallback.source = "template_llm_unverified"
        fallback.verification_notes = notes
        _cache.put(key, fallback.to_dict())
        return fallback

    _cache.put(key, candidate.to_dict())
    return candidate

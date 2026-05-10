"""Step definitions for rationale.feature.

Hits the rationale module directly with synthetic fact bundles —
no comparator run, no Yahoo, no Ollama. Keeps the suite fast +
deterministic. The end-to-end pipeline gets exercised by manual
runs of `uv run tradepro-compare` in dev.
"""
from __future__ import annotations

import os

from behave import given, when, then

from tradepro_strategies import rationale as rationale_mod
from tradepro_strategies.llm import NoOpProvider


@given("the LLM provider is the no-op (so tests don't call out)")
def step_provider_noop(context) -> None:
    os.environ["TRADEPRO_LLM"] = "noop"
    context.provider = NoOpProvider()


@given("a fact bundle for {symbol} in {bucket} bucket")
def step_fact_bundle(context, symbol: str, bucket: str) -> None:
    context.facts = rationale_mod.gather_facts(
        symbol=symbol,
        bucket=bucket,
        bucket_reason=f"placeholder reason for {bucket}",
        long_count=4 if bucket == "BUY" else 1,
        total_strategies=5,
        market_state={
            "rsi_14": 60.0,
            "above_sma_200": bucket == "BUY",
            "pct_off_52w_high_pct": 5.0,
            "drawdown_from_peak_pct": -3.0,
            "momentum_12m_pct": 12.0 if bucket == "BUY" else -15.0,
            "decision_trace": [],
        },
        sentiment_summary={"mean_sentiment": 0.05, "material_negative_count": 0},
        sentiment_status="scored",
        best_strategy_label="buy_and_hold",
        best_stats={"sharpe": 0.85, "cagr_pct": 12.5, "max_drawdown_pct": -25.0},
        regimes=[],
        fundamentals=None,
        sentiment_demoted=False,
    )


@given('a fact bundle for AVOID with reason "{reason}"')
def step_fact_bundle_avoid(context, reason: str) -> None:
    context.facts = rationale_mod.gather_facts(
        symbol="EXAMPLE",
        bucket="AVOID",
        bucket_reason=reason,
        long_count=0,
        total_strategies=5,
        market_state={
            "rsi_14": 35.0,
            "above_sma_200": False,
            "pct_off_52w_high_pct": 25.0,
            "drawdown_from_peak_pct": -25.0,
            "momentum_12m_pct": -18.0,
            "decision_trace": [],
        },
        sentiment_summary={"mean_sentiment": -0.1, "material_negative_count": 0},
        sentiment_status="scored",
        best_strategy_label="buy_and_hold",
        best_stats={"sharpe": -0.1, "cagr_pct": -3.0, "max_drawdown_pct": -42.0},
        regimes=[],
        fundamentals=None,
        sentiment_demoted=False,
    )


@when("I build a rationale for it")
def step_build_rationale(context) -> None:
    context.rationale = rationale_mod.build_rationale(
        context.facts, provider=NoOpProvider(),
    )


@then("the rationale source is a template variant")
def step_source_is_template(context) -> None:
    src = context.rationale.source
    assert src.startswith("template"), f"expected a template variant, got {src!r}"


@then("the rationale is marked verified")
def step_marked_verified(context) -> None:
    assert context.rationale.verified is True, (
        "expected verified=True for a template rationale"
    )


@then("every number in the rationale appears in the input facts")
def step_numbers_traceable(context) -> None:
    ok, notes = rationale_mod._verify_locally(context.rationale, context.facts)
    assert ok, f"unsupported numbers in rationale: {notes}"


@given('a rationale that mentions an unsupported "999%" figure')
def step_unsupported_rationale(context) -> None:
    from tradepro_strategies.rationale import Rationale
    context.rationale = Rationale(
        summary="QQQ delivered an exceptional 999% return last quarter.",
        key_factors=["999% upside"],
        caveats=[],
        source="llm",
        verified=False,
    )


@given("a fact bundle containing no such number")
def step_facts_without_999(context) -> None:
    context.facts = rationale_mod.gather_facts(
        symbol="QQQ",
        bucket="BUY",
        bucket_reason="trend up",
        long_count=4,
        total_strategies=5,
        market_state={"decision_trace": []},
        sentiment_summary={},
        sentiment_status="scored",
        best_strategy_label="buy_and_hold",
        best_stats={"sharpe": 0.8, "cagr_pct": 14.0, "max_drawdown_pct": -30.0},
        regimes=[],
        fundamentals=None,
        sentiment_demoted=False,
    )


@when("I run the local verifier")
def step_run_local_verifier(context) -> None:
    context.verifier_ok, context.verifier_notes = rationale_mod._verify_locally(
        context.rationale, context.facts,
    )


@then("the rationale is rejected as unverified")
def step_rejected(context) -> None:
    assert context.verifier_ok is False, (
        f"expected verification to fail, got ok={context.verifier_ok} "
        f"notes={context.verifier_notes}"
    )
    assert any("999" in n for n in context.verifier_notes), (
        f"expected '999' to be flagged, got notes={context.verifier_notes}"
    )


@given("the symbol's basket-relative momentum rank is {rank:d} of {total:d} with top quartile")
def step_attach_cs_momentum(context, rank: int, total: int) -> None:
    # Mutate the existing facts dict so the next build_rationale call
    # sees the cross_basket_momentum block. Mirrors what gather_facts
    # produces when handed cross_sectional_momentum from compare.py.
    context.facts["cross_basket_momentum"] = {
        "rank": rank,
        "peers": total - 1,  # gather_facts stores peer_count = N-1 (excluding self)
        "total": total,      # peers + self — what the rationale quotes
        "zscore": 0.85,
        "is_top_quartile": True,
        "value_pct": 18.5,
        "basket_median_pct": 12.0,
    }


@given("the symbol's swing composite score is {total:d} with verdict {verdict}")
def step_attach_swing(context, total: int, verdict: str) -> None:
    context.facts["swing_composite"] = {
        "total": total,
        "verdict": verdict,
        "layers": {"quality": 2, "valuation": 1, "event": 2, "price": 1},
        "reasons": {
            "quality": "Sharpe 0.85 ≥ 0.7; recovered in 200d (fast)",
            "valuation": "mid-basket yield",
            "event": "no recent earnings event",
            "price": "4/5 strategies long",
        },
    }


@given('the symbol\'s basket-relative valuation flag is "{flag}"')
def step_attach_valuation(context, flag: str) -> None:
    context.facts["cross_basket_valuation"] = {
        "flag": flag,
        "yield_pct": 4.2,
        "basket_median_yield_pct": 2.8,
        "basis": f"yield 4.20% vs basket median 2.80% (rank 2 of 13)",
    }


@then('a key factor mentions "{snippet}"')
def step_factor_mentions(context, snippet: str) -> None:
    factors = context.rationale.key_factors or []
    matched = [f for f in factors if snippet in f]
    assert matched, (
        f"no key factor contains {snippet!r}; got: {factors}"
    )


@then('no key factor mentions "{snippet}"')
def step_no_factor_mentions(context, snippet: str) -> None:
    factors = context.rationale.key_factors or []
    bad = [f for f in factors if snippet in f]
    assert not bad, f"unexpected factor with {snippet!r}: {bad}"


@then("the rationale summary mentions the bucket name {bucket}")
def step_summary_mentions_bucket(context, bucket: str) -> None:
    assert bucket in context.rationale.summary, (
        f"bucket name {bucket!r} missing from summary: {context.rationale.summary!r}"
    )


# ----- Prompt v3 (ETF passive guard) -----

@when("I read the rationale module's PROMPT_VERSION")
def step_read_prompt_version(context) -> None:
    context.prompt_version = rationale_mod.PROMPT_VERSION


@then('PROMPT_VERSION equals "{expected}"')
def step_prompt_version_equals(context, expected: str) -> None:
    assert context.prompt_version == expected, (
        f"expected PROMPT_VERSION={expected!r}, got {context.prompt_version!r}"
    )


@given("two cache keys for the same facts but different prompt versions")
def step_two_cache_keys(context) -> None:
    import hashlib
    import json as _json

    facts = {"symbol": "VUKE.L", "verdict": "BUY", "any": "thing"}
    payload = _json.dumps(facts, sort_keys=True, default=str)
    model = "test-model"
    # Mirror the _cache_key construction so a refactor of that helper
    # surfaces here. The point is: different version → different hash.
    context.key_v2 = hashlib.sha1(
        f"{model}::v2-horizons::{payload}".encode()
    ).hexdigest()
    context.key_v3 = hashlib.sha1(
        f"{model}::{rationale_mod.PROMPT_VERSION}::{payload}".encode()
    ).hexdigest()


@then("the two cache keys differ")
def step_cache_keys_differ(context) -> None:
    assert context.key_v2 != context.key_v3, (
        "cache keys collided across prompt versions — v2 entries would not be invalidated"
    )


@when("I render the rationale prompt for an ETF")
def step_render_prompt(context) -> None:
    facts = rationale_mod.gather_facts(
        symbol="VUKE.L",
        bucket="BUY",
        bucket_reason="trend up + range OK",
        long_count=4,
        total_strategies=5,
        market_state={"decision_trace": []},
        sentiment_summary={},
        sentiment_status="scored",
        best_strategy_label="buy_and_hold",
        best_stats={"sharpe": 0.85, "cagr_pct": 12.5, "max_drawdown_pct": -25.0},
        regimes=[],
        fundamentals=None,
        sentiment_demoted=False,
    )
    context.prompt_text = rationale_mod._build_prompt(facts)


@then('the prompt text contains "{snippet}"')
def step_prompt_contains(context, snippet: str) -> None:
    assert snippet in context.prompt_text, (
        f"prompt missing required guard snippet {snippet!r}"
    )

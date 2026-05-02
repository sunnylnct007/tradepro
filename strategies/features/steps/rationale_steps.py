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


@then("the rationale summary mentions the bucket name {bucket}")
def step_summary_mentions_bucket(context, bucket: str) -> None:
    assert bucket in context.rationale.summary, (
        f"bucket name {bucket!r} missing from summary: {context.rationale.summary!r}"
    )

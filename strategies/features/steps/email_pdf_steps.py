"""Steps for email_pdf.feature — pin the envelope-shape regression
that briefly produced "0 BUY candidates" PDFs on 2026-05-09."""
from __future__ import annotations

from behave import given, then, when


def _row(symbol: str = "NVDA", bucket: str = "BUY") -> dict:
    """Minimum row shape the PDF builder + filter helpers consume."""
    return {
        "symbol": symbol,
        "rank": 1,
        "bucket": bucket,
        "bucket_reason": f"{bucket} reason",
        "label": f"{symbol} · sma_crossover",
        "market_state": {
            "rsi_14": 53,
            "pct_off_52w_high_pct": -8,
            "above_sma_200": True,
            "last_price": 198,
            "decision_trace": [
                {"name": "Trend", "status": "pass", "detail": "above SMA"},
            ],
        },
        "stats": {"sharpe": 1.0, "cagr_pct": 30, "max_drawdown_pct": -25},
        "swing_score": {
            "total": 5, "verdict": "BUY",
            "layers": {"quality": 2, "valuation": 1, "event": 2, "price": 0},
            "reasons": {"quality": "Sharpe 1.0", "valuation": "fair",
                        "event": "beat", "price": "0/5"},
        },
        "fundamentals": {"n_holdings": 1, "legal_type": "EQUITY"},
        "external_consensus": {},
        "valuation_flag": {"flag": "FAIR"},
        "horizon_classification": {
            "swing": {"signal": "WATCH", "score": "5/8", "horizon": "1-8w", "reasons": []},
            "long_term": {"signal": "BUY", "score": "7/8", "horizon": "6-18m", "reasons": []},
            "passive": {"signal": "N/A", "score": "N/A", "horizon": "3-5y", "reasons": []},
            "range_pct": 60,
        },
        "sentiment_summary": {},
    }


@given("a payload envelope with 1 BUY row in API shape (payload.rows)")
def step_api_shape(context):
    context.payloads = [{
        "universe": "test",
        "payload": {"rows": [_row()]},
    }]


@given("a payload envelope with 1 BUY row in top-level shape (rows)")
def step_top_shape(context):
    context.payloads = [{
        "universe": "test",
        "rows": [_row()],
    }]


@given("an empty payloads list")
def step_empty(context):
    context.payloads = []


@given("two payload envelopes each containing the same NVDA BUY row")
def step_dup(context):
    context.payloads = [
        {"universe": "etf_us_core", "payload": {"rows": [_row()]}},
        {"universe": "etf_all", "payload": {"rows": [_row()]}},
    ]


@when("I build the digest PDF")
def step_build_pdf(context):
    from tradepro_strategies.email_pdf import build_digest_pdf
    context.pdf = build_digest_pdf(context.payloads)


@when("I filter buckets in the PDF")
def step_filter(context):
    from tradepro_strategies.email_pdf import _filter_bucket
    context.buys = _filter_bucket(context.payloads, "BUY")


@then("the PDF is non-empty")
def step_pdf_non_empty(context):
    assert context.pdf, "expected non-empty PDF bytes"
    assert len(context.pdf) > 0, "expected len > 0"


@then("the PDF is empty")
def step_pdf_empty(context):
    assert not context.pdf, f"expected empty bytes, got {len(context.pdf)} bytes"


@then("the BUY count is {expected:d}")
def step_buy_count(context, expected: int):
    actual = len(context.buys)
    assert actual == expected, f"expected {expected}, got {actual}"

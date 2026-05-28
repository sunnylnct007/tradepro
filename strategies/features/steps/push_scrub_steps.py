"""Steps for push_scrub.feature — pure function, no I/O."""
from __future__ import annotations

import json
import math

from behave import given, then, when

from tradepro_strategies.cli.push_to_api import scrub_for_json


@given("a payload with floats nan, +inf, -inf, and 3.14")
def step_top_level(context):
    context.payload = {
        "nan": float("nan"),
        "pinf": float("inf"),
        "ninf": float("-inf"),
        "pi": 3.14,
    }


@given("a deeply nested payload with NaN inside a list inside a dict")
def step_nested(context):
    context.payload = {
        "rows": [
            {"symbol": "X", "stats": {"sharpe": float("nan"), "cagr": 0.05}},
            {"symbol": "Y", "regimes": [
                {"key": "gfc", "max_dd": float("-inf")},
                {"key": "covid", "max_dd": -0.34},
            ]},
        ],
        "best": float("nan"),
    }


@when("I scrub the payload for JSON")
def step_scrub(context):
    context.scrubbed = scrub_for_json(context.payload)


@then("nan and the infs become null")
def step_nulls(context):
    s = context.scrubbed
    assert s["nan"] is None, s
    assert s["pinf"] is None, s
    assert s["ninf"] is None, s


@then("finite floats are preserved")
def step_finite(context):
    assert context.scrubbed["pi"] == 3.14


@then("the result serialises with json.dumps without raising")
def step_serialises(context):
    serialised = json.dumps(context.scrubbed)
    # Round-trip to confirm valid JSON.
    parsed = json.loads(serialised)
    assert parsed == context.scrubbed


@then("no NaN survives anywhere in the structure")
def step_no_nan(context):
    def has_nan(obj):
        if isinstance(obj, float) and math.isnan(obj):
            return True
        if isinstance(obj, dict):
            return any(has_nan(v) for v in obj.values())
        if isinstance(obj, list):
            return any(has_nan(v) for v in obj)
        return False
    assert not has_nan(context.scrubbed), context.scrubbed

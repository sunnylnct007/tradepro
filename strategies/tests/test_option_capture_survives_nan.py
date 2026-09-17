"""One NaN must not discard a whole symbol's option chain.

Measured 16 Sep 2026, in a single scheduled run: 136 symbols fetched their
chains and persisted ZERO legs, with 125 `Out of range float values are not
JSON compliant: nan` errors. IWM fetched 156 legs and stored none. Every one
printed a ✓ and the run exited 0, so the capture had been throwing away the
majority of its work for as long as it had been running — which is why there
was no option price history to study theta with.

The cause is that `json.dumps` emits a bare `NaN` token for a non-finite
float. That is not valid JSON, the whole request body is rejected, and one
missing greek on one leg costs every other leg in the payload.
"""
import math

from tradepro_strategies.cli.option_chain_capture import _scrub_nan


def test_nan_and_inf_become_null_and_the_row_survives():
    rows = [{"symbol": "IWM", "strike": 220.0, "impliedVolatility": float("nan"),
             "delta": float("-inf"), "bid": 1.25}]
    cleaned, scrubbed = _scrub_nan(rows)
    assert scrubbed == 2
    assert cleaned[0]["impliedVolatility"] is None
    assert cleaned[0]["delta"] is None
    # THE POINT: the fields that were fine are still there.
    assert cleaned[0]["strike"] == 220.0
    assert cleaned[0]["bid"] == 1.25
    assert cleaned[0]["symbol"] == "IWM"


def test_the_scrubbed_payload_is_actually_json_serialisable():
    """Serialising is the thing that failed in production, so assert on it."""
    import json
    rows = [{"iv": float("nan")}, {"iv": 0.31}]
    # Confirm the bug is real and not a misreading of the log.
    assert "NaN" in json.dumps({"rows": rows})
    assert json.dumps({"rows": rows}, allow_nan=False) if False else True
    try:
        json.dumps({"rows": rows}, allow_nan=False)
        raise AssertionError("expected NaN to be rejected by strict JSON")
    except ValueError:
        pass
    cleaned, _ = _scrub_nan(rows)
    json.dumps({"rows": cleaned}, allow_nan=False)   # must not raise


def test_a_clean_payload_is_left_exactly_alone():
    rows = [{"symbol": "NVDA", "bid": 2.0, "openInterest": 41, "expiry": "2026-10-16"}]
    cleaned, scrubbed = _scrub_nan(rows)
    assert scrubbed == 0
    assert cleaned == rows


def test_none_and_strings_are_not_disturbed():
    rows = [{"a": None, "b": "nan", "c": 0.0}]
    cleaned, scrubbed = _scrub_nan(rows)
    assert scrubbed == 0
    assert cleaned[0]["b"] == "nan"       # the STRING "nan" is not a float NaN
    assert cleaned[0]["c"] == 0.0         # a legitimate zero survives
    assert not math.isnan(cleaned[0]["c"])

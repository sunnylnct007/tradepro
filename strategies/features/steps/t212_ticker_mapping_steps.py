"""Steps for t212_ticker_mapping.feature — pins the FX vs equity vs
unsupported paths of paper.brokers.t212._to_t212_ticker."""
from __future__ import annotations

from behave import then, when

from tradepro_strategies.paper.brokers.t212 import _to_t212_ticker


@when('I map the T212 ticker for "{symbol}"')
def step_map(context, symbol: str) -> None:
    context.symbol_in = symbol
    try:
        context.ticker_out = _to_t212_ticker(symbol)
        context.raised = None
    except ValueError as exc:
        context.ticker_out = None
        context.raised = exc


@then('the T212 ticker is "{expected}"')
def step_ticker_is(context, expected: str) -> None:
    assert context.raised is None, f"unexpected raise: {context.raised}"
    assert context.ticker_out == expected, (
        f"expected {expected!r}, got {context.ticker_out!r} from {context.symbol_in!r}"
    )


@then('a ValueError is raised mentioning "{fragment}"')
def step_raises(context, fragment: str) -> None:
    assert context.raised is not None, (
        f"expected ValueError, got ticker {context.ticker_out!r}"
    )
    assert fragment in str(context.raised), (
        f"expected message to mention {fragment!r}, got: {context.raised}"
    )

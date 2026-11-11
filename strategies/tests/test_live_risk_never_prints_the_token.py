"""11 Nov 2026: a DNS blip made requests raise with the full Flex URL in the
message, and the lane printed it — token included — into its log, twice.
"""
from tradepro_strategies.cli.live_risk import redact_flex


def test_the_token_is_stripped_from_an_error_message():
    msg = ("HTTPSConnectionPool(host='gdcdyn.interactivebrokers.com', port=443): Max retries "
           "exceeded with url: /AccountManagement/FlexWebService/GetStatement?q=1597845688&t=SECRET123&v=3")
    out = redact_flex(msg, "SECRET123")
    assert "SECRET123" not in out
    assert "1597845688" not in out
    assert "t=***" in out and "q=***" in out


def test_redaction_without_a_known_token_still_masks_query_values():
    assert redact_flex("x?t=abc&q=9", None) == "x?t=***&q=***"


def test_the_lane_uses_it_on_the_direct_read_failure():
    import inspect
    from tradepro_strategies.cli import live_risk
    src = inspect.getsource(live_risk.main)
    i = src.index("direct Flex read failed")
    assert "redact_flex(" in src[i - 50:i + 120]

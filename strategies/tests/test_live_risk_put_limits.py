"""The owner's short-put limits (build pack 13 Nov 2026): 50% of account value
in total, 20% per sector, 10% per name. One answer every screen reads."""
from tradepro_strategies.cli.live_risk import put_limits

ROWS = [{"symbol": "NVDA", "strike": 150, "qty": -10},   # $150k
        {"symbol": "MU", "strike": 100, "qty": -5}]      # $50k


def test_the_13_nov_book_is_breached_and_blocks_new_puts():
    out = put_limits(ROWS, nav_usd=260_000, sectors={"NVDA": "Tech", "MU": "Tech"})
    assert out["status"] == "breached" and out["new_puts_allowed"] is False
    assert any("total strike value" in b for b in out["breaches"])     # 200k > 130k
    assert any(b.startswith("Tech") for b in out["breaches"])          # 200k > 52k
    assert any(b.startswith("NVDA") for b in out["breaches"])          # 150k > 26k


def test_inside_the_limits_allows_new_puts():
    out = put_limits([{"symbol": "KO", "strike": 60, "qty": -1}], nav_usd=260_000)
    assert out["status"] == "within" and out["new_puts_allowed"] is True


def test_no_account_value_means_no_new_puts():
    out = put_limits(ROWS, nav_usd=None)
    assert out["status"] == "unknown" and out["new_puts_allowed"] is False

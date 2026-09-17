"""THE STRANGLE'S OWN CHAINS — captured first, at more than one expiry.

FUNDING_GATES_V1 S3 grades credit RECEIVED against credit MODELLED, and the
model prices every leg off `iv_used`, a THIRTY-DAY vol index. On a 7-DTE leg in
contango that overprices badly — XSP 15 Sep received 245.56 against a modelled
546 (45%), while the monthly came in at 94.7%. So S3 is gradeable on the
monthly leg only.

Fixing it needs a real per-expiry ATM IV, which needs the chain at more than one
DTE. Before this, option_quote_daily held ZERO rows for SPX and XSP: they were
never in the capture universe at all.

The trap these tests pin: `MARKETS[m]["index"]` is where SPOT comes from, and
for SPX **and** XSP it is `^GSPC`, which has ZERO option expiries on Yahoo. A
lane reusing it captures nothing and reports success.
"""
from unittest.mock import patch

import tradepro_strategies.cli.option_chain_capture as C
from tradepro_strategies.cli.index_strangle_paper import MARKETS


def test_the_chain_roots_are_not_the_spot_tickers():
    roots = {cfg["chain_symbol"] for cfg in MARKETS.values() if cfg.get("chain_symbol")}
    assert "^SPX" in roots and "^XSP" in roots
    # The exact confusion this guards: both markets take spot from ^GSPC.
    assert "^GSPC" not in roots, (
        "^GSPC has no option chain on Yahoo — capturing it yields nothing "
        "while every counter reports success")


def test_each_root_is_fetched_once_per_dte_and_never_twice():
    """SPY/QQQ/GLD appear under more than one market. A duplicate here is a
    duplicate Yahoo fetch on a lane that already earns rate limits."""
    calls = []

    def _fake_capture(sym, *, target_dte, rights="PC"):
        calls.append((sym, target_dte))
        return ([{"symbol": sym, "expiry": "2026-09-24", "strike": 500.0,
                  "right": "P", "iv": 0.2, "spot": 500.0}], "ok")

    with patch.object(C, "capture_symbol", _fake_capture), \
         patch.object(C, "_post_rows", lambda rows: len(rows)), \
         patch.object(C, "_in_capture_window", lambda: (True, "x")), \
         patch("time.sleep", lambda *_: None), \
         patch("sys.argv", ["x", "--symbols", "AAPL", "--strangle-dte", "7,21",
                            "--pace", "0"]):
        C.main()

    roots = {cfg["chain_symbol"] for cfg in MARKETS.values() if cfg.get("chain_symbol")}
    strangle_calls = [c for c in calls if c[0] in roots]
    assert len(strangle_calls) == len(set(strangle_calls)), (
        f"a chain root was fetched twice: {strangle_calls}")
    for root in roots:
        assert (root, 7) in calls and (root, 21) in calls, root


def test_the_strangle_chains_are_captured_before_the_wheel_walk():
    """The wheel walk already runs 109 min against a 3h deadline and backs off
    to a 300s pace. If the strangle set went last, a deadline overrun would
    silently drop the data that has NO history in favour of the data that has
    months of it."""
    order = []

    def _fake_capture(sym, *, target_dte, rights="PC"):
        order.append(sym)
        return ([], "no_expiries")

    with patch.object(C, "capture_symbol", _fake_capture), \
         patch.object(C, "_post_rows", lambda rows: len(rows)), \
         patch.object(C, "_in_capture_window", lambda: (True, "x")), \
         patch("time.sleep", lambda *_: None), \
         patch("sys.argv", ["x", "--symbols", "AAPL", "--strangle-dte", "7",
                            "--pace", "0"]):
        C.main()

    assert "AAPL" in order, order
    assert order.index("^SPX") < order.index("AAPL"), (
        f"the wheel walk ran before the strangle chains: {order}")


def test_no_strangle_dte_means_no_extra_fetches():
    """The default must not change the lane's cost. Omitting the flag leaves
    the wheel walk exactly as it was."""
    calls = []

    def _fake_capture(sym, *, target_dte, rights="PC"):
        calls.append(sym)
        return ([], "no_expiries")

    with patch.object(C, "capture_symbol", _fake_capture), \
         patch.object(C, "_post_rows", lambda rows: len(rows)), \
         patch.object(C, "_in_capture_window", lambda: (True, "x")), \
         patch("time.sleep", lambda *_: None), \
         patch("sys.argv", ["x", "--symbols", "AAPL", "--pace", "0"]):
        C.main()

    assert calls == ["AAPL"], calls

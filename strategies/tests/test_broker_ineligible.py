"""The IWM loop of 2026-09-02 must not be repeatable.

28 identical BUY IWM orders went out that day, one every 15 minutes, the last
90 minutes after the close. IBKR rejected every one with the same permanent
reason (PRIIPs KID — a UK retail account cannot buy a US-domiciled ETF), and
nothing in the stack noticed.

These tests pin the guard that stops it.
"""
from __future__ import annotations

import tradepro_strategies.paper.broker_ineligible as bi

# The exact string IBKR returned, from the OMS record. Not paraphrased —
# a guess about a broker's wording is how this class of bug gets re-shipped.
REAL_IBKR_REJECTION = (
    '"BUY 17 IWM ARCA"\n'
    "No Trading Permission, Customer Ineligible; Ineligibility reasons: \n"
    "This product does not have a KID in English or in a language approved "
    "for your country. Retail clients can trade packaged retail products only "
    "if an appropriate KID is available. More information is available in "
    '<a href="https://www.clientam.com/lib/cstools/faq/#/content/104941212">x</a> .'
)


def test_the_real_rejection_is_permanent():
    assert bi.is_permanent(REAL_IBKR_REJECTION) is True


def test_unknown_and_transient_reasons_stay_retryable():
    # Deliberate: we would rather retry something hopeless than silently stop
    # trading a name for a reason nobody chose.
    assert bi.is_permanent("insufficient buying power") is False
    assert bi.is_permanent("a reason we have never seen before") is False
    assert bi.is_permanent(None) is False
    assert bi.is_permanent("") is False


def test_first_line_drops_the_html_and_faq_link():
    out = bi.first_line(REAL_IBKR_REJECTION)
    assert "No Trading Permission" in out
    assert "<a href" not in out
    assert "More information" not in out


def _fake_get(rows):
    class _R:
        status_code = 200
        def raise_for_status(self): pass
        def json(self): return rows
    def _get(*a, **k): return _R()
    return _get


def test_blocked_symbols_selects_only_permanent_rejections_for_that_broker(monkeypatch):
    rows = [
        {"broker": "IBKR_PAPER", "state": "REJECTED", "symbol": "IWM_US_EQ",
         "cancelledReason": REAL_IBKR_REJECTION},
        # right broker, rejected, but a TRANSIENT reason -> still tradeable
        {"broker": "IBKR_PAPER", "state": "REJECTED", "symbol": "AAPL_US_EQ",
         "cancelledReason": "insufficient buying power"},
        # permanent, but a DIFFERENT broker -> must not leak across accounts
        {"broker": "T212_DEMO", "state": "REJECTED", "symbol": "SPY_US_EQ",
         "cancelledReason": REAL_IBKR_REJECTION},
        # right broker, permanent-sounding, but the order FILLED -> not a block
        {"broker": "IBKR_PAPER", "state": "FILLED", "symbol": "MSFT_US_EQ",
         "cancelledReason": None},
    ]
    import requests
    monkeypatch.setattr(requests, "get", _fake_get(rows))
    bi._CACHE.clear()
    blocked = bi.blocked_symbols("https://api.example", "tok", "IBKR_PAPER")
    assert set(blocked) == {"IWM_US_EQ"}


def test_blocked_symbols_fails_open_when_the_oms_is_unreachable(monkeypatch):
    # This guard must never become a NEW reason that trading stops.
    import requests

    def _boom(*a, **k):
        raise RuntimeError("OMS down")

    monkeypatch.setattr(requests, "get", _boom)
    bi._CACHE.clear()
    assert bi.blocked_symbols("https://api.example", "tok", "IBKR_PAPER") == {}

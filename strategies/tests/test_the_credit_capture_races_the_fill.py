"""The fill price must not be lost to a race with the broker booking it.

24 Sep 2026. XSP monthly placed at 14:12:35Z; the credit was read immediately
after and found nothing, so credit_actual was left NULL. Five hours later the
SAME call returned 995.56 (put 5.227797 + call 4.727797) because by then IBKR
had booked the position. Nothing was wrong with the capture except WHEN it ran.

The number is only recoverable while the position is OPEN — IBKR returns
avgPrice NULL on the order, so the fill price lives on the position and nowhere
else, and the 19:45Z time exit destroys it. A miss is permanent, and it is the
one number the whole paper exercise exists to collect.
"""
import pytest

from tradepro_strategies.cli import index_strangle_paper as P


LEG = {"put_strike": 758.0, "call_strike": 780.0}
FILLED = {"credit": 995.56, "put_entry": 5.227797, "call_entry": 4.727797}


@pytest.fixture(autouse=True)
def _no_sleeping(monkeypatch):
    monkeypatch.setattr("time.sleep", lambda *_: None)
    monkeypatch.setenv("TRADEPRO_CREDIT_RETRY_WAIT_S", "0")


def test_a_credit_that_arrives_late_is_still_captured(monkeypatch):
    # Empty, empty, then the real thing — exactly what happened live.
    seq = [None, None, FILLED]
    monkeypatch.setattr(P, "_credit_from_broker",
                        lambda *a, **k: seq.pop(0) if seq else FILLED)
    got = P._credit_with_retry({"market": "XSP"}, LEG, 2, "XSP", "monthly")
    assert got["credit"] == pytest.approx(995.56)


def test_the_first_answer_is_used_when_it_is_already_there(monkeypatch):
    calls = []

    def _one(*a, **k):
        calls.append(1)
        return FILLED

    monkeypatch.setattr(P, "_credit_from_broker", _one)
    P._credit_with_retry({"market": "XSP"}, LEG, 2, "XSP", "monthly")
    assert len(calls) == 1, "must not keep polling once the credit is known"


def test_a_HALF_pair_keeps_retrying_rather_than_settling_for_it(monkeypatch):
    # credit=None with one leg present is the half-pair guard firing. That is
    # not an answer — the other leg may simply not be booked yet.
    half = {"credit": None, "call_entry": 4.727797}
    seq = [half, half, FILLED]
    monkeypatch.setattr(P, "_credit_from_broker",
                        lambda *a, **k: seq.pop(0) if seq else FILLED)
    got = P._credit_with_retry({"market": "XSP"}, LEG, 2, "XSP", "monthly")
    assert got["credit"] == pytest.approx(995.56)


def test_it_gives_up_and_SAYS_SO(monkeypatch, capsys):
    monkeypatch.setattr(P, "_credit_from_broker", lambda *a, **k: None)
    got = P._credit_with_retry({"market": "XSP"}, LEG, 2, "XSP", "monthly")
    assert got is None
    out = capsys.readouterr().out
    assert "CANNOT be recovered" in out, out
    assert "XSP" in out


def test_a_read_that_RAISES_is_retried_not_fatal(monkeypatch):
    seq = [RuntimeError("timeout"), RuntimeError("timeout"), FILLED]

    def _flaky(*a, **k):
        v = seq.pop(0)
        if isinstance(v, Exception):
            raise v
        return v

    monkeypatch.setattr(P, "_credit_from_broker", _flaky)
    got = P._credit_with_retry({"market": "XSP"}, LEG, 2, "XSP", "monthly")
    assert got["credit"] == pytest.approx(995.56)


def test_the_attempt_count_is_configurable(monkeypatch):
    calls = []
    monkeypatch.setenv("TRADEPRO_CREDIT_RETRIES", "2")
    monkeypatch.setattr(P, "_credit_from_broker",
                        lambda *a, **k: calls.append(1))
    P._credit_with_retry({"market": "XSP"}, LEG, 2, "XSP", "monthly")
    assert len(calls) == 2

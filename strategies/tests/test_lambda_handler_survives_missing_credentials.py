"""The Lambda must survive a best-effort call that EXITS rather than raises.

THE OUTAGE (31 Aug 2026). Provenance logging was added to the handler, wrapped
in `except Exception` and documented "logging must never fail the job". It
called log_run -> load_credentials(), which calls sys.exit() when the API
credentials are absent. SystemExit derives from BaseException, not Exception,
so it went straight through the guard and killed the runtime with exit 2.

Lambda has no credentials file, so this fired on EVERY invocation: every
scheduled job, the 15-minute alerts, and the smoke test that finally surfaced
it. The function was completely dead between the CI deploy and this fix.
"""
from __future__ import annotations

import importlib
import sys


def _handler_module():
    root = __import__("pathlib").Path(__file__).resolve().parents[1]
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    return importlib.import_module("lambda_handler")


def test_provenance_catches_a_systemexit(monkeypatch):
    """The exact failure: a best-effort helper that exits instead of raising."""
    h = _handler_module()

    def _exits(*a, **kw):
        raise SystemExit(2)

    monkeypatch.setattr(h, "_provenance", lambda: {
        "jobs_commit": "abc", "api_commit": None, "status": "ok", "detail": None})
    import tradepro_strategies.run_log as rl
    monkeypatch.setattr(rl, "log_run", _exits)
    # Must return, not exit.
    out = h._report_provenance("index_strangle_paper")
    assert out["status"] == "ok"


def test_an_unknown_job_returns_the_known_list_rather_than_dying():
    """What CI's smoke test asserts. It sends {"job":"__smoke__"} and expects
    the handler to answer with the jobs it knows — that is the check that
    caught this outage, and it must keep working."""
    h = _handler_module()
    res = h.handler({"job": "__smoke__"}, None)
    import json
    body = json.loads(res["body"])
    assert "post_earnings_puts" in body["known"]
    assert body["ok"] is False


def test_the_guard_names_systemexit():
    """Pinned at the source, because `except Exception` here reads as correct
    and is not. Anything reaching load_credentials can EXIT rather than raise."""
    h = _handler_module()
    src = open(h.__file__).read()
    i = src.find("def _report_provenance")
    j = src.find("\ndef ", i + 10)
    guard = src[i:j]
    assert "SystemExit" in guard or "BaseException" in guard, (
        "_report_provenance must catch SystemExit explicitly — it is not an "
        "Exception, and load_credentials exits rather than raising")

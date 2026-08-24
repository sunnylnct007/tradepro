"""Exactly one module may build a yfinance session.

A yfinance session must carry BOTH a timeout and browser impersonation, and the
two get separated whenever someone writes a second copy:

* No timeout -> `ticker.history()` blocks on a socket read with no deadline.
  One options-screen run took 2h50m on 15 Aug 2026 for exactly this.
* No `impersonate=` -> handing yfinance a bare `curl_cffi.Session` REPLACES its
  own browser-impersonating one, and Yahoo answers a non-browser TLS
  fingerprint with 429. The project spent weeks waiting out a "Yahoo rate
  limit" that it was causing itself.

Both sites existed. The bar provider got the impersonation fix on 23 Aug;
`quant_engine/options/chains.py` kept its own copy and produced **158
YFRateLimitError warnings on 24 Aug alone** — the day after. Measured directly:

    Session(timeout=8)                        -> YFRateLimitError
    yahoo_session()                           -> 23 expiries

A fix that does not propagate is barely a fix. This test makes the second copy
impossible rather than trusting the next author to remember.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

STRATEGIES_ROOT = Path(__file__).resolve().parents[1]
PACKAGE = STRATEGIES_ROOT / "tradepro_strategies"
OWNER = PACKAGE / "yahoo_session.py"


def _builds_curl_cffi_session(path: Path) -> list[int]:
    """Line numbers where this module constructs a curl_cffi Session."""
    try:
        tree = ast.parse(path.read_text())
    except (SyntaxError, UnicodeDecodeError):
        return []
    # Which local names refer to curl_cffi's requests module?
    aliases: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and (node.module or "").startswith("curl_cffi"):
            for a in node.names:
                aliases.add(a.asname or a.name)
        elif isinstance(node, ast.Import):
            for a in node.names:
                if a.name.startswith("curl_cffi"):
                    aliases.add(a.asname or a.name.split(".")[0])
    if not aliases:
        return []
    hits = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            f = node.func
            if isinstance(f, ast.Attribute) and f.attr == "Session":
                base = f.value
                name = getattr(base, "id", None) or getattr(base, "attr", None)
                if name in aliases:
                    hits.append(node.lineno)
    return hits


def test_the_owner_module_exists():
    assert OWNER.exists(), f"the single yahoo session owner is missing: {OWNER}"


def test_only_the_owner_builds_a_curl_cffi_session():
    offenders = []
    for path in sorted(PACKAGE.rglob("*.py")):
        if path.resolve() == OWNER.resolve():
            continue
        for line in _builds_curl_cffi_session(path):
            rel = path.relative_to(STRATEGIES_ROOT).as_posix()
            offenders.append(f"  {rel}:{line}")

    assert not offenders, (
        "These modules build their own curl_cffi Session:\n"
        + "\n".join(offenders)
        + "\n\nUse tradepro_strategies.yahoo_session.yahoo_session() instead. A "
          "second copy is how the impersonate= fix reached the bar provider on "
          "23 Aug and not the options chain, which then logged 158 "
          "YFRateLimitError warnings the following day."
    )


def test_the_owner_sets_both_timeout_and_impersonation():
    """Neither may be dropped in favour of the other — that is the whole point."""
    src = OWNER.read_text()
    assert "impersonate=" in src, "the session must impersonate a browser or Yahoo returns 429"
    assert "timeout" in src, "the session must carry a timeout or a throttle becomes a hang"


@pytest.mark.parametrize("caller", [
    "tradepro_strategies/bar_cache/providers/yfinance_provider.py",
    "tradepro_strategies/quant_engine/options/chains.py",
])
def test_known_callers_go_through_the_owner(caller: str):
    """Both historical copy sites must reference the shared builder."""
    src = (STRATEGIES_ROOT / caller).read_text()
    assert "yahoo_session" in src, (
        f"{caller} no longer routes through the shared session builder"
    )

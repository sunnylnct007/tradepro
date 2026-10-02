"""Every read of the IBKR position book must ask for a FRESH one.

THE DAMAGE, 1 Oct 2026. The lane's position seed called
/api/integrations/ibkr/positions WITHOUT ?fresh=true, so it was answered from
IBKR's cache. Every seed read through the night returned the same thing:

    00:16  ESNTx118
    00:32  ESNTx118
    ...
    05:20  ESNTx118

while sells were filling against it. mean_reversion_swing_ibkr re-sold the full
118 twenty-three times and drove a +118 long to -2,596 SHORT;
momentum_pullback did the same to CLF, +261 -> -2,088. Both sleeves are
long-only and cannot hold a short by design.

The strategy was reading the golden source correctly. The golden source was
stale. IBKRClient.GetPositionsAsync states the rule in its own docstring --
"pass forceFresh after anything that MUTATES the book" -- and the seed is the
most important caller of all, because it runs immediately after the previous
cycle placed orders.

WHY A SOURCE TEST IS RIGHT HERE, when it usually is not. This repo has been
bitten by source-slice tests that passed while the code they described was
unreachable, so the oversell guard's tests call the rule instead of reading it.
But "which call sites pass a query parameter" is a STATIC property of the
source -- there is no runtime behaviour to exercise, and a call site that
forgets it fails silently and invisibly, which is exactly how this one survived.
Four of the nine callers already passed it; the one that decided whether to
sell did not.
"""
from __future__ import annotations

import pathlib

import pytest

#: Every BROKER position endpoint, not just the one that burned us. T212 had
#: the identical fault with no escape hatch at all — its endpoint exposed no
#: bypass, so a lane could not get an uncached book even if it knew to ask
#: (fixed alongside this).
#:
#: IG is DELIBERATELY ABSENT. It has no positions cache at any layer —
#: IGClient.GetPositionsAsync calls IG on every request — so there is nothing
#: to bypass and ?fresh=true would be a parameter the server ignores. Add it
#: here the moment an IG positions cache appears.
ENDPOINTS = (
    "integrations/ibkr/positions",
    "integrations/trading212/positions",
)
#: The call may span several lines (requests.get(url, params={...})), so the
#: parameter can legitimately appear a little after the URL itself.
WINDOW = 4

_ROOT = pathlib.Path(__file__).resolve().parents[1] / "tradepro_strategies"


def _call_sites() -> list[tuple[pathlib.Path, int, str, bool]]:
    out = []
    for path in sorted(_ROOT.rglob("*.py")):
        lines = path.read_text().splitlines()
        for i, line in enumerate(lines):
            if line.lstrip().startswith("#"):
                continue
            if not any(e in line for e in ENDPOINTS):
                continue
            # An explicit, reasoned opt-out for lines that NAME the endpoint
            # without calling it (provenance labels, docs). It must say why,
            # and it is deliberately ugly so it cannot be sprinkled quietly.
            prev = lines[i - 1] if i else ""
            if "fresh-exempt:" in prev or "fresh-exempt:" in line:
                continue
            window = " ".join(lines[i:i + WINDOW])
            out.append((path, i + 1, line.strip(), "fresh" in window))
    return out


def test_there_are_call_sites_to_check():
    """If this fails the scan is broken, not the code — re-point it."""
    assert _call_sites(), (
        f"no call sites matched any of {ENDPOINTS}; an endpoint was probably "
        "renamed. Re-point this test rather than deleting it: it guards the "
        "1 Oct runaway that sold a long-only sleeve 2,596 short."
    )


@pytest.mark.parametrize(
    "path,lineno,src",
    [(p, n, s) for p, n, s, fresh in _call_sites() if not fresh],
    ids=lambda v: f"{getattr(v, 'name', v)}",
)
def test_no_cached_position_read(path, lineno, src):
    """Parametrised over the VIOLATIONS, so a regression names the file."""
    pytest.fail(
        f"{path.name}:{lineno} reads the IBKR position book without "
        f"?fresh=true:\n    {src}\n\n"
        "A cached book is what let a long-only sleeve sell itself 2,596 "
        "short on 1 Oct 2026 — the seed read 'ESNTx118' all night while the "
        "real position was going increasingly negative. Add "
        'params={"fresh": "true"} (or ?fresh=true in the path). If this read '
        "genuinely does not inform a trading or reconciliation decision, say "
        "so here explicitly rather than dropping the parameter."
    )


def test_every_caller_asks_for_a_fresh_book():
    """The summary assertion, so the count is visible in the failure."""
    sites = _call_sites()
    stale = [f"{p.name}:{n}" for p, n, _s, fresh in sites if not fresh]
    assert not stale, (
        f"{len(stale)} of {len(sites)} IBKR position reads are served from "
        f"cache: {', '.join(stale)}"
    )

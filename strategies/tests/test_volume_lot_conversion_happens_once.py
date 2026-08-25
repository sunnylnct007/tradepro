"""The IBKR lot→shares conversion must happen at exactly ONE layer.

IBKR reports historical volume in 100-share lots. On 2026-08-23 the ×100 was
applied in TWO places for a single pipeline:

    IBKRResponseParser.ParseHistory        (C# API — correct, this is the
                                            boundary where raw IBKR JSON lands)
    ibkr_web_provider._parse               (Python — WRONG, it reads the API,
                                            not IBKR)

`ibkr_web_provider` fetches `/api/integrations/ibkr/price-history` from our own
backend, whose bars have already been through ParseHistory. So every row it
wrote came out ×100 too high. Measured on the endpoint it actually reads:

    API  /price-history      TXN 1d    2,212,121   (correct, shares)
    parquet store                    221,212,100   (×100)

and SPY's August bars stored 5.9 BILLION shares/day against a real ~59 million.

Both call sites were individually defensible, which is what makes this class of
bug survive review. The conversion belongs at the boundary where raw IBKR JSON
is parsed, and nowhere downstream of it.
"""
from __future__ import annotations

import re
from pathlib import Path

PROVIDER = (Path(__file__).resolve().parents[1]
            / "tradepro_strategies" / "bar_cache" / "providers"
            / "ibkr_web_provider.py")


def test_ibkr_web_provider_does_not_reapply_the_lot_conversion():
    """This provider reads our API, which has already converted lots→shares."""
    src = PROVIDER.read_text()
    # Strip comments so the explanation of the bug doesn't trip its own guard.
    code = "\n".join(l.split("#", 1)[0] for l in src.splitlines())
    offenders = [
        m.group(0) for m in
        re.finditer(r'volume[^\n]*\*[^\n]*(_IBKR_VOLUME_LOT_SIZE|100)\b', code)
    ]
    assert not offenders, (
        "ibkr_web_provider appears to multiply volume again:\n  "
        + "\n  ".join(offenders)
        + "\n\nIt reads /api/integrations/ibkr/price-history, whose bars have "
          "already been through IBKRResponseParser.ParseHistory. Converting "
          "again stores volume 100x too high — SPY at 5.9 billion shares/day."
    )

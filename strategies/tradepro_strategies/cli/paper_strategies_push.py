"""tradepro-paper-strategies-push — publish the local registry to the API.

The dashboard needs to know "which strategies are available" without
the API box having a Python install. The Mac (which DOES have the
registry) introspects it and pushes the catalogue to the API.

Same one-way push model as compare / heartbeat / paper-backtest:
    POST /api/ingest/paper-strategies   bearer token, JSON body

Run this once after `uv pip install -e .` and any time you register
a new strategy. Easy to wire into the daily launchd refresh later
(strategies catalog changes rarely so once-per-deploy is fine).
"""
from __future__ import annotations

import json

from ..paper import registry as strategy_registry
# Importing the package triggers every @register_strategy decorator via
# strategies/__init__.py. Don't trim individual imports here — every
# missing one means a strategy silently disappears from the catalog.
import tradepro_strategies.paper.strategies  # noqa: F401
from . import push_to_api


def build_catalog() -> dict:
    """Snapshot the registry into a JSON-friendly payload. Pulls the
    class docstring (first non-empty paragraph) for the description,
    so the UI tooltip stays in lock-step with the code without a
    separate metadata file. Also surfaces provenance + lifecycle
    metadata (source / status / default_lookback_days) so the UI can
    render trader-vs-scaffold badges and pre-fill lookback without
    operators having to know strategy internals."""
    items = []
    for name in strategy_registry.list_names():
        spec = strategy_registry.get(name)
        cls = spec.cls
        doc = (cls.__doc__ or "").strip()
        # First paragraph = up to the first blank line, then collapsed
        # to a single line for the UI summary.
        first_para = doc.split("\n\n", 1)[0].strip()
        summary = " ".join(line.strip() for line in first_para.splitlines() if line.strip())
        items.append({
            "name": name,
            "class": f"{cls.__module__}:{cls.__name__}",
            "summary": summary,
            "source": getattr(cls, "source", "scaffold"),
            "status": getattr(cls, "status", "evaluating"),
            "default_lookback_days": getattr(cls, "default_lookback_days", 0),
            # Operator-facing caveats — short, actionable strings the UI
            # renders as a warning banner under the strategy pill so the
            # trader can't accidentally treat a design-limited strategy
            # as production-ready.
            "caveats": list(getattr(cls, "caveats", []) or []),
            "default_params": spec.default_params(),
        })
    return {
        "kind": "paper-strategies",
        "report_id": "paper-strategies-catalog",
        "count": len(items),
        "strategies": items,
    }


def main() -> int:
    catalog = build_catalog()
    print(json.dumps(catalog, indent=2, default=str))
    base, token = push_to_api.load_credentials()
    push_to_api.push("paper-strategies", catalog, base, token)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

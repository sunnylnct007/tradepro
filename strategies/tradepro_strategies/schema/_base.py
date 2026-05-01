"""Shared Pydantic config for every schema model.

`extra="allow"` is intentional: the comparator output evolves quickly,
and we don't want a new field on the producer side to fail validation
on every existing row before the schema is updated. Validation still
catches the things that matter — wrong types, missing required fields,
incompatible structures.
"""
from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class TPModel(BaseModel):
    """Base for every TradePro payload model."""
    model_config = ConfigDict(
        extra="allow",
        # Coerce ints → floats etc. when reasonable. Strict-mode here
        # would reject backtest stats that happen to be integer-valued.
        strict=False,
        # Keep field order stable for deterministic JSON output.
        populate_by_name=True,
    )

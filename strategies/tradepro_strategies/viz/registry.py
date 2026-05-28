"""Chart registry — register builders by name; render by lookup.

Mirrors the strategy registry pattern: each chart is a class that
declares its `name` + `description` + `build(**inputs) → dict`. The
returned dict is a Plotly figure spec ready for the frontend's
PlotlyChart component to render.

Example::

    @register_chart
    class IchimokuCloud(ChartBuilder):
        name = "ichimoku_cloud"
        description = "Per-symbol Ichimoku cloud with entry markers."

        def build(self, *, symbol: str, df, fills) -> dict:
            import plotly.graph_objects as go
            fig = go.Figure(...)
            return fig.to_dict()

Frontend never needs to know what charts exist — it discovers them
via ``list_charts()`` and asks for ``build_chart(name, **inputs)`` on
demand. Tests assert the figure dict shape (no headless browser
required).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, ClassVar, Iterable


@dataclass(frozen=True)
class ChartSpec:
    """Public-facing description of a registered chart. Used by the
    catalog endpoint so the UI can render a picker without having to
    know individual chart names a priori."""

    name: str
    description: str
    inputs: tuple[str, ...]  # required keyword arguments to .build()


class ChartBuilder:
    """Base class. Subclass + decorate with ``@register_chart``.

    Subclasses must declare ``name`` (unique kebab/snake-case key) and
    ``description`` (human-readable one-liner). ``build`` returns a
    Plotly figure dict (use ``fig.to_dict()`` or ``fig.to_plotly_json()``).
    ``required_inputs`` declares the keyword argument names ``build``
    expects; the registry exposes these to the UI for parameter prompts.
    """

    name: ClassVar[str] = ""
    description: ClassVar[str] = ""
    required_inputs: ClassVar[tuple[str, ...]] = ()

    def build(self, **inputs: Any) -> dict:  # pragma: no cover - abstract
        raise NotImplementedError


_REGISTRY: dict[str, type[ChartBuilder]] = {}


def register_chart(cls: type[ChartBuilder]) -> type[ChartBuilder]:
    """Class decorator — registers a ChartBuilder under its ``name``.

    Re-registration is allowed; the last registration wins. This is
    intentional for hot-reload in dev. In tests we rely on importing
    the module to register, and the same import is idempotent.
    """
    if not cls.name:
        raise ValueError(f"ChartBuilder {cls.__name__} must set .name")
    _REGISTRY[cls.name] = cls
    return cls


def list_charts() -> Iterable[ChartSpec]:
    """Return the catalog of registered charts in deterministic order
    (sorted by name) for a stable UI."""
    for name in sorted(_REGISTRY):
        cls = _REGISTRY[name]
        yield ChartSpec(
            name=cls.name,
            description=cls.description,
            inputs=tuple(cls.required_inputs),
        )


def build_chart(name: str, /, **inputs: Any) -> dict:
    """Look up a chart by name, build the figure, and return a fully
    JSON-serialisable dict.

    Plotly's ``to_plotly_json()`` returns a dict that may still embed
    numpy arrays inside trace values — ``json.dumps`` then fails. We
    round-trip through ``plotly.io.to_json`` (which uses Plotly's own
    encoder that knows how to flatten numpy / datetime / Decimal) so
    callers always get a vanilla dict ready for an HTTP response or a
    Postgres JSONB column.

    Raises KeyError if the chart isn't registered.
    """
    try:
        cls = _REGISTRY[name]
    except KeyError as exc:
        raise KeyError(
            f"Unknown chart {name!r}. Registered: {sorted(_REGISTRY)}"
        ) from exc
    raw = cls().build(**inputs)
    return _to_jsonable(raw)


def _to_jsonable(figure: Any) -> dict:
    """Ensure the figure dict is round-trip JSON-safe by serialising
    through Plotly's PlotlyJSONEncoder and reloading. If plotly isn't
    importable for some reason (tests with no plotly dep), fall back
    to a stdlib pass that recursively unboxes numpy."""
    try:
        import json
        import plotly.io as pio
        return json.loads(pio.to_json(figure))
    except Exception:
        # Fallback path — rarely hit, only if pio.to_json can't see
        # the figure for any reason. Plain dict pass-through.
        return figure


def _reset_for_tests() -> None:
    """Test helper — drop all registered charts so a test can register
    a stub builder without colliding with the real registry."""
    _REGISTRY.clear()


__all__ = [
    "ChartBuilder",
    "ChartSpec",
    "build_chart",
    "list_charts",
    "register_chart",
]

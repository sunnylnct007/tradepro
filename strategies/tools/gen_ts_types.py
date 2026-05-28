"""Generate frontend/src/api/types.generated.ts from the Pydantic schema.

Run from the strategies directory:

    uv run python tools/gen_ts_types.py

The output is a single TypeScript file with one interface per Pydantic
model + the SCHEMA_VERSION constant. The hand-written types.ts can
re-export from .generated and add anything UI-only on top.

Why a custom generator over `datamodel-code-generator` or
`json-schema-to-typescript`: our types are small (~10 models), simple
(no recursive references the standard tools mishandle), and we want
predictable output that's easy to diff. Hand-rolled is ~150 lines and
gives us complete control.
"""
from __future__ import annotations

import sys
import types
import typing
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal, Union, get_args, get_origin


def _add_repo_root_to_path() -> None:
    here = Path(__file__).resolve().parent
    sys.path.insert(0, str(here.parent))


_add_repo_root_to_path()

# Import after path tweak so script works when run from any cwd.
from tradepro_strategies import schema  # noqa: E402
from tradepro_strategies.schema._base import TPModel  # noqa: E402

OUT_PATH = Path(__file__).resolve().parents[2] / "frontend" / "src" / "api" / "types.generated.ts"


_UNION_TYPES: tuple = (Union,)
if hasattr(types, "UnionType"):
    _UNION_TYPES = (Union, types.UnionType)


def py_to_ts(annot: Any) -> str:
    """Map a Python type annotation to a TypeScript type string."""
    if annot is type(None):
        return "null"
    origin = get_origin(annot)
    args = get_args(annot)

    # Optional / Union — handle both typing.Union[A, B] and PEP 604 `A | B`.
    if origin in _UNION_TYPES:
        inner = " | ".join(py_to_ts(a) for a in args)
        return inner if inner else "unknown"

    if origin is list:
        return f"{py_to_ts(args[0])}[]" if args else "unknown[]"

    if origin is dict:
        if len(args) == 2:
            return f"Record<{py_to_ts(args[0])}, {py_to_ts(args[1])}>"
        return "Record<string, unknown>"

    if origin is Literal:
        return " | ".join(_literal_value(a) for a in args)

    # Pydantic model nested → reference by class name.
    if isinstance(annot, type) and issubclass(annot, TPModel):
        return annot.__name__

    # Primitives — checked AFTER union/list/dict so the special handling
    # for compound types takes precedence.
    if annot is str:
        return "string"
    if annot is bool:
        return "boolean"
    if annot is int or annot is float:
        return "number"
    if annot is Any:
        return "unknown"
    if annot is dict:
        return "Record<string, unknown>"
    if annot is list:
        return "unknown[]"

    # Last-resort fallback — emit the class name only, never the full
    # module path (`tradepro_strategies.schema.compare.X`).
    return getattr(annot, "__name__", "unknown")


def _literal_value(v: Any) -> str:
    if isinstance(v, str):
        return f"'{v}'"
    if v is None:
        return "null"
    if isinstance(v, bool):
        return "true" if v else "false"
    return repr(v)


def emit_model(model: type[TPModel]) -> str:
    lines = [f"export interface {model.__name__} {{"]
    for name, info in model.model_fields.items():
        annot = info.annotation
        ts = py_to_ts(annot)
        # Optional if has a default value (anything other than required).
        # In Pydantic v2 we use is_required().
        optional = not info.is_required()
        marker = "?" if optional else ""
        # Map python from_ → from in JSON.
        emit_name = "from" if name == "from_" else name
        lines.append(f"  {emit_name}{marker}: {ts};")
    lines.append("}")
    return "\n".join(lines)


def collect_models() -> list[type[TPModel]]:
    seen: list[type[TPModel]] = []
    seen_names: set[str] = set()
    for attr in sorted(dir(schema)):
        obj = getattr(schema, attr)
        if isinstance(obj, type) and issubclass(obj, TPModel) and obj is not TPModel:
            if obj.__name__ not in seen_names:
                seen.append(obj)
                seen_names.add(obj.__name__)
    return seen


def main() -> None:
    models = collect_models()
    parts: list[str] = []
    parts.append("// AUTO-GENERATED — do not edit by hand.")
    parts.append("// Regenerate with: uv run python tools/gen_ts_types.py")
    parts.append(f"// Generated at:   {datetime.now(timezone.utc).isoformat()}")
    parts.append(f"// Source:         tradepro_strategies.schema (Pydantic)")
    parts.append("")
    parts.append(f"export const SCHEMA_VERSION = '{schema.SCHEMA_VERSION}';")
    parts.append("")
    for m in models:
        parts.append(emit_model(m))
        parts.append("")
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text("\n".join(parts).rstrip() + "\n")
    print(f"wrote {len(models)} interfaces → {OUT_PATH}")
    for m in models:
        print(f"  - {m.__name__}")


if __name__ == "__main__":
    main()

"""Step definitions for documents.feature.

Hits the extractor + manifest builder directly. No HTTP, no API, no
Yahoo, no LLM — same isolation principle as the rationale tests.
"""
from __future__ import annotations

from pathlib import Path
import re
import tempfile

from behave import given, when, then

from tradepro_strategies.documents import (
    build_manifest,
    extract,
)


@given('a temp file containing "{content}"')
def step_temp_file(context, content: str) -> None:
    fd = tempfile.NamedTemporaryFile(
        suffix=".txt", delete=False, mode="w", encoding="utf-8",
    )
    fd.write(content)
    fd.close()
    context.tmp_path = Path(fd.name)


@given('a temp file with extension "{ext}"')
def step_temp_file_ext(context, ext: str) -> None:
    fd = tempfile.NamedTemporaryFile(
        suffix=ext, delete=False, mode="w", encoding="utf-8",
    )
    fd.write("placeholder")
    fd.close()
    context.tmp_path = Path(fd.name)


@when("I run extract on that file")
def step_run_extract(context) -> None:
    context.error = None
    try:
        context.extracted = extract(context.tmp_path)
    except Exception as e:  # noqa: BLE001
        context.extracted = None
        context.error = e


@when('I build a manifest with title "{title}" and symbols "{symbols}"')
def step_build_manifest(context, title: str, symbols: str) -> None:
    syms = symbols.split(",")
    context.manifest = build_manifest(
        extracted=context.extracted,
        title=title,
        linked_symbols=syms,
    )


@then("the file_kind is {kind}")
def step_file_kind(context, kind: str) -> None:
    assert context.extracted is not None
    assert context.extracted.file_kind == kind, (
        f"expected {kind}, got {context.extracted.file_kind}"
    )


@then("the extracted char_count is greater than 0")
def step_char_count_positive(context) -> None:
    assert context.extracted.char_count > 0


@then("the sha256 is a 64-character hex string")
def step_sha256_format(context) -> None:
    s = context.extracted.sha256
    assert re.fullmatch(r"[0-9a-f]{64}", s), f"bad sha256: {s!r}"


@then("the manifest's linked_symbols are {expected}")
def step_linked_symbols(context, expected: str) -> None:
    import json
    want = json.loads(expected)
    got = list(context.manifest.linked_symbols)
    assert got == want, f"expected {want}, got {got}"


@then("the manifest has a uuid doc_id")
def step_doc_id_uuid(context) -> None:
    import uuid
    uuid.UUID(context.manifest.doc_id)  # raises if not valid


@then("the manifest preserves the file's char_count")
def step_char_count_preserved(context) -> None:
    assert context.manifest.char_count == context.extracted.char_count


@then("a ValueError is raised mentioning supported types")
def step_value_error_supported(context) -> None:
    assert isinstance(context.error, ValueError), (
        f"expected ValueError, got {type(context.error).__name__}: {context.error!r}"
    )
    msg = str(context.error)
    assert "supported" in msg.lower() or "unsupported" in msg.lower(), msg

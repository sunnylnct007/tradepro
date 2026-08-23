"""Every shipped launchd plist must be well-formed XML.

Why this test exists: eight of the twenty-two plists in scripts/ contained a
double hyphen inside an XML comment (`--ibkr-only`, `--quarantine`, and so on).
XML forbids `--` inside a comment, so Python's parser rejected the file
outright. Apple's parser is lenient and accepts it, which is exactly what made
this hard to notice — launchd ran the jobs happily while every strict reader
choked, so the breakage only ever showed up in tooling built around them.

The flags themselves are fine in `<string>--flag</string>`; only comment text
is constrained. Comments now write them as `[flag: name]`.
"""
from __future__ import annotations

import plistlib
from pathlib import Path

import pytest

PLIST_DIR = Path(__file__).resolve().parents[1] / "scripts"
PLISTS = sorted(PLIST_DIR.glob("*.plist"))


def test_plist_directory_is_found():
    assert PLISTS, f"no plists discovered under {PLIST_DIR}"


@pytest.mark.parametrize("path", PLISTS, ids=lambda p: p.name)
def test_plist_is_well_formed_xml(path: Path):
    try:
        with path.open("rb") as fh:
            plistlib.load(fh)
    except Exception as exc:  # noqa: BLE001 — report the file, not a bare trace
        pytest.fail(
            f"{path.name} is not well-formed XML: {exc}\n"
            "If this is a '--' inside an <!-- comment -->, XML forbids it. "
            "Write the flag as [flag: name] instead; leave "
            "<string>--flag</string> arguments alone."
        )


@pytest.mark.parametrize("path", PLISTS, ids=lambda p: p.name)
def test_plist_declares_a_label(path: Path):
    """A plist without a Label is silently ignored by launchd."""
    with path.open("rb") as fh:
        data = plistlib.load(fh)
    assert data.get("Label"), f"{path.name} has no Label"

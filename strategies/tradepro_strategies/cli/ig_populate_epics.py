"""tradepro-ig-populate-epics — discover IG epics for the candidates
listed in `paper/ig_epic_map.json`.

The strategy refuses to route a symbol whose `epic` is null
(`IGEpicMissingError` — see `paper/ig_epic_map.py`). Until the operator
populates each epic, the scanner drops every candidate at
`scanner-drop-no-epic` and no trades fire. This CLI talks to the
backend's `/api/admin/ig/search?term=<symbol>` endpoint (which proxies
IG's `markets?searchTerm`) and writes the chosen epic back into
`ig_epic_map.json` so the strategy can route on the next session.

Default behaviour is INTERACTIVE — print IG's matches for each symbol
and prompt the operator to pick one. `--auto` accepts the first
equity-CFD-looking match (best when the candidates are liquid US ETFs
with one obvious listing). Existing populated entries are skipped
unless `--force` is set.

Usage:

  uv run tradepro-ig-populate-epics
      --api-base http://localhost:5252
      --account demo
      --interactive            # default

  uv run tradepro-ig-populate-epics --api-base ... --auto
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

try:
    import requests
except ImportError:
    print("requests is required (uv pip install requests)", file=sys.stderr)
    sys.exit(2)


_DEFAULT_MAP = (
    Path(__file__).parent.parent
    / "paper" / "ig_epic_map.json"
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Populate IG epics in ig_epic_map.json via "
                    "/api/admin/ig/search.",
    )
    parser.add_argument(
        "--api-base", required=True,
        help="API base URL (e.g. http://localhost:5252)",
    )
    parser.add_argument(
        "--map", default=str(_DEFAULT_MAP),
        help=f"Path to ig_epic_map.json (default: {_DEFAULT_MAP})",
    )
    parser.add_argument(
        "--auto", action="store_true",
        help="Accept the first equity-CFD-looking match without prompting. "
             "Risk: may pick a wrong listing if there are multiple matches; "
             "verify the populated epics before relying on them for live trading.",
    )
    parser.add_argument(
        "--force", action="store_true",
        help="Re-populate symbols that already have a non-null epic.",
    )
    parser.add_argument(
        "--auth-token", default=None,
        help="Bearer token for Authorization header. Falls back to "
             "TRADEPRO_API_TOKEN env var if unset. Some deployments don't "
             "require auth on localhost — try without first.",
    )
    args = parser.parse_args()

    map_path = Path(args.map)
    if not map_path.exists():
        print(f"map file not found: {map_path}", file=sys.stderr)
        return 2

    with map_path.open("r", encoding="utf-8") as f:
        body: dict[str, Any] = json.load(f)

    token = args.auth_token or os.environ.get("TRADEPRO_API_TOKEN")
    headers = {"Authorization": f"Bearer {token}"} if token else {}

    updated = 0
    skipped_existing = 0
    failed: list[str] = []

    for sym, entry in body.items():
        if sym.startswith("_"):
            continue  # comment key
        if not isinstance(entry, dict):
            print(f"skipping malformed entry for {sym!r}", file=sys.stderr)
            continue

        existing = entry.get("epic")
        if existing and not args.force:
            print(f"{sym}: epic already populated ({existing}) — skip")
            skipped_existing += 1
            continue

        url = f"{args.api_base.rstrip('/')}/api/admin/ig/search"
        try:
            resp = requests.get(
                url, params={"term": sym}, headers=headers, timeout=10,
            )
        except Exception as exc:  # noqa: BLE001
            print(f"{sym}: search request failed: {exc}", file=sys.stderr)
            failed.append(sym)
            continue

        if resp.status_code != 200:
            print(
                f"{sym}: {resp.status_code} {resp.reason}: {resp.text[:200]}",
                file=sys.stderr,
            )
            failed.append(sym)
            continue

        payload = resp.json() or {}
        matches = payload.get("matches") or []
        if not matches:
            print(f"{sym}: no matches returned from IG")
            failed.append(sym)
            continue

        chosen = _pick_match(sym, matches, auto=args.auto)
        if chosen is None:
            print(f"{sym}: skipped (operator declined)")
            continue

        entry["epic"] = chosen.get("epic")
        entry["instrument_name"] = (
            chosen.get("instrumentName")
            or chosen.get("instrument_name")
            or entry.get("instrument_name")
        )
        notes_existing = entry.get("notes") or ""
        entry["notes"] = (
            (notes_existing + " ").lstrip()
            + "Populated via tradepro-ig-populate-epics."
        ).strip()
        updated += 1
        print(f"{sym}: → {entry['epic']!r} ({entry.get('instrument_name')})")

    # Atomic write — write to a tmp file then rename, so a crashed
    # process can't leave an empty / half-written JSON behind.
    tmp_path = map_path.with_suffix(map_path.suffix + ".tmp")
    with tmp_path.open("w", encoding="utf-8") as f:
        json.dump(body, f, indent=2)
        f.write("\n")
    tmp_path.replace(map_path)

    print()
    print(f"Updated: {updated}  Skipped existing: {skipped_existing}  Failed: {len(failed)}")
    if failed:
        print(f"  failures: {failed}")
    if updated == 0 and not failed:
        print("All entries already populated; nothing to do.")
    return 0 if not failed else 1


def _pick_match(
    sym: str,
    matches: list[dict[str, Any]],
    *,
    auto: bool,
) -> dict[str, Any] | None:
    """Prompt-or-auto-pick. In auto mode pick the first match whose
    `instrumentType` matches the equity family (SHARES / CFD on shares);
    otherwise prompt the operator interactively."""
    if auto:
        for m in matches:
            t = (m.get("instrumentType") or "").upper()
            if "SHARE" in t or "EQUIT" in t or t == "CFD":
                return m
        # Fall back to the first match if nothing equity-looking.
        return matches[0] if matches else None

    print(f"\n{sym}: {len(matches)} match{'es' if len(matches) != 1 else ''}:")
    for i, m in enumerate(matches[:10]):
        print(
            f"  [{i}] epic={m.get('epic')!r}  "
            f"name={m.get('instrumentName')!r}  "
            f"type={m.get('instrumentType')!r}  "
            f"status={m.get('marketStatus')!r}"
        )
    while True:
        choice = input("  pick [0-9 / s=skip / q=quit]: ").strip().lower()
        if choice == "q":
            print("aborted by operator", file=sys.stderr)
            sys.exit(130)
        if choice in ("s", ""):
            return None
        try:
            idx = int(choice)
            if 0 <= idx < len(matches[:10]):
                return matches[idx]
        except ValueError:
            pass
        print("  invalid; pick a number, 's' to skip, or 'q' to quit")


if __name__ == "__main__":  # pragma: no cover — entry point
    sys.exit(main())
